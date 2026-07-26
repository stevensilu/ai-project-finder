from __future__ import annotations

import json
import tempfile
import threading
import unittest
from http.client import HTTPConnection
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import app


class AutomaticPathsTest(unittest.TestCase):
    def test_default_config_contains_only_auto_sources(self) -> None:
        self.assertEqual(
            app.DEFAULT_CONFIG["sources"],
            {
                "codex": "auto",
                "claude": "auto",
                "kimi": "auto",
                "kimi-desktop": "auto",
            },
        )
        self.assertEqual(app.DEFAULT_CONFIG["locale"], "en")

    def test_locale_normalization_is_deterministic(self) -> None:
        self.assertEqual(app.normalize_locale("en"), "en")
        self.assertEqual(app.normalize_locale("EN-us"), "en")
        self.assertEqual(app.normalize_locale("zh"), "zh-CN")
        self.assertEqual(app.normalize_locale("zh_TW"), "zh-CN")

    def test_custom_paths_expand_home_and_environment(self) -> None:
        with patch.dict("os.environ", {"APF_FIXTURE_ROOT": "/tmp/apf-fixture"}):
            paths = app.expand_source_value(
                ["~/custom-sessions", "$APF_FIXTURE_ROOT/sessions"]
            )
        self.assertEqual(paths[0], Path.home() / "custom-sessions")
        self.assertEqual(paths[1], Path("/tmp/apf-fixture/sessions"))

    def test_windows_system_opener_uses_cmd_start(self) -> None:
        with patch.object(app, "IS_WINDOWS", True):
            command = app.system_open_command("codex://threads/demo-session")
        self.assertEqual(
            command,
            [
                "cmd.exe",
                "/d",
                "/s",
                "/c",
                "start",
                "",
                "codex://threads/demo-session",
            ],
        )

    def test_windows_kimi_desktop_runtime_discovery(self) -> None:
        with patch.object(app, "IS_WINDOWS", True), patch.dict(
            "os.environ",
            {
                "APPDATA": r"C:\Users\Example\AppData\Roaming",
                "LOCALAPPDATA": r"C:\Users\Example\AppData\Local",
            },
            clear=False,
        ):
            paths = app.automatic_source_paths()["kimi-desktop"]
        self.assertTrue(
            any(
                "AppData" in str(path)
                and "daimon-share" in str(path)
                and str(path).endswith("sessions")
                for path in paths
            )
        )

    def test_windows_claude_desktop_profile_discovery(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            appdata = Path(temporary) / "AppData" / "Roaming"
            session_root = appdata / "Claude" / "claude-code-sessions"
            session_root.mkdir(parents=True)
            with (
                patch.object(app, "IS_WINDOWS", True),
                patch.dict(
                    "os.environ",
                    {"APPDATA": str(appdata), "LOCALAPPDATA": ""},
                    clear=False,
                ),
            ):
                roots = app.claude_desktop_session_roots()
        self.assertEqual(roots, [("Claude", session_root)])

    def test_windows_kimi_launcher_is_cmd_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            data_dir = Path(temporary) / "data"
            binary = Path(temporary) / "kimi.exe"
            binary.touch()
            with (
                patch.object(app, "IS_WINDOWS", True),
                patch.object(app, "DATA_DIR", data_dir),
                patch.object(app, "kimi_binary", return_value=binary),
            ):
                launcher = app.kimi_cli_launcher("demo-session-001")
                contents = launcher.read_text(encoding="utf-8")
        self.assertEqual(launcher.suffix, ".cmd")
        self.assertIn('"demo-session-001"', contents)
        self.assertIn("kimi.exe", contents)


class KimiDesktopParserTest(unittest.TestCase):
    def test_parser_accepts_conversation_state_and_skips_helpers(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            conversation = root / "conv-public-fixture"
            conversation.mkdir()
            state_path = conversation / "state.json"
            state_path.write_text(
                json.dumps(
                    {
                        "id": "conv-public-fixture",
                        "title": "Public Kimi Work session",
                        "lastPrompt": "Find the launch files",
                        "cwd": str(root / "sample-project"),
                    }
                ),
                encoding="utf-8",
            )
            record = app.parse_kimi_desktop(state_path, 9000)
            self.assertIsNotNone(record)
            assert record is not None
            self.assertEqual(record["source"], "kimi-desktop")
            self.assertEqual(record["open_label"], "Open Kimi Desktop ↗")

            helper = root / "ctitle-conv-public-fixture"
            helper.mkdir()
            helper_state = helper / "state.json"
            helper_state.write_text("{}", encoding="utf-8")
            self.assertIsNone(app.parse_kimi_desktop(helper_state, 9000))


class PublicSourceScanTest(unittest.TestCase):
    def test_public_runtime_files_do_not_contain_original_username(self) -> None:
        root = Path(__file__).resolve().parents[1]
        runtime_files = [
            root / "app.py",
            root / "config.json",
            root / "start.command",
            root / "install.command",
            root / "start.bat",
            root / "install.bat",
            root / "static" / "index.html",
            root / "demo" / "fixtures.json",
        ]
        private_home_fixture = "/Users/" + "si" + "lu"
        for path in runtime_files:
            self.assertNotIn(private_home_fixture, path.read_text(encoding="utf-8"))


class DemoFixtureTest(unittest.TestCase):
    def test_demo_payload_is_balanced_and_contains_only_synthetic_paths(self) -> None:
        payload = app.load_demo_payload()
        self.assertTrue(payload["demo"])
        self.assertEqual(payload["summary"]["records"], 20)
        self.assertEqual(payload["summary"]["projects"], 6)
        self.assertEqual(
            payload["summary"]["sources"],
            {"claude": 5, "codex": 5, "kimi": 5, "kimi-desktop": 5},
        )
        self.assertTrue(
            all(
                str(record["cwd"]).startswith("~/Demo Workspaces/")
                and str(record["session_path"]).startswith("demo://sessions/")
                for record in payload["records"]
            )
        )
        fixture_text = app.DEMO_FIXTURE_FILE.read_text(encoding="utf-8").lower()
        for forbidden in ("/users/", "/home/", "@", "petlibro"):
            self.assertNotIn(forbidden, fixture_text)

    def test_demo_index_loader_never_builds_or_reads_the_real_index(self) -> None:
        with (
            patch.object(app, "DEMO_MODE", True),
            patch.object(app, "INDEX_FILE") as guarded_index,
            patch.object(app, "build_index", side_effect=AssertionError("real index built")),
        ):
            guarded_index.exists.side_effect = AssertionError("real index checked")
            payload = app.load_index_payload()
            guarded_index.exists.assert_not_called()
        self.assertTrue(payload["demo"])
        self.assertEqual(len(payload["records"]), 20)

    def test_chinese_demo_payload_uses_localized_project_content(self) -> None:
        with patch.object(app, "APP_LOCALE", "zh-CN"):
            payload = app.load_demo_payload()
        atlas = [
            record
            for record in payload["records"]
            if record["session_id"].startswith("demo-atlas-")
        ]
        self.assertEqual(len(atlas), 4)
        self.assertTrue(all(record["project"] == "阿特拉斯发布" for record in atlas))
        self.assertTrue(all(str(record["cwd"]).startswith("~/演示工作区/") for record in atlas))
        self.assertTrue(all("阿特拉斯" in record["search_text"] for record in atlas))

    def test_demo_main_skips_local_config_and_source_build(self) -> None:
        class FakeServer:
            def serve_forever(self) -> None:
                return

            def server_close(self) -> None:
                return

        fake_server = FakeServer()
        with (
            patch("sys.argv", ["app.py", "--demo"]),
            patch.object(app, "DEMO_MODE", False),
            patch.object(app, "APP_LOCALE", "en"),
            patch.object(app, "load_config", side_effect=AssertionError("local config read")),
            patch.object(app, "build_index", side_effect=AssertionError("real source scan")),
            patch.object(app, "ThreadingHTTPServer", return_value=fake_server) as server_factory,
            patch("builtins.print"),
        ):
            app.main()
        server_factory.assert_called_once_with(
            ("127.0.0.1", app.DEMO_DEFAULT_PORT),
            app.FinderHandler,
        )
        self.assertEqual(app.APP_LOCALE, "en")

    def test_demo_main_accepts_explicit_chinese_locale(self) -> None:
        class FakeServer:
            def serve_forever(self) -> None:
                return

            def server_close(self) -> None:
                return

        with (
            patch("sys.argv", ["app.py", "--demo", "--locale", "zh-CN"]),
            patch.object(app, "DEMO_MODE", False),
            patch.object(app, "APP_LOCALE", "en"),
            patch.object(app, "load_config", side_effect=AssertionError("local config read")),
            patch.object(app, "ThreadingHTTPServer", return_value=FakeServer()),
            patch("builtins.print"),
        ):
            app.main()
            self.assertEqual(app.APP_LOCALE, "zh-CN")


class ClaudeTranscriptParserTest(unittest.TestCase):
    def write_transcript(self, directory: Path, rows: list[dict]) -> Path:
        path = directory / "session.jsonl"
        path.write_text(
            "\n".join(json.dumps(row, ensure_ascii=False) for row in rows),
            encoding="utf-8",
        )
        return path

    def test_subagent_turns_stay_out_of_the_excerpt_and_input_count(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = self.write_transcript(
                root,
                [
                    {
                        "type": "user",
                        "sessionId": "sess-sidechain-001",
                        "cwd": str(root),
                        "timestamp": "2026-07-20T10:00:00Z",
                        "message": {"content": "Locate the launch plan spreadsheet"},
                    },
                    {
                        "type": "user",
                        "isSidechain": True,
                        "sessionId": "sess-sidechain-001",
                        "message": {"content": "SUBAGENT INSTRUCTION BLOCK"},
                    },
                    {
                        "type": "user",
                        "isSidechain": True,
                        "sessionId": "sess-sidechain-001",
                        "message": {"content": "TOOL RESULT PAYLOAD"},
                    },
                ],
            )
            record = app.parse_claude(path, 9000)
        assert record is not None
        self.assertEqual(record["message_count"], 1)
        self.assertNotIn("SUBAGENT INSTRUCTION BLOCK", record["excerpt"])
        self.assertNotIn("TOOL RESULT PAYLOAD", record["search_text"])

    def test_title_prefers_a_written_title_over_a_generated_one(self) -> None:
        base_rows = [
            {
                "type": "user",
                "sessionId": "sess-titles-001",
                "timestamp": "2026-07-20T10:00:00Z",
                "cwd": "/tmp/apf-title-fixture",
                "message": {"content": "first prompt that should lose to any title row"},
            },
            {"type": "ai-title", "sessionId": "sess-titles-001", "aiTitle": "Generated title"},
        ]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            generated = app.parse_claude(self.write_transcript(root, base_rows), 9000)
            renamed = app.parse_claude(
                self.write_transcript(
                    root,
                    [
                        *base_rows,
                        {
                            "type": "custom-title",
                            "sessionId": "sess-titles-001",
                            "customTitle": "Written title",
                        },
                        # Claude Code keeps refreshing its own title after a
                        # rename, so the later row must not win.
                        {
                            "type": "ai-title",
                            "sessionId": "sess-titles-001",
                            "aiTitle": "Generated title again",
                        },
                    ],
                ),
                9000,
            )
        assert generated is not None and renamed is not None
        self.assertEqual(generated["title"], "Generated title")
        self.assertEqual(generated["title_source"], "ai-title")
        self.assertEqual(renamed["title"], "Written title")
        self.assertEqual(renamed["title_source"], "custom-title")

    def test_a_user_set_desktop_title_outranks_a_generated_title(self) -> None:
        rows = [
            {
                "type": "user",
                "sessionId": "sess-desktop-001",
                "cwd": "/tmp/apf-title-fixture",
                "timestamp": "2026-07-20T10:00:00Z",
                "message": {"content": "first prompt"},
            },
            {"type": "ai-title", "sessionId": "sess-desktop-001", "aiTitle": "Generated title"},
        ]
        desktop_sessions = {
            "sess-desktop-001": {
                "desktop_session_id": "desktop-session-001",
                "desktop_title": "Desktop rename",
                "desktop_title_source": "user",
                "desktop_profile": "Claude",
            }
        }
        with tempfile.TemporaryDirectory() as temporary:
            record = app.parse_claude(
                self.write_transcript(Path(temporary), rows), 9000, desktop_sessions
            )
        assert record is not None
        self.assertEqual(record["title"], "Desktop rename")
        self.assertEqual(record["title_source"], "desktop-user")


class DemoServerTestCase(unittest.TestCase):
    """Serve the real handler over loopback with every real side effect blocked."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.demo_patch = patch.object(app, "DEMO_MODE", True)
        self.manual_patch = patch.object(
            app,
            "MANUAL_FILE",
            Path(self.temporary.name) / "manual.json",
        )
        self.build_patch = patch.object(
            app,
            "build_index",
            side_effect=AssertionError("demo mode scanned real sources"),
        )
        self.launch_patch = patch.object(
            app,
            "launch_record",
            side_effect=AssertionError("demo mode launched a real application"),
        )
        self.demo_patch.start()
        self.manual_patch.start()
        self.build_mock = self.build_patch.start()
        self.launch_mock = self.launch_patch.start()
        self.server = app.ThreadingHTTPServer(("127.0.0.1", 0), app.FinderHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        for active_patch in (
            self.launch_patch,
            self.build_patch,
            self.manual_patch,
            self.demo_patch,
        ):
            active_patch.stop()
        self.temporary.cleanup()

    def read_json(self, path: str, *, method: str = "GET", payload: dict | None = None) -> tuple[int, dict]:
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        request = Request(
            f"{self.base_url}{path}",
            data=body,
            method=method,
            headers={
                "Content-Type": "application/json",
                "X-APF-Token": app.SESSION_TOKEN,
            },
        )
        with urlopen(request, timeout=3) as response:
            return response.status, json.loads(response.read().decode("utf-8"))

    def raw_request(
        self,
        method: str,
        path: str,
        *,
        host: str | None = None,
        headers: dict[str, str] | None = None,
        body: bytes | None = None,
        with_token: bool = True,
    ) -> tuple[int, dict[str, str], bytes]:
        """Send a hand-built request so Host and fetch metadata can be forged."""
        connection = HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        try:
            connection.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
            connection.putheader("Host", host or f"127.0.0.1:{self.server.server_port}")
            if with_token:
                connection.putheader("X-APF-Token", app.SESSION_TOKEN)
            if body is not None:
                connection.putheader("Content-Length", str(len(body)))
            for name, value in (headers or {}).items():
                connection.putheader(name, value)
            connection.endheaders()
            if body is not None:
                connection.send(body)
            response = connection.getresponse()
            return (
                response.status,
                {name.lower(): value for name, value in response.getheaders()},
                response.read(),
            )
        finally:
            connection.close()

    def error_code(self, raw_body: bytes) -> str:
        return str(json.loads(raw_body.decode("utf-8")).get("error_code") or "")


class DemoHTTPIsolationTest(DemoServerTestCase):
    def test_demo_reindex_and_open_are_safe_no_ops(self) -> None:
        index_status, index_payload = self.read_json("/api/index")
        self.assertEqual(index_status, 200)
        self.assertTrue(index_payload["demo"])

        reindex_status, reindex_payload = self.read_json("/api/reindex", method="POST")
        self.assertEqual(reindex_status, 200)
        self.assertTrue(reindex_payload["demo"])

        open_status, open_payload = self.read_json(
            "/api/open",
            method="POST",
            payload={"record_id": "claude:demo-atlas-claude", "action": "session"},
        )
        self.assertEqual(open_status, 200)
        self.assertEqual(open_payload, {"ok": True, "demo": True, "mode": "claude"})
        self.build_mock.assert_not_called()
        self.launch_mock.assert_not_called()

    def test_demo_manual_endpoint_is_read_only(self) -> None:
        with self.assertRaises(HTTPError) as raised:
            self.read_json(
                "/api/manual",
                method="POST",
                payload={"title": "This must not be stored"},
            )
        self.assertEqual(raised.exception.code, 403)
        # The refusal must come from Demo Mode, not from a failed token check.
        self.assertEqual(self.error_code(raised.exception.read()), "demo_read_only")
        raised.exception.close()
        self.assertFalse(app.MANUAL_FILE.exists())


class LocalApiBoundaryTest(DemoServerTestCase):
    """A website must not be able to read the index or trigger an open action."""

    OPEN_BODY = json.dumps(
        {"record_id": "claude:demo-atlas-claude", "action": "session"}
    ).encode("utf-8")

    def test_a_forged_host_header_is_refused(self) -> None:
        # DNS rebinding keeps the attacker's hostname in the Host header.
        status, _, body = self.raw_request("GET", "/api/index", host="evil.example.com")
        self.assertEqual(status, 403)
        self.assertEqual(self.error_code(body), "invalid_host")

    def test_a_request_without_the_session_token_is_refused(self) -> None:
        status, _, body = self.raw_request("GET", "/api/index", with_token=False)
        self.assertEqual(status, 403)
        self.assertEqual(self.error_code(body), "unauthorized")

    def test_the_page_cookie_authorizes_the_api(self) -> None:
        status, _, _ = self.raw_request(
            "GET",
            "/api/health",
            with_token=False,
            headers={"Cookie": f"{app.SESSION_COOKIE_NAME}={app.SESSION_TOKEN}"},
        )
        self.assertEqual(status, 200)

    def test_a_cross_site_post_is_refused_even_with_a_valid_token(self) -> None:
        status, _, body = self.raw_request(
            "POST",
            "/api/open",
            headers={
                "Content-Type": "application/json",
                "Origin": "https://evil.example.com",
                "Sec-Fetch-Site": "cross-site",
            },
            body=self.OPEN_BODY,
        )
        self.assertEqual(status, 403)
        self.assertEqual(self.error_code(body), "cross_origin")

    def test_a_simple_post_body_that_skips_preflight_is_refused(self) -> None:
        status, _, body = self.raw_request(
            "POST",
            "/api/open",
            headers={"Content-Type": "text/plain"},
            body=self.OPEN_BODY,
        )
        self.assertEqual(status, 415)
        self.assertEqual(self.error_code(body), "unsupported_media_type")

    def test_a_preflight_never_receives_permission(self) -> None:
        status, headers, _ = self.raw_request("OPTIONS", "/api/open")
        self.assertEqual(status, 405)
        self.assertNotIn("access-control-allow-origin", headers)

    def test_a_page_load_issues_a_strict_session_cookie(self) -> None:
        status, headers, _ = self.raw_request("GET", "/?lang=en", with_token=False)
        self.assertEqual(status, 200)
        cookie = headers.get("set-cookie", "")
        self.assertIn(f"{app.SESSION_COOKIE_NAME}={app.SESSION_TOKEN}", cookie)
        self.assertIn("SameSite=Strict", cookie)
        self.assertIn("HttpOnly", cookie)
        self.assertEqual(headers.get("x-frame-options"), "DENY")
        self.assertIn("frame-ancestors 'none'", headers.get("content-security-policy", ""))

    def test_api_responses_do_not_hand_out_the_token(self) -> None:
        _, headers, _ = self.raw_request("GET", "/api/health")
        self.assertNotIn("set-cookie", headers)


if __name__ == "__main__":
    unittest.main()

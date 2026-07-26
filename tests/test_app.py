from __future__ import annotations

import json
import os
import re
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

    def test_windows_opens_through_the_shell_api_without_a_command_line(self) -> None:
        with (
            patch.object(app, "IS_WINDOWS", True),
            patch("os.startfile", create=True) as start_file,
            patch.object(app.subprocess, "run", side_effect=AssertionError("spawned a shell")),
        ):
            app.system_open_target("codex://threads/demo-session")
        start_file.assert_called_once_with("codex://threads/demo-session")

    def test_a_windows_path_with_a_shell_character_stays_one_value(self) -> None:
        # cmd.exe would have read everything after & as a second command.
        target = r"C:\Users\Example\R&D\launch"
        with (
            patch.object(app, "IS_WINDOWS", True),
            patch("os.startfile", create=True) as start_file,
        ):
            app.system_open_target(target)
        start_file.assert_called_once_with(target)

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


class VersionConsistencyTest(unittest.TestCase):
    """Keep one release number, so the Server header cannot drift from the README."""

    def readme_paths(self) -> list[Path]:
        root = Path(__file__).resolve().parents[1]
        return [root / "README.md", root / "README.zh-CN.md"]

    def test_the_server_header_states_the_release(self) -> None:
        self.assertEqual(app.FinderHandler.server_version, f"AIProjectFinder/{app.APP_VERSION}")

    def test_both_readmes_state_the_current_release(self) -> None:
        for path in self.readme_paths():
            text = path.read_text(encoding="utf-8")
            self.assertIn(f"v{app.APP_VERSION}", text.splitlines()[13], path.name)

    def test_download_links_point_at_the_current_release(self) -> None:
        for path in self.readme_paths():
            text = path.read_text(encoding="utf-8")
            links = re.findall(r"/releases/download/(v[\d.]+)/(\S+?\.zip)", text)
            self.assertTrue(links, f"{path.name} lists no download links")
            for tag, archive in links:
                self.assertEqual(tag, f"v{app.APP_VERSION}", path.name)
                self.assertIn(f"_v{app.APP_VERSION}.zip", archive, path.name)


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
        # The interface builds its own searchable text from these fields, so the
        # payload no longer carries a second lowercased copy of them.
        self.assertTrue(all("search_text" not in record for record in atlas))
        self.assertTrue(all("阿特拉斯" in record["cwd"] for record in atlas))

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
        self.assertNotIn("TOOL RESULT PAYLOAD", record["excerpt"])

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


class ProjectNamingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.rules = patch.object(
            app, "NAMING_RULES", {key: list(value) for key, value in app.DEFAULT_NAMING.items()}
        )
        self.rules.start()
        self.addCleanup(self.rules.stop)

    def test_a_projects_folder_names_the_project(self) -> None:
        self.assertEqual(
            app.canonical_project("/srv/work/projects/atlas-launch/docs", "")[0],
            "atlas-launch",
        )

    def test_a_client_marker_tolerates_a_numbered_prefix(self) -> None:
        project, customer = app.canonical_project(
            "/srv/work/1.1 客户/Northwind/Q3 Campaign", ""
        )
        self.assertEqual(customer, "Northwind")
        self.assertEqual(project, "Northwind · Q3 Campaign")

    def test_a_dated_workspace_skips_the_date_folder(self) -> None:
        self.assertEqual(
            app.canonical_project("/srv/Codex/2026-07-25/orchid-site", "")[0],
            "orchid-site",
        )

    def test_an_unnamed_path_does_not_invent_a_project_from_the_request(self) -> None:
        # Summarising the request here produced one throwaway project per
        # session, which is what buried the real ones.
        self.assertEqual(
            app.canonical_project(
                str(Path.home() / "Downloads"), "rewrite the launch brief for Northwind"
            )[0],
            "",
        )

    def test_an_unnamed_project_stays_empty_for_the_interface_to_label(self) -> None:
        # A Chinese placeholder here would reach the English edition unchanged.
        self.assertEqual(app.canonical_project(str(Path.home()), "")[0], "")

    def test_a_slugified_request_folder_is_not_treated_as_a_project(self) -> None:
        for folder in (
            "clone-https-github-com-someone-project",
            "let-s-set-up-a-scheduled",
            "https-x-com-someone",
        ):
            self.assertEqual(app.canonical_project(f"/srv/Codex/2026-07-25/{folder}", "")[0], "")

    def test_an_ordinary_hyphenated_folder_is_still_a_project(self) -> None:
        self.assertEqual(
            app.canonical_project("/srv/Codex/2026-07-25/ai-project-finder", "")[0],
            "ai-project-finder",
        )

    def test_markers_come_from_configuration(self) -> None:
        with patch.object(
            app,
            "NAMING_RULES",
            app.resolve_naming_rules({"naming": {"project_markers": ["arbeit"]}}),
        ):
            self.assertEqual(
                app.canonical_project("/srv/arbeit/atlas-launch", "")[0], "atlas-launch"
            )


class CodexTranscriptParserTest(unittest.TestCase):
    def write_transcript(self, path: Path, rows: list[dict]) -> Path:
        path.write_text(
            "\n".join(json.dumps(row, ensure_ascii=False) for row in rows),
            encoding="utf-8",
        )
        return path

    def test_a_user_thread_becomes_a_record(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = self.write_transcript(
                root / "rollout.jsonl",
                [
                    {
                        "type": "session_meta",
                        "timestamp": "2026-07-20T09:00:00Z",
                        "payload": {"id": "codex-session-001", "cwd": "/srv/projects/atlas"},
                    },
                    {
                        "type": "response_item",
                        "timestamp": "2026-07-20T09:01:00Z",
                        "payload": {
                            "role": "user",
                            "content": [{"type": "input_text", "text": "Draft the launch checklist"}],
                        },
                    },
                ],
            )
            record = app.parse_codex(path, 9000)
        assert record is not None
        self.assertEqual(record["source"], "codex")
        self.assertEqual(record["session_id"], "codex-session-001")
        self.assertEqual(record["message_count"], 1)
        self.assertIn("launch checklist", record["excerpt"])

    def test_a_subagent_thread_is_not_indexed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = self.write_transcript(
                Path(temporary) / "rollout.jsonl",
                [
                    {
                        "type": "session_meta",
                        "payload": {
                            "id": "codex-session-002",
                            "cwd": "/srv/projects/atlas",
                            "thread_source": "subagent",
                        },
                    },
                    {
                        "type": "response_item",
                        "payload": {"role": "user", "content": "internal fan-out prompt"},
                    },
                ],
            )
            self.assertIsNone(app.parse_codex(path, 9000))


class IndexBuildTest(unittest.TestCase):
    """Exercise the whole build: caching, deduplication, and manual traces."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.data_dir = root / "data"
        self.sessions = root / "codex-sessions"
        self.sessions.mkdir(parents=True)
        self.data_dir.mkdir(parents=True)
        for attribute, value in (
            ("DATA_DIR", self.data_dir),
            ("INDEX_FILE", self.data_dir / "index.json"),
            ("PARSE_CACHE_FILE", self.data_dir / "parse-cache.json"),
            ("MANUAL_FILE", self.data_dir / "manual.json"),
            ("DEMO_MODE", False),
        ):
            patcher = patch.object(app, attribute, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        config = {
            "port": 4388,
            "max_prompt_chars": 9000,
            "locale": "en",
            "sources": {
                "codex": str(self.sessions),
                "claude": str(root / "absent"),
                "kimi": str(root / "absent"),
                "kimi-desktop": str(root / "absent"),
            },
        }
        config_patch = patch.object(app, "load_config", return_value=config)
        config_patch.start()
        self.addCleanup(config_patch.stop)
        index_cache = patch.object(app, "INDEX_CACHE", {"key": None, "payload": None})
        index_cache.start()
        self.addCleanup(index_cache.stop)

    def write_session(self, name: str, session_id: str, prompt: str, cwd: str) -> Path:
        path = self.sessions / name
        path.write_text(
            "\n".join(
                json.dumps(row)
                for row in (
                    {
                        "type": "session_meta",
                        "timestamp": "2026-07-20T09:00:00Z",
                        "payload": {"id": session_id, "cwd": cwd},
                    },
                    {
                        "type": "response_item",
                        "timestamp": "2026-07-20T09:01:00Z",
                        "payload": {"role": "user", "content": prompt},
                    },
                )
            ),
            encoding="utf-8",
        )
        return path

    def test_an_unchanged_transcript_is_not_parsed_again(self) -> None:
        self.write_session("one.jsonl", "codex-cache-001", "first request", "/srv/projects/atlas")
        first = app.build_index()
        self.assertEqual(first["reused_records"], 0)
        second = app.build_index()
        self.assertEqual(second["reused_records"], 1)
        self.assertEqual(second["summary"]["records"], 1)

    def test_a_changed_transcript_is_parsed_again(self) -> None:
        path = self.write_session("one.jsonl", "codex-cache-002", "first request", "/srv/projects/atlas")
        app.build_index()
        os.utime(path, (1, 1))
        path.write_text(
            path.read_text(encoding="utf-8").replace("first request", "second request"),
            encoding="utf-8",
        )
        rebuilt = app.build_index()
        self.assertEqual(rebuilt["reused_records"], 0)
        self.assertIn("second request", rebuilt["records"][0]["excerpt"])

    def test_a_parser_change_discards_the_cache(self) -> None:
        self.write_session("one.jsonl", "codex-cache-003", "first request", "/srv/projects/atlas")
        app.build_index()
        with patch.object(app, "PARSE_CACHE_VERSION", app.PARSE_CACHE_VERSION + 1):
            rebuilt = app.build_index()
        self.assertEqual(rebuilt["reused_records"], 0)

    def test_the_richer_copy_of_a_duplicated_session_wins(self) -> None:
        self.write_session("short.jsonl", "codex-duplicate", "only line", "/srv/projects/atlas")
        longer = self.sessions / "long.jsonl"
        longer.write_text(
            "\n".join(
                json.dumps(row)
                for row in (
                    {
                        "type": "session_meta",
                        "timestamp": "2026-07-20T09:00:00Z",
                        "payload": {"id": "codex-duplicate", "cwd": "/srv/projects/atlas"},
                    },
                    {"type": "response_item", "payload": {"role": "user", "content": "first line"}},
                    {"type": "response_item", "payload": {"role": "user", "content": "second line"}},
                )
            ),
            encoding="utf-8",
        )
        payload = app.build_index()
        self.assertEqual(payload["summary"]["records"], 1)
        self.assertEqual(payload["records"][0]["message_count"], 2)

    def test_a_manual_trace_is_marked_for_editing(self) -> None:
        app.MANUAL_FILE.write_text(
            json.dumps(
                [
                    {
                        "id": "manual-001",
                        "source": "ChatGPT",
                        "project": "Atlas Launch",
                        "title": "Browser conversation",
                        "location": "https://example.com/thread",
                        "notes": "pricing questions",
                        "updated_at": "2026-07-20T09:00:00Z",
                    }
                ]
            ),
            encoding="utf-8",
        )
        payload = app.build_index()
        manual = [record for record in payload["records"] if record.get("manual")]
        self.assertEqual(len(manual), 1)
        self.assertEqual(manual[0]["manual_id"], "manual-001")
        self.assertEqual(manual[0]["notes"], "pricing questions")
        # The origin label is chosen by the interface, in the reader's language.
        self.assertEqual(manual[0]["origin"], "")

    def test_the_served_index_drops_the_duplicated_search_field(self) -> None:
        self.write_session("one.jsonl", "codex-fields-001", "first request", "/srv/projects/atlas")
        payload = app.build_index()
        self.assertNotIn("search_text", payload["records"][0])

    def test_a_second_read_comes_from_memory(self) -> None:
        self.write_session("one.jsonl", "codex-memory-001", "first request", "/srv/projects/atlas")
        app.build_index()
        with patch.object(app, "build_index", side_effect=AssertionError("rebuilt")):
            first = app.load_index_payload()
            with patch.object(
                Path, "read_text", side_effect=AssertionError("re-read from disk")
            ):
                second = app.load_index_payload()
        self.assertIs(first, second)


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


class ProjectAssignmentTest(unittest.TestCase):
    """An assignment is how work gets grouped when the folder layout cannot say."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.sessions = root / "sessions"
        self.sessions.mkdir(parents=True)
        for attribute, value in (
            ("DATA_DIR", root),
            ("INDEX_FILE", root / "index.json"),
            ("PARSE_CACHE_FILE", root / "parse-cache.json"),
            ("PROJECTS_FILE", root / "projects.json"),
            ("MANUAL_FILE", root / "manual.json"),
            ("DEMO_MODE", False),
            ("INDEX_CACHE", {"key": None, "payload": None}),
        ):
            patcher = patch.object(app, attribute, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        config = {
            "max_prompt_chars": 9000,
            "sources": {
                "codex": str(self.sessions),
                "claude": str(root / "absent"),
                "kimi": str(root / "absent"),
                "kimi-desktop": str(root / "absent"),
            },
        }
        patcher = patch.object(app, "load_config", return_value=config)
        patcher.start()
        self.addCleanup(patcher.stop)

    def write_session(self, name: str, session_id: str, cwd: str) -> None:
        (self.sessions / name).write_text(
            "\n".join(
                json.dumps(row)
                for row in (
                    {
                        "type": "session_meta",
                        "timestamp": "2026-07-20T09:00:00Z",
                        "payload": {"id": session_id, "cwd": cwd},
                    },
                    {"type": "response_item", "payload": {"role": "user", "content": "a request"}},
                )
            ),
            encoding="utf-8",
        )

    def records_by_id(self) -> dict[str, dict]:
        return {record["id"]: record for record in app.build_index()["records"]}

    def test_an_assignment_survives_a_rebuild_and_the_parse_cache(self) -> None:
        home = str(Path.home())
        self.write_session("one.jsonl", "codex-assign-001", home)
        self.assertEqual(self.records_by_id()["codex:codex-assign-001"]["project"], "")
        app.save_project_overrides(
            {"by_workspace": {}, "by_record": {"codex:codex-assign-001": "Atlas Launch"}}
        )
        rebuilt = app.build_index()
        self.assertEqual(rebuilt["records"][0]["project"], "Atlas Launch")
        self.assertEqual(rebuilt["records"][0]["project_source"], "assigned")
        # The cached record still carries the derived label, so clearing works.
        self.assertEqual(rebuilt["records"][0]["derived_project"], "")
        again = app.build_index()
        self.assertEqual(again["reused_records"], 1)
        self.assertEqual(again["records"][0]["project"], "Atlas Launch")

    def test_clearing_an_assignment_restores_the_derived_label(self) -> None:
        self.write_session("one.jsonl", "codex-assign-002", "/srv/projects/orchid")
        app.save_project_overrides(
            {"by_workspace": {}, "by_record": {"codex:codex-assign-002": "Renamed"}}
        )
        self.assertEqual(self.records_by_id()["codex:codex-assign-002"]["project"], "Renamed")
        app.save_project_overrides({"by_workspace": {}, "by_record": {}})
        self.assertEqual(self.records_by_id()["codex:codex-assign-002"]["project"], "orchid")

    def test_a_workspace_assignment_covers_later_sessions_in_that_folder(self) -> None:
        home = str(Path.home())
        self.write_session("one.jsonl", "codex-assign-003", home)
        app.save_project_overrides(
            {"by_workspace": {app.workspace_key(home): "Home Notes"}, "by_record": {}}
        )
        self.assertEqual(self.records_by_id()["codex:codex-assign-003"]["project"], "Home Notes")
        self.write_session("two.jsonl", "codex-assign-004", home)
        records = self.records_by_id()
        self.assertEqual(records["codex:codex-assign-004"]["project"], "Home Notes")

    def test_a_record_assignment_outranks_its_workspace(self) -> None:
        home = str(Path.home())
        self.write_session("one.jsonl", "codex-assign-005", home)
        app.save_project_overrides(
            {
                "by_workspace": {app.workspace_key(home): "Home Notes"},
                "by_record": {"codex:codex-assign-005": "Atlas Launch"},
            }
        )
        self.assertEqual(self.records_by_id()["codex:codex-assign-005"]["project"], "Atlas Launch")

    def test_a_workspace_key_ignores_a_trailing_separator(self) -> None:
        self.assertEqual(app.workspace_key("/srv/work/atlas/"), app.workspace_key("/srv/work/atlas"))


class ManualTraceEndpointTest(unittest.TestCase):
    """A trace added by hand must also be editable and removable."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        for attribute, value in (
            ("DATA_DIR", root),
            ("INDEX_FILE", root / "index.json"),
            ("PARSE_CACHE_FILE", root / "parse-cache.json"),
            ("MANUAL_FILE", root / "manual.json"),
            ("PROJECTS_FILE", root / "projects.json"),
            ("DEMO_MODE", False),
        ):
            patcher = patch.object(app, attribute, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        # Naming every source keeps the build inside this temporary directory.
        # An empty mapping falls through to automatic discovery, which reads the
        # real local history: slow, and not this test's business.
        absent_sources = {
            name: str(root / "absent") for name in ("codex", "claude", "kimi", "kimi-desktop")
        }
        for attribute, value in (
            ("load_config", {"sources": absent_sources, "max_prompt_chars": 9000}),
            ("INDEX_CACHE", {"key": None, "payload": None}),
        ):
            patcher = (
                patch.object(app, attribute, return_value=value)
                if attribute == "load_config"
                else patch.object(app, attribute, value)
            )
            patcher.start()
            self.addCleanup(patcher.stop)
        self.server = app.ThreadingHTTPServer(("127.0.0.1", 0), app.FinderHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.thread.join, 2)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

    def post(self, path: str, payload: dict) -> tuple[int, dict]:
        request = Request(
            f"{self.base_url}{path}",
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers={
                "Content-Type": "application/json",
                "X-APF-Token": app.SESSION_TOKEN,
            },
        )
        try:
            with urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            body = json.loads(error.read().decode("utf-8"))
            error.close()
            return error.code, body

    def stored_traces(self) -> list[dict]:
        return json.loads(app.MANUAL_FILE.read_text(encoding="utf-8"))

    def test_a_trace_can_be_added_edited_and_removed(self) -> None:
        created, _ = self.post(
            "/api/manual",
            {"source": "ChatGPT", "title": "Pricing thread", "notes": "kept in the browser"},
        )
        self.assertEqual(created, 201)
        traces = self.stored_traces()
        self.assertEqual(len(traces), 1)
        trace_id = traces[0]["id"]

        updated, _ = self.post(
            "/api/manual/update",
            {"id": trace_id, "title": "Pricing thread, revised", "notes": "kept in the browser"},
        )
        self.assertEqual(updated, 200)
        self.assertEqual(self.stored_traces()[0]["title"], "Pricing thread, revised")

        removed, _ = self.post("/api/manual/delete", {"id": trace_id})
        self.assertEqual(removed, 200)
        self.assertEqual(self.stored_traces(), [])

    def test_editing_an_unknown_trace_reports_a_missing_record(self) -> None:
        status, body = self.post(
            "/api/manual/update", {"id": "no-such-trace", "title": "Anything"}
        )
        self.assertEqual(status, 404)
        self.assertEqual(body["error_code"], "trace_missing")

    def test_a_trace_still_needs_a_title(self) -> None:
        status, _ = self.post("/api/manual", {"source": "ChatGPT", "title": "  "})
        self.assertEqual(status, 400)

    def test_a_session_can_be_assigned_to_a_project_over_http(self) -> None:
        created, _ = self.post("/api/manual", {"source": "ChatGPT", "title": "Pricing thread"})
        self.assertEqual(created, 201)
        record_id = [
            record["id"]
            for record in app.load_index_payload()["records"]
            if record.get("manual")
        ][0]

        assigned, body = self.post(
            "/api/project/assign", {"record_id": record_id, "project": "Atlas Launch"}
        )
        self.assertEqual(assigned, 200)
        self.assertEqual(body["project"], "Atlas Launch")
        self.assertEqual(
            app.load_project_overrides()["by_record"][record_id], "Atlas Launch"
        )

        renamed, rename_body = self.post(
            "/api/project/rename", {"from": "Atlas Launch", "to": "Orchid Launch"}
        )
        self.assertEqual(renamed, 200)
        self.assertEqual(rename_body["records"], 1)

        cleared, _ = self.post("/api/project/assign", {"record_id": record_id, "project": ""})
        self.assertEqual(cleared, 200)
        self.assertNotIn(record_id, app.load_project_overrides()["by_record"])

    def test_assigning_an_unknown_record_is_refused(self) -> None:
        status, body = self.post(
            "/api/project/assign", {"record_id": "codex:missing", "project": "Atlas"}
        )
        self.assertEqual(status, 404)
        self.assertEqual(body["error_code"], "record_missing")

    def test_renaming_a_project_that_does_not_exist_is_refused(self) -> None:
        status, body = self.post("/api/project/rename", {"from": "Nowhere", "to": "Somewhere"})
        self.assertEqual(status, 404)
        self.assertEqual(body["error_code"], "project_missing")

    def test_removing_an_unknown_trace_leaves_the_file_alone(self) -> None:
        self.post("/api/manual", {"source": "ChatGPT", "title": "Keep me"})
        status, body = self.post("/api/manual/delete", {"id": "no-such-trace"})
        self.assertEqual(status, 404)
        self.assertEqual(body["error_code"], "trace_missing")
        self.assertEqual(len(self.stored_traces()), 1)


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

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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

    def test_custom_paths_expand_home_and_environment(self) -> None:
        with patch.dict("os.environ", {"APF_FIXTURE_ROOT": "/tmp/apf-fixture"}):
            paths = app.expand_source_value(
                ["~/custom-sessions", "$APF_FIXTURE_ROOT/sessions"]
            )
        self.assertEqual(paths[0], Path.home() / "custom-sessions")
        self.assertEqual(paths[1], Path("/tmp/apf-fixture/sessions"))


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
            root / "static" / "index.html",
        ]
        private_home_fixture = "/Users/" + "si" + "lu"
        for path in runtime_files:
            self.assertNotIn(private_home_fixture, path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()

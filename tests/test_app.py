"""Unit/integration tests for app-level behaviour and recent bug fixes.

Covers:
  - _load_stream_thoughts tolerating a malformed wizard.yaml stream section.
  - main() actually building an orchestration in a fresh (codeless) workspace,
    where the build options previously dead-ended at the farewell message.

The AI is stubbed via WHITE_WIZARD_MOCK_REPLY so these run offline.
"""
import contextlib
import glob
import io
import os
import shutil
import tempfile
import unittest
from unittest import mock

from white_wizard import app
from white_wizard import db as wizard_db

from tests.test_integration import CANNED_PLAN, ScriptedMenu


class LoadStreamThoughtsTest(unittest.TestCase):
    def test_valid_thoughts_are_returned(self):
        thoughts = [{"priority": 1, "id": "a", "prompt": "do a"}]
        self.assertEqual(app._load_stream_thoughts({"stream": {"thoughts": thoughts}}), thoughts)

    def test_legacy_questions_key_is_accepted(self):
        q = [{"priority": 2, "id": "b", "prompt": "do b"}]
        self.assertEqual(app._load_stream_thoughts({"stream": {"questions": q}}), q)

    def test_missing_stream_falls_back_to_defaults(self):
        self.assertEqual(app._load_stream_thoughts({}), list(app.DEFAULT_STREAM_THOUGHTS))

    def test_malformed_stream_section_does_not_crash(self):
        # Regression: a non-dict stream or non-list thoughts used to raise.
        for data in ({"stream": 5}, {"stream": {"thoughts": 5}}, {"stream": []}, []):
            with self.subTest(data=data):
                self.assertEqual(
                    app._load_stream_thoughts(data), list(app.DEFAULT_STREAM_THOUGHTS)
                )

    def test_invalid_items_are_filtered_then_defaulted(self):
        bad = {"stream": {"thoughts": [{"id": "x"}, "nope", 3]}}
        self.assertEqual(app._load_stream_thoughts(bad), list(app.DEFAULT_STREAM_THOUGHTS))


class FreshWorkspaceBuildTest(unittest.TestCase):
    """main() must build an orchestration in an empty workspace, not just exit."""

    def setUp(self):
        self.origin = os.getcwd()
        self.tmp = tempfile.mkdtemp(prefix="ww_empty_")
        os.chdir(self.tmp)  # genuinely empty: no code files, no wizard.yaml

        self.menu = ScriptedMenu()  # picks the first OPTIONS entry; quits mgmt menu
        self._patches = [
            mock.patch.dict(os.environ, {"WHITE_WIZARD_MOCK_REPLY": CANNED_PLAN}),
            mock.patch.object(app, "menu", self.menu),
            mock.patch.object(app, "read_key", lambda: "enter"),
            mock.patch.object(app, "loading", lambda *a, **k: None),
            mock.patch.object(app.time, "sleep", lambda *a, **k: None),
        ]
        for p in self._patches:
            p.start()
        wizard_db.init_db()

    def tearDown(self):
        for p in reversed(self._patches):
            p.stop()
        os.chdir(self.origin)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_build_option_creates_orchestration(self):
        with contextlib.redirect_stdout(io.StringIO()):
            app.main()

        team_dirs = glob.glob(os.path.join(self.tmp, ".claude", "agents", "*_team"))
        self.assertTrue(team_dirs, "no team folder created from the fresh-workspace menu")
        self.assertTrue(
            os.path.isfile(os.path.join(self.tmp, ".claude", "wizard-orch.json")),
            "manifest not created",
        )
        md_files = [f for f in os.listdir(team_dirs[0]) if f.endswith(".md")]
        self.assertTrue(any("orchestrator" in f for f in md_files), "no orchestrator subagent")


if __name__ == "__main__":
    unittest.main()

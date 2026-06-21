"""Integration test for the orchestration lifecycle.

Runs White Wizard inside a throwaway copy of tests/fixtures/basic_html_js,
creates a dev team orchestration, asserts the Claude-native files were written,
then wipes them via the wipe command. The AI is stubbed with a canned plan
(WHITE_WIZARD_MOCK_REPLY) so the test is deterministic and offline. The
interactive menus are driven by a scripted stand-in. Teardown removes every
file the test generated regardless of outcome.

Run with:  python3 -m unittest tests.test_integration -v
"""
import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

from white_wizard import app, ui
from white_wizard import db as wizard_db

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "basic_html_js")

CANNED_PLAN = """\
Here is the dev team plan.

```json
{
  "agents": [
    {"name": "planner", "description": "Plans the work. Use first.",
     "tools": ["Read", "Grep"],
     "prompt": "You are the planner.\\n\\nWhen invoked:\\n1. Break the request into steps.\\n\\nReport:\\n- A short step list."},
    {"name": "implementer", "description": "Writes code. Use after planning.",
     "tools": ["Read", "Write", "Bash"],
     "prompt": "You are the implementer."},
    {"name": "reviewer", "description": "Reviews code. Use proactively after code is written.",
     "prompt": "You are the reviewer."}
  ],
  "transitions": [
    {"from": "planner", "to": "implementer", "condition": "always"},
    {"from": "implementer", "to": "reviewer", "condition": "pass"},
    {"from": "reviewer", "to": "planner", "condition": "fail"},
    {"from": "reviewer", "to": "done", "condition": "pass"}
  ]
}
```
"""


class ScriptedMenu:
    """Stand-in for ui.menu that picks deterministic answers per screen."""

    def __init__(self):
        self.wipe = False  # when True, the management menu chooses "wipe"

    def __call__(self, options, *args, **kwargs):
        values = [o.value for o in options]
        if True in values and False in values:      # yes/no confirmation
            return (True, "")
        if "yes" in values and "detail" in values:  # plan approval
            return ("yes", "")
        if "wipe" in values:                         # management menu
            return ("wipe", "") if self.wipe else (None, "")
        for o in options:                            # team picker
            if o.value == "Create dev team":
                return ("Create dev team", "")
        return (values[0], "")


class OrchestrationLifecycleTest(unittest.TestCase):
    def setUp(self):
        self.origin = os.getcwd()
        self.tmp = tempfile.mkdtemp(prefix="ww_itest_")
        self.repo = os.path.join(self.tmp, "basic_html_js")
        shutil.copytree(FIXTURE, self.repo)
        os.chdir(self.repo)

        self.menu = ScriptedMenu()
        self._patches = [
            mock.patch.dict(os.environ, {"WHITE_WIZARD_MOCK_REPLY": CANNED_PLAN}),
            mock.patch.object(app, "menu", self.menu),
            mock.patch.object(app, "read_key", lambda: "enter"),
            mock.patch.object(app, "loading", lambda *a, **k: None),
            mock.patch.object(app, "_ensure_mcp", lambda *a, **k: ("ok", "")),
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

    def _run(self, fn, *args):
        with contextlib.redirect_stdout(io.StringIO()):
            return fn(*args)

    def test_create_then_wipe(self):
        team_dir = os.path.join(self.repo, ".claude", "agents", "dev_team")
        manifest = os.path.join(self.repo, ".claude", "wizard-orch.json")
        mcp_json = os.path.join(self.repo, ".mcp.json")

        # 1. Create the orchestration (management menu quits without wiping).
        self.menu.wipe = False
        self._run(app.main)

        # 2. Wizard wrote the Claude-native orchestration.
        self.assertTrue(os.path.isdir(team_dir), "team folder not created")
        self.assertTrue(os.path.isfile(manifest), "manifest not created")
        agent_files = [f for f in os.listdir(team_dir) if f.endswith(".md")]
        self.assertIn("dev-orchestrator.md", agent_files)
        for specialist in ("planner.md", "implementer.md", "reviewer.md"):
            self.assertIn(specialist, agent_files)
        # The state-machine MCP tool was generated and registered.
        self.assertTrue(os.path.isfile(os.path.join(team_dir, "state_machine.py")))
        self.assertTrue(os.path.isfile(mcp_json), ".mcp.json not created")
        with open(mcp_json) as f:
            self.assertIn("dev-state-machine", json.load(f)["mcpServers"])
        # wizard.yaml holds only the wizard's own config — no orchestration data.
        self.assertTrue(os.path.isfile(os.path.join(self.repo, "wizard.yaml")))

        # 3. Wipe via the wipe command.
        self.menu.wipe = True
        self._run(app.handle_wizard_yaml)

        # 4. The orchestration — and its MCP registration — is gone.
        self.assertFalse(os.path.exists(team_dir), "team folder not wiped")
        self.assertFalse(os.path.exists(manifest), "manifest not wiped")
        self.assertFalse(os.path.exists(mcp_json), ".mcp.json not removed")


if __name__ == "__main__":
    unittest.main()

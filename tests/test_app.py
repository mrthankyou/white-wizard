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
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

from white_wizard import app
from white_wizard import db as wizard_db
from white_wizard import ui

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


class ParseAgentPlanTest(unittest.TestCase):
    def test_plain_fenced_block(self):
        resp = '```json\n{"agents": [{"name": "a"}], "transitions": []}\n```'
        plan = app.parse_agent_plan(resp)
        self.assertEqual([a["name"] for a in plan["agents"]], ["a"])

    def test_nested_code_fences_in_prompt(self):
        # Regression: agent prompts contain ``` fences; a non-greedy match used
        # to truncate the JSON at the first inner fence, so a valid plan parsed
        # as None ("could not produce an orchestration plan").
        resp = (
            "Here is the plan.\n\n"
            "```json\n"
            "{\n"
            '  "agents": [\n'
            '    {"name": "planner", "description": "Plans.",\n'
            '     "prompt": "Do work.\\n\\nOutput:\\n```\\nPLAN\\n1. step\\n```\\nDone."}\n'
            "  ],\n"
            '  "transitions": [{"from": "planner", "to": "done", "condition": "pass"}]\n'
            "}\n"
            "```\n"
        )
        plan = app.parse_agent_plan(resp)
        self.assertIsNotNone(plan, "nested-fence plan failed to parse")
        self.assertEqual(plan["agents"][0]["name"], "planner")
        self.assertIn("```", plan["agents"][0]["prompt"])

    def test_raw_decode_fallback_with_surrounding_prose(self):
        resp = 'Sure!\n{"agents": [{"name": "solo"}], "transitions": []}\nHope that helps.'
        plan = app.parse_agent_plan(resp)
        self.assertEqual(plan["agents"][0]["name"], "solo")

    def test_prose_only_returns_none(self):
        self.assertIsNone(app.parse_agent_plan("No JSON here at all."))

    def test_bare_array_is_not_a_plan(self):
        self.assertIsNone(app.parse_agent_plan("```json\n[1, 2, 3]\n```"))

    def test_agents_without_name_rejected(self):
        self.assertIsNone(app.parse_agent_plan('{"agents": [{"description": "x"}]}'))


class EnsureMcpTest(unittest.TestCase):
    def test_already_installed_skips_pip(self):
        with mock.patch("importlib.util.find_spec", return_value=object()), \
             mock.patch("white_wizard.app.subprocess.run") as run:
            self.assertEqual(app._ensure_mcp(), ("ok", ""))
        run.assert_not_called()

    def test_installs_when_missing(self):
        proc = mock.Mock(returncode=0, stdout="", stderr="")
        with mock.patch("importlib.util.find_spec", return_value=None), \
             mock.patch("white_wizard.app.subprocess.run", return_value=proc) as run:
            self.assertEqual(app._ensure_mcp(), ("installed", ""))
        cmd = run.call_args.args[0]
        self.assertEqual(cmd[1:], ["-m", "pip", "install", "mcp"])

    def test_install_failure_returns_last_line(self):
        proc = mock.Mock(returncode=1, stdout="", stderr="some noise\nERROR: nope\n")
        with mock.patch("importlib.util.find_spec", return_value=None), \
             mock.patch("white_wizard.app.subprocess.run", return_value=proc):
            status, detail = app._ensure_mcp()
        self.assertEqual(status, "fail")
        self.assertEqual(detail, "ERROR: nope")


class ReadKeyNonTtyTest(unittest.TestCase):
    def test_non_tty_returns_enter_without_crashing(self):
        # Regression: read_key() used to raise termios.error on piped/non-tty stdin.
        with mock.patch.object(ui.sys, "stdin", io.StringIO("x")):
            self.assertEqual(ui.read_key(), "enter")

    def test_non_tty_eof_returns_esc(self):
        with mock.patch.object(ui.sys, "stdin", io.StringIO("")):
            self.assertEqual(ui.read_key(), "esc")


class MultiOrchestrationStoreTest(unittest.TestCase):
    def setUp(self):
        self.origin = os.getcwd()
        self.tmp = tempfile.mkdtemp(prefix="ww_orch_")
        os.chdir(self.tmp)
        os.makedirs(os.path.join(self.tmp, ".claude", "agents"))

    def tearDown(self):
        os.chdir(self.origin)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _orch(self, folder, agents=("a", "b")):
        return {
            "created_at": "2026-06-21", "team": folder.replace("_team", "").title(),
            "team_folder": folder, "model": "m", "orchestrator": folder + "-orch",
            "agents": [{"name": n, "description": ""} for n in agents],
            "transitions": [], "mcp_tools": [], "mcp_servers": [],
        }

    def _make_folder(self, folder):
        os.makedirs(os.path.join(self.tmp, ".claude", "agents", folder), exist_ok=True)

    def test_save_list_and_active_is_latest(self):
        app._save_orchestration(self._orch("dev_team"))
        app._save_orchestration(self._orch("sec_team"))
        self.assertEqual([o["team_folder"] for o in app.list_orchestrations()],
                         ["dev_team", "sec_team"])
        self.assertEqual(app.load_orchestration()["team_folder"], "sec_team")

    def test_set_active(self):
        app._save_orchestration(self._orch("dev_team"))
        app._save_orchestration(self._orch("sec_team"))
        app.set_active_orchestration("dev_team")
        self.assertEqual(app.load_orchestration()["team_folder"], "dev_team")

    def test_save_replaces_same_folder(self):
        app._save_orchestration(self._orch("dev_team", agents=("a",)))
        app._save_orchestration(self._orch("dev_team", agents=("x", "y")))
        orchs = app.list_orchestrations()
        self.assertEqual(len(orchs), 1)
        self.assertEqual([a["name"] for a in orchs[0]["agents"]], ["x", "y"])

    def test_legacy_single_manifest_is_read(self):
        with open(os.path.join(self.tmp, ".claude", "wizard-orch.json"), "w") as f:
            json.dump(self._orch("dev_team"), f)
        self.assertEqual(len(app.list_orchestrations()), 1)
        self.assertEqual(app.load_orchestration()["team_folder"], "dev_team")

    def test_wipe_active_keeps_others(self):
        app._save_orchestration(self._orch("dev_team"))
        app._save_orchestration(self._orch("sec_team"))
        self._make_folder("dev_team")
        self._make_folder("sec_team")
        app.set_active_orchestration("dev_team")
        app.wipe_orchestration()  # wipes the active one (dev_team)
        self.assertEqual([o["team_folder"] for o in app.list_orchestrations()], ["sec_team"])
        self.assertFalse(os.path.exists(os.path.join(self.tmp, ".claude", "agents", "dev_team")))

    def test_wipe_last_removes_manifest(self):
        app._save_orchestration(self._orch("dev_team"))
        self._make_folder("dev_team")
        app.wipe_orchestration()
        self.assertEqual(app.list_orchestrations(), [])
        self.assertFalse(os.path.isfile(os.path.join(self.tmp, ".claude", "wizard-orch.json")))

    def test_wipe_with_blank_team_folder_does_not_nuke_agents_dir(self):
        # Regression: a blank team_folder must not resolve to the whole agents dir.
        agents = os.path.join(self.tmp, ".claude", "agents")
        self._make_folder("real_team")
        manifest = {
            "active": "",
            "orchestrations": [
                {"team_folder": "", "agents": [{"name": "a"}], "mcp_tools": [], "mcp_servers": []},
                {"team_folder": "real_team", "agents": [{"name": "b"}],
                 "mcp_tools": [], "mcp_servers": []},
            ],
        }
        with open(os.path.join(self.tmp, ".claude", "wizard-orch.json"), "w") as f:
            json.dump(manifest, f)

        app.wipe_orchestration()  # active is the blank-folder orch

        self.assertTrue(os.path.isdir(agents), "agents dir must survive")
        self.assertTrue(os.path.isdir(os.path.join(agents, "real_team")),
                        "sibling team must survive")
        self.assertEqual([o["team_folder"] for o in app.list_orchestrations()], ["real_team"])

    def test_ordered_agent_names_follow_transitions(self):
        orch = {
            "agents": [{"name": "planner"}, {"name": "impl"}, {"name": "reviewer"}],
            "transitions": [
                {"from": "impl", "to": "reviewer", "condition": "pass"},
                {"from": "planner", "to": "impl", "condition": "always"},
            ],
        }
        self.assertEqual(app._ordered_agent_names(orch), ["planner", "impl", "reviewer"])


class SwitchOrchestrationTest(unittest.TestCase):
    def setUp(self):
        self.origin = os.getcwd()
        self.tmp = tempfile.mkdtemp(prefix="ww_switch_")
        os.chdir(self.tmp)
        os.makedirs(os.path.join(self.tmp, ".claude", "agents"))
        for folder in ("dev_team", "sec_team"):
            app._save_orchestration({
                "created_at": "2026-06-21", "team": folder.title(),
                "team_folder": folder, "orchestrator": folder + "-orch",
                "agents": [{"name": "a"}], "transitions": [],
                "mcp_tools": [], "mcp_servers": [],
            })  # sec_team ends up active (saved last)

    def tearDown(self):
        os.chdir(self.origin)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_shift_tab_switches_active(self):
        calls = []

        def fake_menu(options, *a, **k):
            calls.append(k.get("extra_keys"))
            return ("__switch__", "") if len(calls) == 1 else (None, "")

        with mock.patch.object(app, "menu", fake_menu):
            app.handle_wizard_yaml()

        # Started active on sec_team (index 1); one switch wraps to dev_team.
        self.assertEqual(app.load_orchestration()["team_folder"], "dev_team")
        # The menu was offered the shift+tab switch key (two orchestrations).
        self.assertEqual(calls[0], {"shift_tab": "__switch__"})


class MainRedirectAfterWipeTest(unittest.TestCase):
    """main() must land on the right menu after a wipe (the user's spec)."""

    def setUp(self):
        self.origin = os.getcwd()
        self.tmp = tempfile.mkdtemp(prefix="ww_main_")
        os.chdir(self.tmp)
        os.makedirs(os.path.join(self.tmp, ".claude", "agents"))

    def tearDown(self):
        os.chdir(self.origin)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _save(self, folder):
        app._save_orchestration({
            "created_at": "2026-06-21", "team": folder.title(), "team_folder": folder,
            "orchestrator": folder + "-orch", "agents": [{"name": "a"}],
            "transitions": [], "mcp_tools": [], "mcp_servers": [],
        })

    def test_wipe_with_others_returns_to_existing_menu(self):
        self._save("dev_team")
        self._save("sec_team")  # active
        handle_calls = []

        def fake_handle(path=None):
            handle_calls.append(1)
            if len(handle_calls) == 1:
                app.wipe_orchestration()      # removes active; dev_team remains
                return "__refresh__"
            return None                        # second visit: user quits

        def fail_first():
            self.fail("create menu must not show while orchestrations remain")

        with mock.patch.object(app, "handle_wizard_yaml", fake_handle), \
             mock.patch.object(app, "_first_menu", fail_first), \
             contextlib.redirect_stdout(io.StringIO()):
            app.main()

        self.assertEqual(len(handle_calls), 2)  # redirected back to existing menu
        self.assertEqual([o["team_folder"] for o in app.list_orchestrations()], ["dev_team"])

    def test_wipe_last_returns_to_create_menu(self):
        self._save("dev_team")  # the only one
        handle_calls, first_calls = [], []

        def fake_handle(path=None):
            handle_calls.append(1)
            app.wipe_orchestration()           # last one → manifest removed
            return "__refresh__"

        def fake_first():
            first_calls.append(1)
            return None                        # user quits the create menu

        with mock.patch.object(app, "handle_wizard_yaml", fake_handle), \
             mock.patch.object(app, "_first_menu", fake_first), \
             contextlib.redirect_stdout(io.StringIO()):
            app.main()

        self.assertEqual(len(handle_calls), 1)
        self.assertEqual(len(first_calls), 1)  # redirected to the create menu
        self.assertEqual(app.list_orchestrations(), [])


class RealMockBuildTest(unittest.TestCase):
    """Against the real mock backend (no injected reply), the build must create
    orchestration files — i.e. the mock returns a usable agent plan."""

    def setUp(self):
        self.origin = os.getcwd()
        self.tmp = tempfile.mkdtemp(prefix="ww_realmock_")
        os.chdir(self.tmp)
        self.menu = ScriptedMenu()
        self._patches = [
            mock.patch.dict(os.environ, {}, clear=True),  # no WHITE_WIZARD_MOCK_REPLY
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

    def test_build_with_real_mock_creates_files(self):
        with contextlib.redirect_stdout(io.StringIO()):
            app.main()
        self.assertTrue(glob.glob(os.path.join(self.tmp, ".claude", "agents", "*_team")),
                        "real mock backend did not build an orchestration")
        self.assertTrue(os.path.isfile(os.path.join(self.tmp, ".claude", "wizard-orch.json")))


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

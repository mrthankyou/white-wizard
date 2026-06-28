"""Unit/integration tests for app-level behaviour and recent bug fixes.

Covers:
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
import subprocess
import tempfile
import time
import unittest
from unittest import mock

from white_wizard import app
from white_wizard import db as wizard_db
from white_wizard import ui

from tests.test_integration import CANNED_PLAN, ScriptedMenu


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

    def test_browsing_syncs_active_orchestration(self):
        # The split pane calls get_state_fn(sel_orch_i) as the user browses
        # orchestrations; doing so must make the displayed orch the active one,
        # so wipe and task/adjust act on what's actually on screen.
        seen = {}

        def fake_pane(get_state_fn, get_options_fn, handle_choice_fn,
                      handle_task_fn=None):
            seen["i0"] = get_state_fn(0)["current_orch"]["team_folder"]
            seen["active_after_0"] = app.load_orchestration()["team_folder"]
            seen["i1"] = get_state_fn(1)["current_orch"]["team_folder"]
            seen["active_after_1"] = app.load_orchestration()["team_folder"]
            return None

        with mock.patch.object(app, "run_split_pane", fake_pane):
            app.handle_wizard_yaml()

        # orchs are in creation order: dev_team (0), sec_team (1).
        self.assertEqual(seen["i0"], "dev_team")
        self.assertEqual(seen["active_after_0"], "dev_team")
        self.assertEqual(seen["i1"], "sec_team")
        self.assertEqual(seen["active_after_1"], "sec_team")


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
            mock.patch.object(app, "select_menu", self.menu),
            mock.patch.object(app, "read_key", lambda: "enter"),
            mock.patch.object(app, "loading", lambda *a, **k: None),
            mock.patch.object(app, "_ensure_mcp", lambda *a, **k: ("ok", "")),
            mock.patch.object(app.time, "sleep", lambda *a, **k: None),
            # The post-build main menu is a curses UI; quit it immediately so
            # main() exits after the build rather than starting curses.
            mock.patch.object(app, "run_split_pane", lambda *a, **k: None),
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
            mock.patch.object(app, "select_menu", self.menu),
            mock.patch.object(app, "read_key", lambda: "enter"),
            mock.patch.object(app, "loading", lambda *a, **k: None),
            mock.patch.object(app, "_ensure_mcp", lambda *a, **k: ("ok", "")),
            mock.patch.object(app.time, "sleep", lambda *a, **k: None),
            # The post-build main menu is a curses UI; quit it immediately so
            # main() exits after the build rather than starting curses.
            mock.patch.object(app, "run_split_pane", lambda *a, **k: None),
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


class RunSingleTaskWhilePausedTest(unittest.TestCase):
    """A user can run one hand-picked task from the right pane while the stream
    is paused, without re-enabling the whole stream."""

    def setUp(self):
        self.origin = os.getcwd()
        self.tmp = tempfile.mkdtemp(prefix="ww_oneshot_")
        os.chdir(self.tmp)
        os.makedirs(os.path.join(self.tmp, ".claude", "agents"))
        wizard_db.init_db()
        app._save_orchestration({
            "created_at": "2026-06-27", "team": "Dev Team", "team_folder": "dev_team",
            "orchestrator": "dev-orchestrator", "agents": [{"name": "a"}],
            "transitions": [], "mcp_tools": [], "mcp_servers": [],
        })

    def tearDown(self):
        os.chdir(self.origin)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_run_task_once_executes_while_disabled(self):
        folder = "dev_team"
        wizard_db.set_stream_enabled(folder, False)
        tid = wizard_db.add_task(folder, "add a smoke test", "user", is_user_prompt=True)
        orch = app.load_orchestration()

        with mock.patch.dict(os.environ, {"WHITE_WIZARD_MOCK_REPLY": "ok"}, clear=True):
            app._run_task_once(orch, {"id": tid, "description": "add a smoke test",
                                      "task_type": "user"})
            for _ in range(40):  # up to ~2s for the one-shot thread to finish
                row = next(t for t in wizard_db.get_tasks_for_orch(folder) if t["id"] == tid)
                if row["status"] == "done":
                    break
                time.sleep(0.05)

        self.assertEqual(row["status"], "done")
        # The stream itself stays paused — only the one task ran.
        self.assertFalse(wizard_db.is_stream_enabled(folder))

    def test_user_task_failed_when_no_file_changes(self):
        """In a git repo, a user task that produces no file changes is recorded
        as 'failed' rather than a false 'done'."""
        if shutil.which("git") is None:
            self.skipTest("git not available")
        subprocess.run(["git", "init", "-q"], cwd=self.tmp, check=True)
        folder = "dev_team"
        wizard_db.set_stream_enabled(folder, False)
        tid = wizard_db.add_task(folder, "add tests", "user", is_user_prompt=True)
        orch = app.load_orchestration()

        with mock.patch.dict(os.environ,
                             {"WHITE_WIZARD_MOCK_REPLY": "I changed nothing"}, clear=True):
            app._run_task_once(orch, {"id": tid, "description": "add tests",
                                      "task_type": "user"})
            for _ in range(40):
                row = next(t for t in wizard_db.get_tasks_for_orch(folder) if t["id"] == tid)
                if row["status"] in ("done", "failed"):
                    break
                time.sleep(0.05)

        self.assertEqual(row["status"], "failed")

    def test_git_commit_helper_commits_only_listed_files(self):
        """_git_commit stages and commits exactly the given paths, leaving an
        unrelated dirty file untouched."""
        if shutil.which("git") is None:
            self.skipTest("git not available")
        for args in (["init", "-q"], ["config", "user.email", "t@t"],
                     ["config", "user.name", "t"]):
            subprocess.run(["git", *args], cwd=self.tmp, check=True)
        with open(os.path.join(self.tmp, "new.py"), "w") as f:
            f.write("x = 1\n")
        with open(os.path.join(self.tmp, "other.txt"), "w") as f:  # unrelated WIP
            f.write("work in progress\n")

        ref = app._git_commit(["new.py"], "add new.py\n\nbody")

        self.assertTrue(ref)
        names = subprocess.run(
            ["git", "show", "--name-only", "--format=", "HEAD"],
            cwd=self.tmp, capture_output=True, text=True).stdout.split()
        self.assertIn("new.py", names)
        self.assertNotIn("other.txt", names)   # unrelated dirty file left alone
        # The unrelated file is still present and uncommitted.
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "other.txt")))

    def test_auto_commit_off_changes_without_committing(self):
        """With auto-commit disabled, a task still makes (and reports) changes
        but does not create a commit."""
        if shutil.which("git") is None:
            self.skipTest("git not available")
        for args in (["init", "-q"], ["config", "user.email", "t@t"],
                     ["config", "user.name", "t"],
                     ["commit", "-q", "--allow-empty", "-m", "init"]):
            subprocess.run(["git", *args], cwd=self.tmp, check=True)
        app._set_auto_commit(False)
        self.assertFalse(app._auto_commit_enabled())

        worker = app.StreamWorker({"team": "Dev Team", "team_folder": "dev_team",
                                   "model": "m"})

        def fake_run_task(prompt, **kw):
            with open(os.path.join(self.tmp, "f.py"), "w") as fh:
                fh.write("x = 1\n")
            return "made f.py"

        with mock.patch.object(app, "run_task", fake_run_task):
            ok, summary = worker._run_user_task("add f")

        self.assertTrue(ok)
        self.assertIn("auto-commit off", summary)
        # No new commit beyond the initial one.
        count = subprocess.run(["git", "rev-list", "--count", "HEAD"],
                               cwd=self.tmp, capture_output=True, text=True).stdout.strip()
        self.assertEqual(count, "1")
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "f.py")))  # change made

    def test_commit_subject_uses_agent_summary_not_prompt(self):
        """The commit subject is the agent's one-line summary of what it changed,
        not the raw task prompt."""
        if shutil.which("git") is None:
            self.skipTest("git not available")
        for args in (["init", "-q"], ["config", "user.email", "t@t"],
                     ["config", "user.name", "t"],
                     ["commit", "-q", "--allow-empty", "-m", "init"]):
            subprocess.run(["git", *args], cwd=self.tmp, check=True)

        worker = app.StreamWorker({"team": "Dev Team", "team_folder": "dev_team",
                                   "model": "m"})

        def fake_run_task(prompt, **kw):
            with open(os.path.join(self.tmp, "g.py"), "w") as fh:
                fh.write("def greet():\n    return 'hi'\n")
            return "Add greeting helper to g.py\n(plus a couple of details)"

        with mock.patch.object(app, "run_task", fake_run_task):
            ok, summary = worker._run_user_task("make a greeting")

        self.assertTrue(ok)
        subject = subprocess.run(["git", "log", "-1", "--format=%s"],
                                 cwd=self.tmp, capture_output=True, text=True).stdout.strip()
        self.assertEqual(subject, "Add greeting helper to g.py")  # summary, not "make a greeting"

    def test_edit_to_existing_untracked_file_is_detected(self):
        """Editing a pre-existing untracked file (common in a fresh repo) counts
        as a real change — not a false 'no changes / failed'."""
        if shutil.which("git") is None:
            self.skipTest("git not available")
        subprocess.run(["git", "init", "-q"], cwd=self.tmp, check=True)
        with open(os.path.join(self.tmp, "existing.py"), "w") as f:
            f.write("old\n")                       # untracked, never added

        worker = app.StreamWorker({"team": "Dev Team", "team_folder": "dev_team",
                                   "model": "m"})

        def fake_run_task(prompt, **kw):
            with open(os.path.join(self.tmp, "existing.py"), "w") as fh:
                fh.write("new content here\n")      # edit the untracked file
            return "Updated existing.py"

        with mock.patch.object(app, "run_task", fake_run_task):
            ok, summary = worker._run_user_task("update it")

        self.assertTrue(ok)                          # detected as a change…
        self.assertNotIn("no file changes", summary) # …not a false failure


class WipeAndPruneTest(unittest.TestCase):
    """Wiping an orchestration also clears its tasks/stream_config from the DB,
    and a startup prune removes state left by orchestrations wiped earlier."""

    def setUp(self):
        self.origin = os.getcwd()
        self.tmp = tempfile.mkdtemp(prefix="ww_wipe_")
        os.chdir(self.tmp)
        os.makedirs(os.path.join(self.tmp, ".claude", "agents", "dev_team"))
        wizard_db.init_db()
        app._save_orchestration({
            "created_at": "2026-06-28", "team": "Dev Team", "team_folder": "dev_team",
            "orchestrator": "dev-orchestrator", "agents": [{"name": "a"}],
            "transitions": [], "mcp_tools": [], "mcp_servers": [],
        })

    def tearDown(self):
        os.chdir(self.origin)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_wipe_removes_the_orchs_tasks_and_config(self):
        wizard_db.add_task("dev_team", "do x", "user", is_user_prompt=True)
        wizard_db.add_task("dev_team", "scan", "bug")
        wizard_db.set_stream_enabled("dev_team", False)
        self.assertEqual(len(wizard_db.get_tasks_for_orch("dev_team")), 2)

        app.wipe_orchestration("dev_team")

        self.assertEqual(wizard_db.get_tasks_for_orch("dev_team"), [])
        # config row gone → is_stream_enabled falls back to the default (True)
        self.assertTrue(wizard_db.is_stream_enabled("dev_team"))

    def test_prune_removes_orphaned_tasks_only(self):
        wizard_db.add_task("dev_team", "keep me", "user")    # known orch
        wizard_db.add_task("ghost_team", "orphan", "bug")    # wiped/unknown orch
        removed = wizard_db.prune_orphaned_state({"dev_team"})
        self.assertEqual(removed, 1)
        self.assertEqual(len(wizard_db.get_tasks_for_orch("dev_team")), 1)
        self.assertEqual(wizard_db.get_tasks_for_orch("ghost_team"), [])


if __name__ == "__main__":
    unittest.main()

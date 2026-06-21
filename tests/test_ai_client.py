#!/usr/bin/env python3
"""Tests for white_wizard.ai_client. The real `claude` binary is never invoked."""

import contextlib
import io
import logging
import os
import shutil
import tempfile
import unittest
from unittest import mock

from white_wizard import ai_client


class MockBackendTests(unittest.TestCase):
    def test_default_backend_is_mock(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            reply = ai_client.ask("Plan an orchestration system")
        self.assertIn("Simulated AI response", reply)
        self.assertIn("Plan an orchestration system", reply)

    def test_mock_includes_model_name(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            reply = ai_client.ask("hi", model="claude-opus-4-8")
        self.assertIn("claude-opus-4-8", reply)

    def test_mock_reply_override(self):
        with mock.patch.dict("os.environ",
                             {"WHITE_WIZARD_MOCK_REPLY": "canned!"}, clear=True):
            self.assertEqual(ai_client.ask("anything"), "canned!")

    def test_unknown_backend_raises(self):
        with self.assertRaises(ValueError):
            ai_client.ask("hi", backend="gpt-bogus")

    def test_mock_returns_buildable_plan_for_plan_prompt(self):
        from white_wizard import app
        with mock.patch.dict("os.environ", {}, clear=True):
            reply = ai_client.ask("Otherwise return the agent plan as JSON.", backend="mock")
        plan = app.parse_agent_plan(reply)
        self.assertIsNotNone(plan)
        self.assertTrue(plan["agents"] and all(a.get("name") for a in plan["agents"]))

    def test_mock_echoes_for_non_plan_prompt(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            reply = ai_client.ask("Summarize this codebase please.", backend="mock")
        self.assertIn("Simulated AI response", reply)


class ParseArgsTests(unittest.TestCase):
    def test_warns_on_unrecognized_flag(self):
        saved = ai_client._backend
        try:
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                ai_client.parse_args(["--bogus"])
            self.assertIn("unrecognized", err.getvalue())
            self.assertIn("--bogus", err.getvalue())
        finally:
            ai_client._backend = saved

    def test_no_warning_for_known_flags(self):
        saved = ai_client._backend
        try:
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                flags = ai_client.parse_args(["--mock", "--stream"])
            self.assertEqual(err.getvalue(), "")
            self.assertTrue(flags["stream"])
        finally:
            ai_client._backend = saved


class ClaudeBackendTests(unittest.TestCase):
    def _proc(self, returncode=0, stdout="", stderr=""):
        proc = mock.Mock()
        proc.communicate.return_value = (stdout, stderr)
        proc.returncode = returncode
        return proc

    def test_claude_builds_command_and_returns_stdout(self):
        proc = self._proc(returncode=0, stdout="hello from claude\n")
        with mock.patch("white_wizard.ai_client.shutil.which", return_value="/usr/bin/claude"), \
             mock.patch("white_wizard.ai_client.subprocess.Popen", return_value=proc) as popen:
            reply = ai_client.ask("Say hi", model="claude-opus-4-8", backend="claude")

        self.assertEqual(reply, "hello from claude")
        cmd = popen.call_args.args[0]
        self.assertEqual(cmd[:3], ["claude", "-p", "Say hi"])
        self.assertIn("--model", cmd)
        self.assertIn("claude-opus-4-8", cmd)

    def test_claude_missing_binary_raises(self):
        with mock.patch("white_wizard.ai_client.shutil.which", return_value=None):
            with self.assertRaises(RuntimeError):
                ai_client.ask("hi", backend="claude")

    def test_claude_nonzero_exit_raises(self):
        proc = self._proc(returncode=1, stderr="boom")
        with mock.patch("white_wizard.ai_client.shutil.which", return_value="/usr/bin/claude"), \
             mock.patch("white_wizard.ai_client.subprocess.Popen", return_value=proc):
            with self.assertRaises(RuntimeError):
                ai_client.ask("hi", backend="claude")


class DebugLoggingTests(unittest.TestCase):
    def setUp(self):
        self.origin = os.getcwd()
        self.tmp = tempfile.mkdtemp(prefix="ww_dbg_")
        os.chdir(self.tmp)

    def tearDown(self):
        # Tear down the singleton logger so its file handle doesn't leak.
        logger = logging.getLogger("white_wizard.debug")
        for h in list(logger.handlers):
            h.close()
            logger.removeHandler(h)
        ai_client._logger = None
        ai_client._log_path = None
        ai_client._seq = 0
        os.chdir(self.origin)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_request_and_response_are_logged(self):
        path = ai_client.enable_debug_logging()
        with mock.patch.dict("os.environ",
                             {"WHITE_WIZARD_MOCK_REPLY": "canned reply"}, clear=True):
            ai_client.ask("trace me please", model="claude-opus-4-8")

        with open(path) as f:
            contents = f.read()
        self.assertIn("REQUEST #1", contents)
        self.assertIn("trace me please", contents)
        self.assertIn("RESPONSE #1", contents)
        self.assertIn("canned reply", contents)
        self.assertTrue(path.endswith(os.path.join(".wizard", "debug.log")))

    def test_no_log_file_without_debug(self):
        # Without enable_debug_logging, ask() must not write a debug log.
        with mock.patch.dict("os.environ",
                             {"WHITE_WIZARD_MOCK_REPLY": "x"}, clear=True):
            ai_client.ask("hello")
        self.assertFalse(os.path.exists(os.path.join(self.tmp, ".wizard", "debug.log")))


if __name__ == "__main__":
    unittest.main()

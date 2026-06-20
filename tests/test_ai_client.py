#!/usr/bin/env python3
"""Tests for white_wizard.ai_client. The real `claude` binary is never invoked."""

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


if __name__ == "__main__":
    unittest.main()

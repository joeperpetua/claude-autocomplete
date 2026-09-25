import json
import os
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

os.environ["BRIDGE_LOG_FILE"] = os.path.join(tempfile.gettempdir(), "autocomplete-bridge-tests.log")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app


class FakeProcess:
    pid = 4242
    returncode = 0

    def __init__(self, *args, **kwargs):
        self.stdout = iter(())
        self.stderr = iter(())
        self.stdin = mock.Mock()

    def poll(self):
        return None

    def communicate(self, timeout=None):
        return "", ""


def scripted_process(*events):
    class ScriptedProcess(FakeProcess):
        def __init__(self, *args, **kwargs):
            super().__init__()
            self.stdout = iter(json.dumps(event) + "\n" for event in events)

    return ScriptedProcess


class WorkerEventTests(unittest.TestCase):
    def setUp(self):
        self.sock, self.peer = socket.socketpair()
        self.addCleanup(self.sock.close)
        self.addCleanup(self.peer.close)

    def worker(self, *events):
        with mock.patch.object(app.subprocess, "Popen", side_effect=scripted_process(*events)):
            return app.ClaudeWorker("haiku")

    def sent(self, worker):
        return [json.loads(call.args[0]) for call in worker.proc.stdin.write.call_args_list]

    def test_ready_after_local_clear_turn(self):
        worker = self.worker({"type": "conversation_reset"}, {"type": "system", "subtype": "init"}, {"type": "result", "is_error": False})
        worker.wait_ready()
        self.assertEqual(self.sent(worker)[0]["message"]["content"], "/clear")

    def test_turn_returns_assistant_text(self):
        worker = self.worker({"type": "assistant", "message": {"content": [{"type": "text", "text": "<insert>x</insert>"}]}})
        self.assertEqual(worker.complete("prompt", self.sock), "<insert>x</insert>")

    def test_auth_failure_raises_api_error(self):
        message = "Failed to authenticate: OAuth session expired and could not be refreshed"
        worker = self.worker(
            {"type": "assistant", "error": "authentication_failed", "is_api_error_message": True,
             "message": {"content": [{"type": "text", "text": message}]}},
            {"type": "result", "subtype": "success", "is_error": True, "result": message},
        )
        with self.assertRaises(app.ClaudeApiError) as caught:
            worker.complete("prompt", self.sock)
        self.assertEqual((caught.exception.code, str(caught.exception)), (app.AUTH_FAILED, message))
        self.assertFalse(worker.result_pending)


class NoWindowTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "console windows exist only on Windows")
    def test_flag_is_create_no_window_on_windows(self):
        self.assertEqual(app.NO_WINDOW, subprocess.CREATE_NO_WINDOW)

    def test_worker_starts_without_console_window(self):
        with mock.patch.object(app.subprocess, "Popen", side_effect=FakeProcess) as popen:
            app.ClaudeWorker("haiku")
        self.assertEqual(popen.call_args.kwargs["creationflags"], app.NO_WINDOW)

    def test_one_off_cli_starts_without_console_window(self):
        with mock.patch.object(app.subprocess, "Popen", side_effect=FakeProcess) as popen:
            app.run_cli(["claude", "-p", "x"], mock.Mock())
        self.assertEqual(popen.call_args.kwargs["creationflags"], app.NO_WINDOW)

    @unittest.skipUnless(os.name == "nt", "taskkill exists only on Windows")
    def test_taskkill_starts_without_console_window(self):
        with mock.patch.object(app.subprocess, "run") as run:
            app.kill_process_tree(FakeProcess())
        self.assertEqual(run.call_args.kwargs["creationflags"], app.NO_WINDOW)


if __name__ == "__main__":
    unittest.main()

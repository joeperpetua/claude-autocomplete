import os
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

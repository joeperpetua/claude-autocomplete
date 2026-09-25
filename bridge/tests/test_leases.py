import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.request

os.environ["BRIDGE_LOG_FILE"] = os.path.join(tempfile.gettempdir(), "autocomplete-bridge-tests.log")
BRIDGE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BRIDGE_DIR)
import app


class FakeClock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


class LeaseTableTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.table = app.LeaseTable(ttl=60, clock=self.clock)

    def test_idle_from_start_until_first_lease(self):
        self.clock.now += 5
        self.assertEqual(self.table.idle_seconds(), 5)
        self.table.renew("a")
        self.assertEqual(self.table.idle_seconds(), 0)

    def test_idle_starts_when_last_lease_is_released(self):
        self.table.renew("a")
        self.table.renew("b")
        self.assertEqual(self.table.release("a"), 1)
        self.clock.now += 10
        self.assertEqual(self.table.idle_seconds(), 0)
        self.assertEqual(self.table.release("b"), 0)
        self.clock.now += 10
        self.assertEqual(self.table.idle_seconds(), 10)

    def test_lease_expires_without_renewal(self):
        self.table.renew("a")
        self.clock.now += 59
        self.assertEqual(self.table.count(), 1)
        self.clock.now += 2
        self.assertEqual(self.table.count(), 0)
        self.clock.now += 4
        self.assertEqual(self.table.idle_seconds(), 4)

    def test_renewal_extends_lease(self):
        self.table.renew("a")
        self.clock.now += 50
        self.table.renew("a")
        self.clock.now += 50
        self.assertEqual(self.table.count(), 1)

    def test_releasing_unknown_client_is_harmless(self):
        self.assertEqual(self.table.release("nobody"), 0)


class WatchLeasesTests(unittest.TestCase):
    def test_shuts_down_server_after_idle_period(self):
        server = app.BridgeServer(("127.0.0.1", 0), app.CLIBridgeHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        table = app.LeaseTable(ttl=60)
        threading.Thread(target=app.watch_leases, args=(server, table, 0.2, 0.05), daemon=True).start()
        thread.join(timeout=3)
        server.server_close()
        self.assertFalse(thread.is_alive())


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def request(method, url, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    with urllib.request.urlopen(urllib.request.Request(url, data=data, method=method), timeout=2) as response:
        return json.loads(response.read().decode())


class ManagedBridgeProcessTests(unittest.TestCase):
    def start_bridge(self, port, **extra):
        env = {**os.environ, "BRIDGE_PORT": str(port), "BRIDGE_FAKE_COMPLETION": "x", "BRIDGE_MANAGED": "1",
               "BRIDGE_IDLE_EXIT_SECONDS": "1", "BRIDGE_LEASE_TTL_SECONDS": "2", **extra}
        return subprocess.Popen([sys.executable, os.path.join(BRIDGE_DIR, "app.py")], env=env,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def wait_healthy(self, port, proc):
        deadline = time.time() + 10
        while time.time() < deadline:
            if proc.poll() is not None:
                self.fail(f"Bridge exited early with code {proc.returncode}")
            try:
                return request("GET", f"http://127.0.0.1:{port}/")
            except OSError:
                time.sleep(0.05)
        self.fail("Bridge did not become healthy")

    def test_exits_after_last_lease_is_released(self):
        port = free_port()
        proc = self.start_bridge(port)
        try:
            self.assertEqual(self.wait_healthy(port, proc)["managed"], True)
            self.assertEqual(request("POST", f"http://127.0.0.1:{port}/lease", {"client": "w1"})["leases"], 1)
            time.sleep(1.5)
            self.assertIsNone(proc.poll())
            self.assertEqual(request("DELETE", f"http://127.0.0.1:{port}/lease/w1")["leases"], 0)
            self.assertEqual(proc.wait(timeout=5), 0)
        finally:
            if proc.poll() is None:
                proc.kill()

    def test_second_bridge_on_same_port_exits(self):
        port = free_port()
        first = self.start_bridge(port, BRIDGE_IDLE_EXIT_SECONDS="30")
        try:
            self.wait_healthy(port, first)
            second = self.start_bridge(port)
            self.assertNotEqual(second.wait(timeout=10), 0)
            self.assertIsNone(first.poll())
        finally:
            first.kill()
            first.wait()


if __name__ == "__main__":
    unittest.main()

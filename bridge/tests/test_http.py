import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

os.environ["BRIDGE_LOG_FILE"] = os.path.join(tempfile.gettempdir(), "autocomplete-bridge-tests.log")
os.environ["BRIDGE_FAKE_COMPLETION"] = "(fullName);"
os.environ["BRIDGE_FAKE_DELAY_MS"] = "10"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app

FIM = "<fim_prefix>const names = getNames<fim_suffix>\n<fim_middle>"


class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = app.ThreadingHTTPServer(("127.0.0.1", 0), app.CLIBridgeHandler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def call(self, method, path, payload=None, raw=None):
        data = raw if raw is not None else json.dumps(payload).encode() if payload is not None else None
        request = urllib.request.Request(f"http://127.0.0.1:{self.server.server_address[1]}{path}", data=data, method=method)
        try:
            with urllib.request.urlopen(request) as response:
                return response.status, response.headers.get("Content-Type"), response.read().decode()
        except urllib.error.HTTPError as error:
            return error.code, error.headers.get("Content-Type"), error.read().decode()

    def test_chat_endpoint_is_rejected(self):
        status, _, body = self.call("POST", "/v1/chat/completions", {"messages": [{"role": "user", "content": "hi"}]})
        self.assertEqual(status, 404)
        self.assertIn("only serves autocomplete", json.loads(body)["error"]["message"])

    def test_request_without_prompt_is_rejected(self):
        status, _, body = self.call("POST", "/v1/completions", {"messages": [{"role": "user", "content": "hi"}]})
        self.assertEqual(status, 400)
        self.assertIn("prompt", json.loads(body)["error"]["message"])

    def test_non_object_body_is_rejected(self):
        self.assertEqual(self.call("POST", "/v1/completions", raw=b"[1, 2]")[0], 400)

    def test_invalid_json_gets_json_error(self):
        status, _, body = self.call("POST", "/v1/completions", raw=b"{not json")
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(body)["error"]["message"], "Invalid JSON")

    def test_completion(self):
        status, _, body = self.call("POST", "/v1/completions", {"model": "claude-haiku", "prompt": FIM})
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertEqual(payload["object"], "text_completion")
        self.assertEqual(payload["choices"][0]["text"], "(fullName);")

    def test_streamed_completion(self):
        status, content_type, body = self.call("POST", "/v1/completions", {"model": "claude-haiku", "stream": True, "prompt": FIM})
        self.assertEqual((status, content_type), (200, "text/event-stream"))
        chunks = [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: {")]
        self.assertEqual("".join(chunk["choices"][0]["text"] for chunk in chunks), "(fullName);")
        self.assertEqual(chunks[-1]["choices"][0]["finish_reason"], "stop")
        self.assertTrue(body.rstrip().endswith("data: [DONE]"))

    def test_alternative_paths(self):
        self.assertEqual(self.call("POST", "/completions", {"model": "claude-haiku", "prompt": FIM})[0], 200)
        self.assertEqual(self.call("POST", "/v1/completions?x=1", {"model": "claude-haiku", "prompt": FIM})[0], 200)

    def test_client_request_shapes(self):
        shapes = {
            "zed": {"model": "claude-haiku", "prompt": FIM, "max_tokens": 64, "stop": ["\n\n"]},
            "twinny": {"model": "claude-haiku", "prompt": FIM, "stream": True, "keep_alive": "5m",
                       "options": {"temperature": 0.2, "num_predict": 512, "stop": ["<file_sep>"]}},
            "minuet": {"model": "claude-haiku", "prompt": FIM, "stream": True, "max_tokens": 256, "top_p": 0.9},
            "tabby": {"model": "claude-haiku", "prompt": FIM, "stream": True, "max_tokens": 64, "temperature": 0.1,
                      "stop": ["\n\n"], "seed": 0, "presence_penalty": 0.0},
        }
        for client, payload in shapes.items():
            with self.subTest(client=client):
                status, _, body = self.call("POST", "/v1/completions", payload)
                self.assertEqual(status, 200)
                if payload.get("stream"):
                    chunks = [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: {")]
                    self.assertEqual("".join(chunk["choices"][0]["text"] for chunk in chunks), "(fullName);")
                    self.assertEqual(chunks[-1]["choices"][0]["finish_reason"], "stop")
                else:
                    response = json.loads(body)
                    self.assertEqual(response["choices"][0]["text"], "(fullName);")
                    self.assertEqual(set(response["usage"]), {"prompt_tokens", "completion_tokens", "total_tokens"})

    def test_health_check(self):
        status, _, body = self.call("GET", "/")
        payload = json.loads(body)
        self.assertEqual((status, payload["status"], payload["managed"]), (200, "ok", False))
        self.assertIn("leases", payload)

    def test_lease_renew_and_release(self):
        status, _, body = self.call("POST", "/lease", {"client": "window-1"})
        self.assertEqual((status, json.loads(body)["leases"]), (200, 1))
        status, _, body = self.call("DELETE", "/lease/window-1")
        self.assertEqual((status, json.loads(body)["leases"]), (200, 0))

    def test_lease_without_client_is_rejected(self):
        self.assertEqual(self.call("POST", "/lease", {"id": 1})[0], 400)
        self.assertEqual(self.call("POST", "/lease", raw=b"not json")[0], 400)

    def test_delete_outside_lease_path_is_rejected(self):
        self.assertEqual(self.call("DELETE", "/v1/completions")[0], 404)


if __name__ == "__main__":
    unittest.main()

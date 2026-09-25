#!/usr/bin/env python3
import itertools
import json
import logging
import logging.handlers
import os
import queue
import select
import socket
import subprocess
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from completion import CURSOR, INSERT_CLOSE, INSERT_OPEN, SYSTEM_PROMPT, extract_completion, split_fim, transform_fim_to_prompt

PORT = int(os.environ.get("BRIDGE_PORT", "11435"))
COMPLETION_PATHS = ("/completions", "/v1/completions")
LEASE_PATH = "/lease"
DISCONNECT_POLL_SECONDS = 0.05
FAKE_COMPLETION = os.environ.get("BRIDGE_FAKE_COMPLETION")
FAKE_DELAY_MS = int(os.environ.get("BRIDGE_FAKE_DELAY_MS", "300"))
EXPLANATION_REASONS = ("untagged", "mentions-cursor", "prose")
AUTH_FAILED = "authentication_failed"

POOL_SIZE = int(os.environ.get("BRIDGE_POOL_SIZE", "2"))
MANAGED = os.environ.get("BRIDGE_MANAGED") == "1"
LEASE_TTL_SECONDS = float(os.environ.get("BRIDGE_LEASE_TTL_SECONDS", "60"))
IDLE_EXIT_SECONDS = float(os.environ.get("BRIDGE_IDLE_EXIT_SECONDS", "30"))
PREWARM = [model.strip() for model in os.environ.get("BRIDGE_PREWARM", "").split(",") if model.strip()]
WORKER_CWD = os.path.join(tempfile.gettempdir(), "autocomplete-bridge-worker")
WORKER_READY_TIMEOUT_SECONDS = 20
WORKER_TURN_TIMEOUT_SECONDS = 30
WORKER_RESET_TIMEOUT_SECONDS = 10
WORKER_MAX_TURNS = 25
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

LOG_FILE = os.environ.get("BRIDGE_LOG_FILE", os.path.join(os.path.dirname(os.path.abspath(__file__)), "bridge.log"))
LOG_FORMAT = "%(asctime)s [%(levelname)s] [%(threadName)s] %(message)s"
LOG_DATEFMT = "%Y-%m-%d %H:%M:%S"

# Configure structured console logging
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
file_handler = logging.handlers.RotatingFileHandler(LOG_FILE, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8")
file_handler.setLevel(logging.DEBUG)
logging.basicConfig(level=logging.DEBUG, format=LOG_FORMAT, datefmt=LOG_DATEFMT, handlers=[console_handler, file_handler])
logger = logging.getLogger("CLIBridge")
request_counter = itertools.count(1)


class ClientDisconnected(Exception):
    pass


def client_disconnected(sock: socket.socket) -> bool:
    try:
        readable, _, _ = select.select([sock], [], [], 0)
        return bool(readable) and sock.recv(1, socket.MSG_PEEK) == b""
    except OSError:
        return True


def kill_process_tree(proc: subprocess.Popen):
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True, creationflags=NO_WINDOW)
    else:
        proc.kill()


def run_cli(cmd: list, sock: socket.socket) -> subprocess.CompletedProcess:
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=NO_WINDOW
    )
    while True:
        try:
            stdout, stderr = proc.communicate(timeout=DISCONNECT_POLL_SECONDS)
            return subprocess.CompletedProcess(cmd, proc.returncode, stdout, stderr)
        except subprocess.TimeoutExpired:
            if client_disconnected(sock):
                kill_process_tree(proc)
                try:
                    proc.communicate(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill()
                raise ClientDisconnected()


def run_fake(cmd: list, sock: socket.socket) -> subprocess.CompletedProcess:
    deadline = time.time() + FAKE_DELAY_MS / 1000
    while time.time() < deadline:
        if client_disconnected(sock):
            raise ClientDisconnected()
        time.sleep(min(DISCONNECT_POLL_SECONDS, max(deadline - time.time(), 0)))
    return subprocess.CompletedProcess(cmd, 0, f"{INSERT_OPEN}{FAKE_COMPLETION}{INSERT_CLOSE}", "")


def parse_model(requested_model: str) -> tuple:
    if "-" in requested_model:
        tool, model_id = requested_model.split("-", 1)
        return tool, model_id
    return requested_model, "default"


class WorkerError(Exception):
    pass


class ClaudeApiError(WorkerError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class ClaudeWorker:
    def __init__(self, model_id: str):
        self.model_id = model_id
        self.turns = 0
        self.result_pending = False
        self.events = queue.Queue()
        self.proc = subprocess.Popen(
            [
                "claude", "-p", "--model", model_id,
                "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
                "--safe-mode", "--tools", "", "--strict-mcp-config", "--no-session-persistence",
                "--system-prompt", SYSTEM_PROMPT,
                "--settings", json.dumps({"alwaysThinkingEnabled": False}),
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=WORKER_CWD,
            env={**os.environ, "MAX_THINKING_TOKENS": "0"},
            creationflags=NO_WINDOW
        )
        self.name = f"claude-{model_id}#{self.proc.pid}"
        threading.Thread(target=self._read_stdout, name=f"{self.name}-out", daemon=True).start()
        threading.Thread(target=self._read_stderr, name=f"{self.name}-err", daemon=True).start()

    def _read_stdout(self):
        for line in self.proc.stdout:
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                self.events.put(json.loads(line))
            except json.JSONDecodeError:
                logger.debug(f"[{self.name}] Unparsed output: {line}")
        self.events.put(None)

    def _read_stderr(self):
        for line in self.proc.stderr:
            logger.debug(f"[{self.name}] stderr: {line.rstrip()}")

    def alive(self) -> bool:
        return self.proc.poll() is None

    def send(self, payload: dict):
        try:
            self.proc.stdin.write(json.dumps(payload) + "\n")
            self.proc.stdin.flush()
        except (OSError, ValueError) as e:
            raise WorkerError(f"{self.name} stopped accepting input: {e}")

    def send_user(self, text: str):
        self.send({"type": "user", "message": {"role": "user", "content": text}})

    def next_event(self, timeout: float):
        try:
            event = self.events.get(timeout=timeout)
        except queue.Empty:
            return None
        if event is None:
            raise WorkerError(f"{self.name} exited with code {self.proc.poll()}")
        return event

    def wait_ready(self):
        # The CLI emits nothing until it reads input; a local /clear turn proves it booted without an API call.
        self.send_user("/clear")
        deadline = time.time() + WORKER_READY_TIMEOUT_SECONDS
        while time.time() < deadline:
            event = self.next_event(DISCONNECT_POLL_SECONDS)
            if event is not None and event.get("type") == "result":
                return
        if not self.alive():
            raise WorkerError(f"{self.name} exited during startup")

    def complete(self, prompt: str, sock: socket.socket) -> str:
        self.turns += 1
        self.result_pending = True
        self.send_user(prompt)
        deadline = time.time() + WORKER_TURN_TIMEOUT_SECONDS
        api_error = None
        while time.time() < deadline:
            if client_disconnected(sock):
                raise ClientDisconnected()
            event = self.next_event(DISCONNECT_POLL_SECONDS)
            if event is None:
                continue
            if event.get("type") == "assistant":
                if event.get("is_api_error_message") or event.get("error"):
                    api_error = event.get("error") or "api_error"
                    continue
                content = event.get("message", {}).get("content", [])
                parts = [block.get("text", "") for block in content if block.get("type") == "text"]
                if parts:
                    return "".join(parts)
            elif event.get("type") == "result":
                self.result_pending = False
                if event.get("is_error"):
                    message = event.get("result") or event.get("subtype")
                    if api_error:
                        raise ClaudeApiError(api_error, message)
                    raise WorkerError(f"{self.name} turn failed: {message}")
                return event.get("result") or ""
        raise WorkerError(f"{self.name} gave no answer within {WORKER_TURN_TIMEOUT_SECONDS}s")

    def interrupt(self):
        self.send({"type": "control_request", "request_id": f"interrupt-{self.turns}", "request": {"subtype": "interrupt"}})

    def close(self):
        if self.result_pending and self.alive():
            try:
                self.interrupt()
            except WorkerError:
                pass
        try:
            self.proc.stdin.close()
        except (OSError, ValueError):
            pass
        try:
            self.proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            kill_process_tree(self.proc)
            try:
                self.proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.proc.kill()

    def reset(self) -> bool:
        try:
            if self.result_pending:
                self.interrupt()
                if not self._wait_for_result():
                    return False
                self.result_pending = False
            self.send_user("/clear")
            return self._wait_for_result()
        except WorkerError as e:
            logger.debug(f"[{self.name}] Reset failed: {e}")
            return False

    def _wait_for_result(self) -> bool:
        deadline = time.time() + WORKER_RESET_TIMEOUT_SECONDS
        while time.time() < deadline:
            event = self.next_event(DISCONNECT_POLL_SECONDS)
            if event is not None and event.get("type") == "result":
                return True
        return False


class WorkerPool:
    def __init__(self, model_id: str, size: int):
        self.model_id = model_id
        self.idle = queue.Queue()
        self.lock = threading.Lock()
        self.slots = size
        self.workers = set()
        self.last_error = None
        self.closed = False
        for _ in range(size):
            self._start_spawn()

    def _start_spawn(self):
        threading.Thread(target=self._spawn, name="spawn-claude", daemon=True).start()

    def _spawn(self):
        started = time.time()
        try:
            worker = ClaudeWorker(self.model_id)
        except Exception as e:
            self._lose_slot(e)
            return
        with self.lock:
            self.workers.add(worker)
        try:
            worker.wait_ready()
        except WorkerError as e:
            self._drop(worker)
            self._lose_slot(e)
            return
        if self.closed:
            self._drop(worker)
            return
        logger.info(f"Worker {worker.name} ready in {(time.time() - started) * 1000:.0f}ms")
        self.idle.put(worker)

    def _lose_slot(self, error: Exception):
        with self.lock:
            self.slots -= 1
            self.last_error = error
        logger.error(f"Could not start a 'claude' worker for model '{self.model_id}': {error}")

    def _drop(self, worker: ClaudeWorker):
        with self.lock:
            self.workers.discard(worker)
        worker.close()

    def _replace(self, worker: ClaudeWorker):
        if not self.closed:
            self._start_spawn()
        self._drop(worker)

    def acquire(self, sock: socket.socket) -> ClaudeWorker:
        while True:
            try:
                worker = self.idle.get(timeout=DISCONNECT_POLL_SECONDS)
            except queue.Empty:
                if client_disconnected(sock):
                    raise ClientDisconnected()
                with self.lock:
                    if self.slots <= 0:
                        raise WorkerError(f"No 'claude' worker could start: {self.last_error}")
                continue
            if worker.alive():
                return worker
            self._replace(worker)

    def release(self, worker: ClaudeWorker):
        threading.Thread(target=self._recycle, args=(worker,), name="recycle-claude", daemon=True).start()

    def _recycle(self, worker: ClaudeWorker):
        if worker.turns < WORKER_MAX_TURNS and worker.alive() and worker.reset():
            self.idle.put(worker)
        else:
            self._replace(worker)

    def close(self):
        self.closed = True
        with self.lock:
            workers = list(self.workers)
        for worker in workers:
            worker.close()


class LeaseTable:
    def __init__(self, ttl: float, clock=time.monotonic):
        self.ttl = ttl
        self.clock = clock
        self.lock = threading.Lock()
        self.expires = {}
        self.idle_since = clock()

    def _prune(self, now: float):
        for client in [client for client, expiry in self.expires.items() if expiry <= now]:
            del self.expires[client]
            logger.info(f"Lease of client '{client}' expired")
        if not self.expires and self.idle_since is None:
            self.idle_since = now

    def renew(self, client: str) -> int:
        with self.lock:
            now = self.clock()
            self._prune(now)
            self.expires[client] = now + self.ttl
            self.idle_since = None
            return len(self.expires)

    def release(self, client: str) -> int:
        with self.lock:
            now = self.clock()
            self.expires.pop(client, None)
            self._prune(now)
            return len(self.expires)

    def count(self) -> int:
        with self.lock:
            self._prune(self.clock())
            return len(self.expires)

    def idle_seconds(self) -> float:
        with self.lock:
            now = self.clock()
            self._prune(now)
            return 0.0 if self.idle_since is None else now - self.idle_since


leases = LeaseTable(LEASE_TTL_SECONDS)


def watch_leases(server, table: LeaseTable, idle_exit: float, interval: float = 1.0):
    while table.idle_seconds() < idle_exit:
        time.sleep(interval)
    logger.info(f"No client leases for {idle_exit:.0f}s, shutting down the managed bridge")
    server.shutdown()


pools = {}
pools_lock = threading.Lock()


def get_pool(tool: str, model_id: str):
    if tool != "claude" or POOL_SIZE <= 0:
        return None
    with pools_lock:
        if model_id not in pools:
            os.makedirs(WORKER_CWD, exist_ok=True)
            logger.info(f"Starting {POOL_SIZE} warm 'claude' workers for model '{model_id}'")
            pools[model_id] = WorkerPool(model_id, POOL_SIZE)
        return pools[model_id]


def run_pooled(pool: WorkerPool, prompt: str, sock: socket.socket) -> str:
    waited = time.time()
    worker = pool.acquire(sock)
    logger.debug(f"Using worker {worker.name} (turn {worker.turns + 1}, waited {(time.time() - waited) * 1000:.0f}ms)")
    try:
        return worker.complete(prompt, sock)
    finally:
        pool.release(worker)


class BridgeServer(ThreadingHTTPServer):
    allow_reuse_address = False

    def server_bind(self):
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


class CLIBridgeHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        threading.current_thread().name = f"req-{next(request_counter)}"
        start_time = time.time()
        client_ip = self.client_address[0]
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)

        if self.path.split("?", 1)[0].rstrip("/") == LEASE_PATH:
            self.renew_lease(body)
            return

        if self.path.split("?", 1)[0].rstrip("/") not in COMPLETION_PATHS:
            logger.warning(f"[{client_ip}] Rejected POST {self.path}: only autocomplete on /v1/completions is supported")
            message = f"{self.path} is not supported. This bridge only serves autocomplete on /v1/completions."
            self.send_json(404, {"error": {"message": message, "type": "bridge_error"}})
            return

        try:
            data = json.loads(body.decode("utf-8"))
            logger.debug(f"[{client_ip}] Request POST {self.path} body:\n{json.dumps(data, indent=2, ensure_ascii=False)}")
        except Exception as e:
            logger.error(f"[{client_ip}] Failed to parse incoming JSON payload: {e}")
            logger.debug(f"[{client_ip}] Raw request body:\n{body.decode('utf-8', errors='replace')}")
            self.send_json(400, {"error": {"message": "Invalid JSON", "type": "bridge_error"}})
            return

        # 1. Parse prompt
        raw_prompt = data.get("prompt") if isinstance(data, dict) else None
        if not isinstance(raw_prompt, str):
            logger.error(f"[{client_ip}] Request has no 'prompt' string")
            self.send_json(400, {"error": {"message": "The request must contain a 'prompt' string.", "type": "bridge_error"}})
            return

        # 2. Parse tool and model from "tool-model"
        requested_model = data.get("model", "claude-haiku")
        tool, model_id = parse_model(requested_model)
        use_fake = FAKE_COMPLETION is not None
        pool = None if use_fake else get_pool(tool, model_id)

        prefix, suffix = split_fim(raw_prompt)
        prompt = transform_fim_to_prompt(prefix, suffix, include_instructions=pool is None)

        cmd = [tool, "-p", prompt, "--model", model_id]

        logger.info(f"[{client_ip}] Received POST {self.path} (model: {requested_model})")
        if pool is not None:
            logger.debug(f"[{client_ip}] Target: warm '{tool}' worker for model {model_id}")
        else:
            logger.debug(f"[{client_ip}] Target command: {tool} -p <prompt> --model {model_id}")
        logger.debug(f"[{client_ip}] Prompt sent to '{tool}':\n{prompt}")

        # 3. Execute subprocess
        exec_start = time.time()
        error_type = "bridge_error"
        try:
            if use_fake:
                result = run_fake(cmd, self.connection)
            elif pool is not None:
                result = subprocess.CompletedProcess(cmd, 0, run_pooled(pool, prompt, self.connection), "")
            else:
                result = run_cli(cmd, self.connection)
            logger.debug(
                f"[{client_ip}] '{tool}' finished with code {result.returncode}\n"
                f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
            )
            if result.returncode == 0:
                raw_output = result.stdout
                status_code = 200
            else:
                raw_output = result.stderr.strip() or result.stdout.strip() or f"Exit code {result.returncode}"
                logger.error(f"Subprocess returned non-zero exit code ({result.returncode}): {raw_output}")
                status_code = 500
        except ClientDisconnected:
            logger.warning(
                f"[{client_ip}] Client aborted after {(time.time() - exec_start) * 1000:.1f}ms, cancelled '{tool}'."
            )
            return
        except ClaudeApiError as e:
            error_type = e.code
            if e.code == AUTH_FAILED:
                raw_output = f"The claude CLI is not signed in ({e}). Run `claude auth login` in a terminal."
                status_code = 401
            else:
                raw_output = str(e)
                status_code = 502
            logger.error(f"[{client_ip}] Claude API error ({e.code}): {e}")
        except WorkerError as e:
            raw_output = str(e)
            logger.error(f"[{client_ip}] Warm worker failed: {e}")
            status_code = 500
        except FileNotFoundError:
            raw_output = f"Executable '{tool}' not found in system PATH."
            logger.error(f"Command not found: '{tool}'")
            status_code = 500
        except Exception as e:
            raw_output = f"Execution error: {str(e)}"
            logger.exception(f"Unexpected error while executing CLI agent: {e}")
            status_code = 500

        exec_duration = (time.time() - exec_start) * 1000
        if status_code == 200:
            clean_text, reason = extract_completion(raw_output, prefix, suffix)
            if reason in EXPLANATION_REASONS:
                logger.warning(f"[{client_ip}] Discarded a reply that is not a tagged completion ({reason}): {raw_output[:150]!r}")
            elif reason and reason != "empty":
                logger.info(f"[{client_ip}] Completion {'shortened' if clean_text else 'dropped'} ({reason})")
        else:
            clean_text = raw_output.strip()

        # 4. Format OpenAI Response
        stream = bool(data.get("stream", False))
        created = int(time.time())

        try:
            if status_code != 200:
                self.send_json(status_code, {"error": {"message": clean_text, "type": error_type}})
            elif stream:
                self.send_sse(self.build_chunks(requested_model, created, clean_text))
            else:
                self.send_json(status_code, self.build_response(requested_model, created, clean_text))

            total_duration = (time.time() - start_time) * 1000
            logger.info(
                f"[{client_ip}] Responded {status_code} in {total_duration:.1f}ms "
                f"(stream: {stream}, agent exec: {exec_duration:.1f}ms, completion len: {len(clean_text)} chars)"
            )
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            logger.warning(f"[{client_ip}] Client aborted/timed out before response could be delivered.")

    @staticmethod
    def build_response(model: str, created: int, text: str) -> dict:
        return {
            "id": f"cmpl-{created}",
            "object": "text_completion",
            "created": created,
            "model": model,
            "choices": [{
                "text": text,
                "index": 0,
                "logprobs": None,
                "finish_reason": "stop"
            }],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        }

    @staticmethod
    def build_chunks(model: str, created: int, text: str) -> list:
        base = {"id": f"cmpl-{created}", "object": "text_completion", "created": created, "model": model}
        return [
            {**base, "choices": [{"text": text, "index": 0, "logprobs": None, "finish_reason": None}]},
            {**base, "choices": [{"text": "", "index": 0, "logprobs": None, "finish_reason": "stop"}]},
        ]

    def send_json(self, status_code: int, payload: dict):
        logger.debug(f"Response {status_code} JSON:\n{json.dumps(payload, indent=2, ensure_ascii=False)}")
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_sse(self, chunks: list):
        logger.debug(f"Response 200 SSE chunks:\n{json.dumps(chunks, indent=2, ensure_ascii=False)}")
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        for chunk in chunks:
            self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode("utf-8"))
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()

    def renew_lease(self, body: bytes):
        try:
            client = json.loads(body.decode("utf-8")).get("client")
        except (ValueError, AttributeError):
            client = None
        if not isinstance(client, str) or not client:
            self.send_json(400, {"error": {"message": "The lease request must contain a 'client' string.", "type": "bridge_error"}})
            return
        count = leases.renew(client)
        logger.debug(f"[{self.client_address[0]}] Renewed lease of client '{client}' ({count} active)")
        self.send_json(200, {"leases": count, "managed": MANAGED})

    def do_DELETE(self):
        threading.current_thread().name = f"req-{next(request_counter)}"
        path = self.path.split("?", 1)[0].rstrip("/")
        if not path.startswith(LEASE_PATH + "/"):
            self.send_json(404, {"error": {"message": f"{self.path} is not supported.", "type": "bridge_error"}})
            return
        client = path[len(LEASE_PATH) + 1:]
        count = leases.release(client)
        logger.info(f"[{self.client_address[0]}] Released lease of client '{client}' ({count} active)")
        self.send_json(200, {"leases": count, "managed": MANAGED})

    def do_GET(self):
        threading.current_thread().name = f"req-{next(request_counter)}"
        logger.info(f"[{self.client_address[0]}] GET {self.path} (health check)")
        self.send_json(200, {"status": "ok", "managed": MANAGED, "leases": leases.count()})

    def log_message(self, format, *args):
        # Suppress default HTTPServer access log so it doesn't double-log
        return


if __name__ == "__main__":
    server = BridgeServer(("127.0.0.1", PORT), CLIBridgeHandler)
    logger.info(f"CLI Bridge initialized and listening on http://127.0.0.1:{PORT}")
    if FAKE_COMPLETION is not None:
        logger.warning(f"Fake autocomplete mode: /v1/completions returns {FAKE_COMPLETION!r} after {FAKE_DELAY_MS}ms")
    for requested in PREWARM:
        if get_pool(*parse_model(requested)) is None:
            logger.warning(f"BRIDGE_PREWARM entry '{requested}' ignored: only claude models use warm workers")
    if MANAGED:
        logger.info(f"Managed mode: exits after {IDLE_EXIT_SECONDS:.0f}s without client leases")
        threading.Thread(target=watch_leases, args=(server, leases, IDLE_EXIT_SECONDS), name="lease-watch", daemon=True).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Server interrupted. Shutting down...")
    finally:
        server.server_close()
        for pool in list(pools.values()):
            pool.close()
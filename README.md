# Autocomplete bridge

Inline code completion from Claude for people who have a Claude subscription but no API key. The bridge runs the official `claude` CLI, so requests go through the subscription.

## Parts

| Folder | Contents |
|---|---|
| `bridge/` | Python HTTP server. It serves `POST /v1/completions` and keeps warm `claude` workers. |
| `extensions/vscode/` | VS Code extension (planned). It starts the bridge and shows the completions. |
| `configs/` | Configs for other clients: Continue, Zed, Twinny, minuet, Tabby. |

## Bridge

Requirements: Python 3.10 or newer, and the `claude` CLI signed in (`claude auth status`).

```
python bridge/app.py
```

The bridge listens on `http://127.0.0.1:11435`.

| Variable | Default | Purpose |
|---|---|---|
| `BRIDGE_PORT` | `11435` | Port to listen on. |
| `BRIDGE_POOL_SIZE` | `2` | Warm `claude` workers per model. |
| `BRIDGE_PREWARM` | empty | Models to start at launch, for example `claude-haiku`. |
| `BRIDGE_LOG_FILE` | `bridge/bridge.log` | Debug log file. |
| `BRIDGE_MANAGED` | unset | `1` makes the bridge exit when no client holds a lease. The VS Code extension sets it. |
| `BRIDGE_LEASE_TTL_SECONDS` | `60` | A lease expires when the client does not renew it in this time. |
| `BRIDGE_IDLE_EXIT_SECONDS` | `30` | A managed bridge exits after this time without leases. |

### Endpoints

| Request | Purpose |
|---|---|
| `POST /v1/completions` | OpenAI text completion. The prompt uses `<fim_prefix>`, `<fim_suffix>` and `<fim_middle>`. |
| `GET /` | Health check: `{"status", "managed", "leases"}`. |
| `POST /lease` | Body `{"client": "<id>"}`. Creates or renews the lease of a client. |
| `DELETE /lease/<id>` | Releases the lease of a client. |

### Tests

```
python -m unittest discover -s bridge/tests
```

These tests make no Claude requests. `python bridge/tests/eval_corpus.py` measures quality and latency with real requests through the bridge. It sends 56 requests for each model in `configs/continue/config.yaml`.

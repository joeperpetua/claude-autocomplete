# Client configs

These clients can send inline completions to the bridge. Start the bridge first (`python bridge/app.py`). It listens on `http://127.0.0.1:11435`.

Every config uses `claude-haiku`. To use another model, change the model name to `claude-sonnet` or `claude-opus`. Haiku is the only model fast enough for most completions (about 1.05s end to end with a 250ms debounce). See `bridge/tests/eval_corpus.py`.

Use `127.0.0.1`, not `localhost`. The bridge does not listen on IPv6, and some clients resolve `localhost` to `::1`.

The API key can be any string. The bridge does not check it.

## Supported clients

| Client | Editor | Config | Status |
|---|---|---|---|
| Continue | VS Code, JetBrains | [`continue/config.yaml`](continue/config.yaml) | Tested in VS Code. No longer maintained. The JetBrains plugin does not load in 2026.2 IDEs. |
| Zed edit predictions | Zed | [`zed/settings.json`](zed/settings.json) | From source, not tested |
| Twinny | VS Code | [Twinny](#twinny) | From source, not tested |
| minuet-ai.nvim | Neovim | [`neovim/minuet.lua`](neovim/minuet.lua) | From source, not tested |
| minuet-ai.el | Emacs | [`emacs/minuet.el`](emacs/minuet.el) | From source, not tested |
| Tabby server | VS Code, JetBrains, Vim, Eclipse | [`tabby/config.toml`](tabby/config.toml) | From source, not tested |

### Continue

Copy the `models` entries from `continue/config.yaml` into `~/.continue/config.yaml`. Then select the model as the autocomplete model in Continue.

### Zed

Merge `zed/settings.json` into the Zed settings file (`zed: open settings`). Zed has a single edit prediction provider, so it uses one model. Zed sends at most 512 tokens of context around the cursor.

### Twinny

Open Twinny, select **Providers**, then **Add provider**, and enter these values:

| Field | Value |
|---|---|
| Type | Autocomplete |
| Provider | OpenAI-compatible |
| Protocol | http |
| Hostname | 127.0.0.1 |
| Port | 11435 |
| API path | /v1/completions |
| Model | claude-haiku |
| FIM template | starcoder |
| API key | bridge |

Select the `starcoder` template manually. Automatic detection selects the codellama template for `claude-haiku`, and the bridge cannot read that template.

### minuet-ai (Neovim and Emacs)

Put `neovim/minuet.lua` in your Neovim config, or load `emacs/minuet.el` from your Emacs init file. The config makes 1 completion per request instead of the default 3, because each completion is a separate Claude request. It also increases the timeout from 3 to 10 seconds.

### Tabby

Tabby runs its own server between the editor and the bridge. Use it to get completions in clients that only work with Tabby, such as its JetBrains, Vim and Eclipse plugins.

1. Put `tabby/config.toml` in `~/.tabby/config.toml`.
2. Start the Tabby server and point the Tabby editor plugin at it.

Keep `kind = "vllm/completion"`. The `openai/completion` kind sends the text after the cursor in a separate `suffix` field, and the bridge ignores that field.

## Not supported

| Client | Reason |
|---|---|
| CodeGPT (VS Code) | Autocomplete is a paid add-on. Local autocomplete accepts only a fixed list of Ollama models, with a 3000ms delay. The request format is not public. |
| Kilo Code 7.x | Autocomplete accepts only the Kilo gateway, Mistral and Inception, with no custom URL. |
| Cody | Sourcegraph ended Cody Free and Pro in July 2025. Autocomplete needs a Sourcegraph Enterprise login, and the local provider sends Ollama `/api/generate` requests. |
| CodeGeeX | Local Mode sends chat requests to `/v1/chat/completions` with its own `<\|code_prefix\|>` tokens. The bridge serves only `/v1/completions`. |
| llama.vscode | Its OpenAI mode is marked experimental. It sends 2 extra look-ahead requests after each suggestion, which triples the Claude requests. |
| GitHub Copilot | Custom models work for chat only, not for inline completions. |
| Cline, Roo Code | No inline completions. |

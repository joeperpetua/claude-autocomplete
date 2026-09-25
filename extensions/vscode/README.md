# Claude Autocomplete for VS Code

Inline code completions from Claude through your Claude subscription. The extension starts the bridge from this repository by itself.

## Requirements

- Python 3.10 or newer.
- The `claude` CLI from the Claude Code native installer, signed in. Check with `claude auth status`.

## Install

1. Get `claude-autocomplete-<version>.vsix`, or build it (see below).
2. In VS Code, open the Extensions view, select **...**, then **Install from VSIX...**, and select the file.
3. Turn off other inline completion extensions, such as Continue, Codeium or Copilot. They compete for the ghost text.

The status bar shows `Claude haiku` when the bridge is ready. Click it to turn completions on or off.

## Use

- Ghost text appears after you stop typing. Press **Tab** to accept it.
- When the IntelliSense list is open, the first line of the suggestion appears as grey text after the cursor, followed by a `Ctrl+Tab` badge. Press **Ctrl+Tab** to accept the full suggestion, or **Esc** to dismiss it. Tab and Enter still accept the IntelliSense item.
- **Claude Autocomplete: Suggest Here** asks for a suggestion without waiting.

## Settings

| Setting | Default | Purpose |
|---|---|---|
| `claudeAutocomplete.enabled` | `true` | Show completions. |
| `claudeAutocomplete.model` | `claude-haiku` | `claude-haiku`, `claude-sonnet` or `claude-opus`. Haiku is the fastest. |
| `claudeAutocomplete.debounceMs` | `250` | Wait after the last keystroke before a request starts. |
| `claudeAutocomplete.maxPrefixChars` | `4000` | Code before the cursor to send. |
| `claudeAutocomplete.maxSuffixChars` | `1000` | Code after the cursor to send. |
| `claudeAutocomplete.disabledLanguages` | `plaintext`, `markdown`, `scminput` | Languages without completions. |
| `claudeAutocomplete.autoStart` | `true` | Start the bridge when none answers. |
| `claudeAutocomplete.pythonPath` | empty | Python for the bridge. Empty: try `py -3`, `python3`, `python`. |
| `claudeAutocomplete.port` | `11435` | Bridge port. Reload the window after a change. |

## How the bridge runs

- When no bridge answers on the port, the extension checks Python, the `claude` CLI and the sign-in, then starts the bundled bridge.
- All VS Code windows share one bridge. Each window holds a lease on it. The bridge stops 30 seconds after the last window closes.
- **Claude Autocomplete: Show Log** shows each request and its result. **Claude Autocomplete: Open Bridge Log** shows the bridge log.

## Build

```
npm install
npm run typecheck
npm run test:unit
npm run package
```

`npm run package` copies `bridge/app.py` and `bridge/completion.py` from the repository root into the package and writes the `.vsix` file.

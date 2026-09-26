import { strict as assert } from "node:assert";
import { existsSync, readFileSync, statSync } from "node:fs";
import { suite, suiteSetup, suiteTeardown, test } from "mocha";
import * as vscode from "vscode";
import { BridgeClient } from "../../src/client.ts";
import type { ExtensionApi } from "../../src/extension.ts";

const EXTENSION_ID = "joeperpetua.claude-autocomplete";
const CONTEXT_LINE = "const total = people.";
const COMPLETION = requiredEnv("BRIDGE_FAKE_COMPLETION");
const FAKE_DELAY_MS = Number(requiredEnv("BRIDGE_FAKE_DELAY_MS"));
const BRIDGE_LOG = requiredEnv("AUTOCOMPLETE_TEST_BRIDGE_LOG");

function requiredEnv(name: string): string {
  const value = process.env[name];
  if (!value) {
    throw new Error(`${name} is not set. Run the integration tests with npm run test:integration.`);
  }
  return value;
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function waitFor<T>(what: string, timeoutMs: number, probe: () => T | undefined | false | Promise<T | undefined | false>): Promise<T> {
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    const value = await probe();
    if (value) {
      return value;
    }
    if (Date.now() > deadline) {
      throw new Error(`Timed out after ${timeoutMs}ms waiting for ${what}`);
    }
    await sleep(50);
  }
}

function bridgeLogSize(): number {
  return existsSync(BRIDGE_LOG) ? statSync(BRIDGE_LOG).size : 0;
}

function bridgeLogSince(offset: number): string {
  return existsSync(BRIDGE_LOG) ? readFileSync(BRIDGE_LOG).subarray(offset).toString("utf8") : "";
}

suite("Claude Autocomplete with the fake bridge", () => {
  const logLines: string[] = [];
  let api: ExtensionApi;
  let client: BridgeClient;
  let sample: vscode.Uri;
  let original: string;

  function waitForLog(pattern: RegExp, timeoutMs: number): Promise<string> {
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        listener.dispose();
        reject(new Error(`No extension log line matched ${pattern} within ${timeoutMs}ms. Recent lines:\n${logLines.slice(-10).join("\n")}`));
      }, timeoutMs);
      const listener = api.onDidLog((line) => {
        if (pattern.test(line)) {
          clearTimeout(timer);
          listener.dispose();
          resolve(line);
        }
      });
    });
  }

  async function editorAt(line: string): Promise<{ editor: vscode.TextEditor; position: vscode.Position }> {
    await vscode.commands.executeCommand("claudeAutocomplete.dismiss");
    const editor = await vscode.window.showTextDocument(sample);
    const document = editor.document;
    const whole = new vscode.Range(document.positionAt(0), document.positionAt(document.getText().length));
    assert.ok(await editor.edit((builder) => builder.replace(whole, `${original}${line}\n`)), "The test document could not be edited");
    const position = document.positionAt(document.getText().lastIndexOf(line) + line.length);
    editor.selection = new vscode.Selection(position, position);
    return { editor, position };
  }

  suiteSetup(async () => {
    const extension = vscode.extensions.getExtension<ExtensionApi>(EXTENSION_ID);
    assert.ok(extension, `${EXTENSION_ID} is not loaded`);
    api = await extension.activate();
    api.onDidLog((line) => logLines.push(line));
    const leaked = Object.keys(process.env).filter((name) => /^(CLAUDE|ANTHROPIC_|MCP_)/i.test(name));
    assert.deepEqual(leaked, [], "Claude Code session variables reached the test VS Code and would reach the bridge");
    const port = vscode.workspace.getConfiguration("claudeAutocomplete").get<number>("port");
    assert.ok(port && port !== 11435, `The fixture workspace must set a dedicated claudeAutocomplete.port, got ${port}`);
    client = new BridgeClient(port);
    const folder = vscode.workspace.workspaceFolders?.[0];
    assert.ok(folder, "The fixture workspace is not open");
    sample = vscode.Uri.joinPath(folder.uri, "sample.ts");
    original = (await vscode.workspace.openTextDocument(sample)).getText();
  });

  suiteTeardown(async () => {
    await vscode.commands.executeCommand("claudeAutocomplete.dismiss");
    if (vscode.window.activeTextEditor?.document.isDirty) {
      await vscode.commands.executeCommand("workbench.action.files.revert");
    }
    await vscode.commands.executeCommand("workbench.action.closeAllEditors");
  });

  test("starts the bundled bridge when none answers on the port", async function () {
    this.timeout(60000);
    const health = await waitFor("a managed bridge with a lease", 50000, async () => {
      const current = await client.health();
      return current && current.managed && current.leases >= 1 ? current : undefined;
    });
    assert.equal(health.status, "ok");
    await waitFor("the bridge state to become ready", 5000, () => api.bridgeState() === "ready");
    assert.match(readFileSync(BRIDGE_LOG, "utf8"), /Fake autocomplete mode/, "The bridge log does not come from the bridge this extension started");
  });

  test("shows ghost text and accepts it", async () => {
    const { editor, position } = await editorAt(CONTEXT_LINE);
    const shown = waitForLog(/ shown as ghost text$/, 10000);
    await vscode.commands.executeCommand("editor.action.inlineSuggest.trigger");
    await shown;
    await waitFor("the ghost text to be committed", 5000, async () => {
      await vscode.commands.executeCommand("editor.action.inlineSuggest.commit");
      return editor.document.lineAt(position.line).text === `${CONTEXT_LINE}${COMPLETION}`;
    });
  });

  test("shows a preview next to the IntelliSense list and accepts it with the accept command", async () => {
    const { editor, position } = await editorAt(CONTEXT_LINE);
    const shown = waitForLog(/ shown as preview next to the IntelliSense list$/, 20000);
    await vscode.commands.executeCommand("editor.action.triggerSuggest");
    await shown;
    await vscode.commands.executeCommand("claudeAutocomplete.accept");
    assert.equal(editor.document.lineAt(position.line).text, `${CONTEXT_LINE}${COMPLETION}`);
  });

  test("aborts the bridge request when typing before it finishes", async () => {
    await editorAt(CONTEXT_LINE);
    const offset = bridgeLogSize();
    const dropped = waitForLog(/ dropped \(document changed\)$/, 10000);
    // The trigger command resolves only after the provider answered, so it must not be awaited before typing.
    const triggered = vscode.commands.executeCommand("editor.action.inlineSuggest.trigger");
    await waitFor("the bridge to receive the request", 5000, () => bridgeLogSince(offset).includes("Received POST /v1/completions"));
    await vscode.commands.executeCommand("type", { text: "l" });
    await dropped;
    await triggered;
    const abort = await waitFor("the bridge to log the abort", 5000, () => /Client aborted after ([\d.]+)ms/.exec(bridgeLogSince(offset)) ?? undefined);
    assert.ok(Number(abort[1]) < FAKE_DELAY_MS, `The bridge request ran for ${abort[1]}ms, the fake delay is ${FAKE_DELAY_MS}ms`);
  });
});

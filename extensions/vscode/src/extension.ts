import * as vscode from "vscode";
import { BridgeClient } from "./client.ts";
import { BridgeManager } from "./bridgeManager.ts";
import type { BridgeState } from "./bridgeManager.ts";
import { checkClaude, preflight } from "./preflight.ts";
import { bridgeLogFile, runCommand, spawnBridge } from "./processes.ts";
import { CompletionProvider } from "./provider.ts";
import type { Settings } from "./provider.ts";
import { StatusBar } from "./statusBar.ts";

const SECTION = "claudeAutocomplete";
const LEASE_RENEW_MS = 20000;

let manager: BridgeManager | undefined;

export interface ExtensionApi {
  onDidLog: vscode.Event<string>;
  bridgeState: () => BridgeState;
}

function readSettings(): Settings {
  const config = vscode.workspace.getConfiguration(SECTION);
  return {
    enabled: config.get("enabled", true),
    model: config.get("model", "claude-haiku"),
    debounceMs: config.get("debounceMs", 250),
    maxPrefixChars: config.get("maxPrefixChars", 4000),
    maxSuffixChars: config.get("maxSuffixChars", 1000),
    disabledLanguages: config.get("disabledLanguages", ["plaintext", "markdown", "scminput"]),
  };
}

export function activate(context: vscode.ExtensionContext): ExtensionApi {
  const output = vscode.window.createOutputChannel("Claude Autocomplete", { log: true });
  const logged = new vscode.EventEmitter<string>();
  const log = (message: string) => {
    output.info(message);
    logged.fire(message);
  };
  const config = vscode.workspace.getConfiguration(SECTION);
  const port = config.get("port", 11435);
  const logDir = context.globalStorageUri.fsPath;
  const client = new BridgeClient(port);
  const status = new StatusBar();
  status.update({ enabled: readSettings().enabled, model: readSettings().model });

  manager = new BridgeManager({
    clientId: `${vscode.env.sessionId}-${process.pid}`,
    health: () => client.health(),
    renewLease: (id) => client.renewLease(id),
    releaseLease: (id) => client.releaseLease(id),
    preflight: () => preflight(runCommand, vscode.workspace.getConfiguration(SECTION).get("pythonPath", ""), process.platform),
    checkAuth: () => checkClaude(runCommand),
    spawnBridge: (python) => spawnBridge({
      python,
      script: context.asAbsolutePath("bridge/app.py"),
      port,
      model: readSettings().model,
      logDir,
    }),
    autoStart: () => vscode.workspace.getConfiguration(SECTION).get("autoStart", true),
    log,
  });
  const bridge = manager;

  let lastProblem: string | undefined;
  bridge.onChange((state, problem) => {
    status.update({ bridge: state, problem });
    if (state === "unavailable" && problem && problem !== lastProblem) {
      lastProblem = problem;
      void vscode.window.showWarningMessage(`Claude Autocomplete: ${problem}`, "Retry", "Show Log").then((choice) => {
        if (choice === "Retry") {
          void bridge.ensureRunning(true);
        } else if (choice === "Show Log") {
          output.show();
        }
      });
    }
    if (state === "ready") {
      lastProblem = undefined;
    }
  });

  const provider = new CompletionProvider({
    client,
    manager: bridge,
    settings: readSettings,
    log,
    onActivity: (activity) => status.update({ activity }),
  });

  const leaseTimer = setInterval(() => void bridge.renew(), LEASE_RENEW_MS);

  context.subscriptions.push(
    output,
    logged,
    status,
    provider,
    { dispose: () => clearInterval(leaseTimer) },
    vscode.languages.registerInlineCompletionItemProvider([{ scheme: "file" }, { scheme: "untitled" }], provider),
    vscode.commands.registerCommand("claudeAutocomplete.toggle", async () => {
      const enabled = !readSettings().enabled;
      await vscode.workspace.getConfiguration(SECTION).update("enabled", enabled, vscode.ConfigurationTarget.Global);
    }),
    vscode.commands.registerCommand("claudeAutocomplete.trigger", () => provider.triggerManually()),
    vscode.commands.registerCommand("claudeAutocomplete.accept", () => provider.accept()),
    vscode.commands.registerCommand("claudeAutocomplete.dismiss", () => provider.dismiss()),
    vscode.commands.registerCommand("claudeAutocomplete.retryBridge", () => bridge.ensureRunning(true)),
    vscode.commands.registerCommand("claudeAutocomplete.showLog", () => output.show()),
    vscode.commands.registerCommand("claudeAutocomplete.openBridgeLog", async () => {
      try {
        await vscode.window.showTextDocument(vscode.Uri.file(bridgeLogFile(logDir)));
      } catch {
        void vscode.window.showInformationMessage("No bridge log yet. The log exists after this extension started a bridge.");
      }
    }),
    vscode.workspace.onDidChangeConfiguration((event) => {
      if (event.affectsConfiguration(SECTION)) {
        const settings = readSettings();
        status.update({ enabled: settings.enabled, model: settings.model });
      }
      if (event.affectsConfiguration(`${SECTION}.port`)) {
        void vscode.window.showInformationMessage("Claude Autocomplete: reload the window to use the new port.");
      }
    }),
  );

  log(`Activated. Bridge port ${port}, model ${readSettings().model}`);
  void bridge.ensureRunning();
  return { onDidLog: logged.event, bridgeState: () => bridge.state };
}

export async function deactivate(): Promise<void> {
  await manager?.dispose();
  manager = undefined;
}

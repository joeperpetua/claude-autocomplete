import * as vscode from "vscode";
import type { BridgeState } from "./bridgeManager.ts";
import type { Activity } from "./provider.ts";

export class StatusBar implements vscode.Disposable {
  private readonly item: vscode.StatusBarItem;
  private enabled = true;
  private model = "";
  private bridge: BridgeState = "unknown";
  private problem: string | undefined;
  private activity: Activity = "idle";

  constructor() {
    this.item = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 100);
    this.item.command = "claudeAutocomplete.toggle";
    this.render();
    this.item.show();
  }

  update(changes: { enabled?: boolean; model?: string; bridge?: BridgeState; problem?: string; activity?: Activity }): void {
    if (changes.enabled !== undefined) {
      this.enabled = changes.enabled;
    }
    if (changes.model !== undefined) {
      this.model = changes.model;
    }
    if (changes.bridge !== undefined) {
      this.bridge = changes.bridge;
      this.problem = changes.problem;
    }
    if (changes.activity !== undefined) {
      this.activity = changes.activity;
    }
    this.render();
  }

  dispose(): void {
    this.item.dispose();
  }

  private render(): void {
    const model = this.model.replace(/^claude-/, "");
    if (!this.enabled) {
      this.item.text = "$(circle-slash) Claude";
      this.item.tooltip = "Claude Autocomplete is off. Click to turn it on.";
      this.item.backgroundColor = undefined;
      return;
    }
    if (this.bridge === "unavailable") {
      this.item.text = "$(warning) Claude";
      this.item.tooltip = `Bridge unavailable: ${this.problem ?? "unknown problem"}\nRun "Claude Autocomplete: Retry Bridge Start" after fixing it.`;
      this.item.backgroundColor = new vscode.ThemeColor("statusBarItem.warningBackground");
      return;
    }
    this.item.backgroundColor = undefined;
    if (this.bridge === "starting" || this.bridge === "unknown") {
      this.item.text = "$(loading~spin) Claude";
      this.item.tooltip = "Starting the bridge.";
      return;
    }
    this.item.text = this.activity === "requesting" ? `$(sync~spin) Claude ${model}` : `$(sparkle) Claude ${model}`;
    this.item.tooltip = `Claude Autocomplete with ${this.model}. Click to turn it off.`;
  }
}

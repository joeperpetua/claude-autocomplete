import * as vscode from "vscode";
import { isAbortError, isAuthError, isConnectionError } from "./client.ts";
import type { BridgeClient } from "./client.ts";
import type { BridgeManager } from "./bridgeManager.ts";
import { buildPrompt } from "./context.ts";
import { previewParts } from "./preview.ts";

export const SUGGESTION_CONTEXT_KEY = "claudeAutocomplete.hasSuggestion";
const PREVIEW_FALLBACK_MS = 300;

export interface Settings {
  enabled: boolean;
  model: string;
  debounceMs: number;
  maxPrefixChars: number;
  maxSuffixChars: number;
  disabledLanguages: string[];
}

export type Activity = "idle" | "requesting";

interface Suggestion {
  id: number;
  key: string;
  document: vscode.TextDocument;
  position: vscode.Position;
  controller: AbortController;
  promise: Promise<void>;
  startedAt: number;
  done: boolean;
  text: string;
  previewShown: boolean;
  ghostShown: boolean;
  logged: boolean;
}

export interface ProviderDeps {
  client: BridgeClient;
  manager: BridgeManager;
  settings: () => Settings;
  log: (message: string) => void;
  onActivity: (activity: Activity) => void;
}

function keyOf(document: vscode.TextDocument, position: vscode.Position): string {
  return `${document.uri.toString()}|${document.version}|${position.line}:${position.character}`;
}

function sleep(ms: number, token: vscode.CancellationToken): Promise<boolean> {
  return new Promise((resolve) => {
    if (token.isCancellationRequested) {
      resolve(false);
      return;
    }
    const timer = setTimeout(() => {
      listener.dispose();
      resolve(true);
    }, ms);
    const listener = token.onCancellationRequested(() => {
      clearTimeout(timer);
      listener.dispose();
      resolve(false);
    });
  });
}

function waitForCancel(token: vscode.CancellationToken): Promise<void> {
  return new Promise((resolve) => {
    if (token.isCancellationRequested) {
      resolve();
      return;
    }
    const listener = token.onCancellationRequested(() => {
      listener.dispose();
      resolve();
    });
  });
}

export class CompletionProvider implements vscode.InlineCompletionItemProvider, vscode.Disposable {
  private readonly deps: ProviderDeps;
  private readonly previewDecoration: vscode.TextEditorDecorationType;
  private readonly badgeDecoration: vscode.TextEditorDecorationType;
  private readonly disposables: vscode.Disposable[] = [];
  private current: Suggestion | undefined;
  private nextId = 1;

  constructor(deps: ProviderDeps) {
    this.deps = deps;
    this.previewDecoration = vscode.window.createTextEditorDecorationType({
      after: { color: new vscode.ThemeColor("editorGhostText.foreground"), fontStyle: "italic" },
    });
    this.badgeDecoration = vscode.window.createTextEditorDecorationType({
      after: {
        color: new vscode.ThemeColor("badge.foreground"),
        backgroundColor: new vscode.ThemeColor("badge.background"),
        fontStyle: "normal",
        fontWeight: "600",
        margin: "0 0 0 0.6em",
        textDecoration: "none; border-radius: 3px; padding: 0 0.45em; font-size: 0.85em; vertical-align: middle",
      },
    });
    this.disposables.push(
      this.previewDecoration,
      this.badgeDecoration,
      vscode.workspace.onDidChangeTextDocument((event) => {
        if (this.current && event.document === this.current.document && event.contentChanges.length > 0) {
          this.drop("document changed");
        }
      }),
      vscode.window.onDidChangeTextEditorSelection((event) => {
        const current = this.current;
        if (current && event.textEditor.document === current.document && !event.selections[0].active.isEqual(current.position)) {
          this.drop("cursor moved");
        }
      }),
      vscode.window.onDidChangeActiveTextEditor(() => this.drop("editor changed")),
    );
  }

  async provideInlineCompletionItems(
    document: vscode.TextDocument,
    position: vscode.Position,
    context: vscode.InlineCompletionContext,
    token: vscode.CancellationToken,
  ): Promise<vscode.InlineCompletionItem[]> {
    const settings = this.deps.settings();
    if (!settings.enabled || settings.disabledLanguages.includes(document.languageId)) {
      return [];
    }
    if (this.deps.manager.state !== "ready") {
      void this.deps.manager.ensureRunning();
      return [];
    }

    const key = keyOf(document, position);
    if (this.current && this.current.key === key && this.current.done) {
      return this.answer(this.current, position, context);
    }

    let suggestion = this.current && this.current.key === key ? this.current : undefined;
    if (!suggestion) {
      const manual = context.triggerKind === vscode.InlineCompletionTriggerKind.Invoke;
      if (!manual && !(await sleep(settings.debounceMs, token))) {
        return [];
      }
      suggestion = this.start(document, position, key, settings);
    }

    const winner = await Promise.race([
      suggestion.promise.then(() => "done" as const),
      waitForCancel(token).then(() => "cancelled" as const),
    ]);
    if (winner === "cancelled") {
      const pending = suggestion;
      pending.promise.then(() => setTimeout(() => this.requery(pending), 0));
      return [];
    }
    if (this.current !== suggestion || !suggestion.done) {
      return [];
    }
    return this.answer(suggestion, position, context);
  }

  triggerManually(): void {
    this.drop("manual trigger");
    void vscode.commands.executeCommand("editor.action.inlineSuggest.trigger");
  }

  async accept(): Promise<void> {
    const editor = vscode.window.activeTextEditor;
    const suggestion = this.current;
    if (!suggestion || !suggestion.done || !suggestion.text || !editor || editor.document !== suggestion.document) {
      return;
    }
    this.deps.log(`#${suggestion.id} accepted with Ctrl+Tab`);
    this.current = undefined;
    this.hidePreview();
    await vscode.commands.executeCommand("hideSuggestWidget");
    await editor.edit((builder) => builder.insert(suggestion.position, suggestion.text));
  }

  async dismiss(): Promise<void> {
    this.drop("dismissed");
    await vscode.commands.executeCommand("hideSuggestWidget");
  }

  dispose(): void {
    this.drop("extension stopped");
    for (const disposable of this.disposables) {
      disposable.dispose();
    }
  }

  private start(document: vscode.TextDocument, position: vscode.Position, key: string, settings: Settings): Suggestion {
    this.drop("new request");
    const offset = document.offsetAt(position);
    const before = document.getText(new vscode.Range(document.positionAt(Math.max(0, offset - settings.maxPrefixChars - 1)), position));
    const after = document.getText(new vscode.Range(position, document.positionAt(offset + settings.maxSuffixChars + 1)));
    const prompt = buildPrompt({
      relativePath: vscode.workspace.asRelativePath(document.uri, false),
      before,
      after,
      maxPrefixChars: settings.maxPrefixChars,
      maxSuffixChars: settings.maxSuffixChars,
    });
    const controller = new AbortController();
    const suggestion: Suggestion = {
      id: this.nextId++,
      key,
      document,
      position,
      controller,
      promise: Promise.resolve(),
      startedAt: Date.now(),
      done: false,
      text: "",
      previewShown: false,
      ghostShown: false,
      logged: false,
    };
    this.current = suggestion;
    this.deps.onActivity("requesting");
    suggestion.promise = this.deps.client.complete(settings.model, prompt, controller.signal).then(
      (text) => {
        if (this.current === suggestion) {
          suggestion.done = true;
          suggestion.text = text;
          if (!text) {
            this.finish(suggestion, "empty");
          }
        }
      },
      (error: unknown) => {
        if (isAbortError(error)) {
          return;
        }
        if (isConnectionError(error)) {
          this.deps.manager.reportConnectionFailure();
        } else if (isAuthError(error)) {
          this.deps.manager.reportAuthFailure();
        }
        this.finish(suggestion, `error: ${error instanceof Error ? error.message : String(error)}`);
        if (this.current === suggestion) {
          this.current = undefined;
        }
      },
    ).finally(() => {
      if (!this.current || this.current === suggestion) {
        this.deps.onActivity("idle");
      }
    });
    return suggestion;
  }

  private answer(suggestion: Suggestion, position: vscode.Position, context: vscode.InlineCompletionContext): vscode.InlineCompletionItem[] {
    if (!suggestion.text) {
      return [];
    }
    if (context.selectedCompletionInfo) {
      this.showPreview(suggestion);
      return [];
    }
    this.hidePreview();
    suggestion.previewShown = false;
    suggestion.ghostShown = true;
    this.finish(suggestion, "shown as ghost text");
    return [new vscode.InlineCompletionItem(suggestion.text, new vscode.Range(position, position))];
  }

  private requery(suggestion: Suggestion): void {
    if (this.current !== suggestion || !suggestion.done || !suggestion.text) {
      return;
    }
    const editor = vscode.window.activeTextEditor;
    if (!editor || editor.document !== suggestion.document || !editor.selection.active.isEqual(suggestion.position)) {
      this.drop("cursor or document changed before the result arrived");
      return;
    }
    void vscode.commands.executeCommand("editor.action.inlineSuggest.trigger");
    setTimeout(() => {
      if (this.current === suggestion && !suggestion.previewShown && !suggestion.ghostShown) {
        this.showPreview(suggestion);
      }
    }, PREVIEW_FALLBACK_MS);
  }

  private showPreview(suggestion: Suggestion): void {
    const editor = vscode.window.activeTextEditor;
    if (this.current !== suggestion || !editor || editor.document !== suggestion.document) {
      return;
    }
    suggestion.previewShown = true;
    const range = new vscode.Range(suggestion.position, suggestion.position);
    const { line, badge } = previewParts(suggestion.text);
    editor.setDecorations(this.previewDecoration, line ? [{ range, renderOptions: { after: { contentText: line } } }] : []);
    editor.setDecorations(this.badgeDecoration, [{ range, renderOptions: { after: { contentText: badge } } }]);
    void vscode.commands.executeCommand("setContext", SUGGESTION_CONTEXT_KEY, true);
    this.finish(suggestion, "shown as preview next to the IntelliSense list");
  }

  private hidePreview(): void {
    for (const editor of vscode.window.visibleTextEditors) {
      editor.setDecorations(this.previewDecoration, []);
      editor.setDecorations(this.badgeDecoration, []);
    }
    void vscode.commands.executeCommand("setContext", SUGGESTION_CONTEXT_KEY, false);
  }

  private drop(reason: string): void {
    const suggestion = this.current;
    if (!suggestion) {
      return;
    }
    this.current = undefined;
    suggestion.controller.abort();
    this.hidePreview();
    this.finish(suggestion, `dropped (${reason})`);
    this.deps.onActivity("idle");
  }

  private finish(suggestion: Suggestion, result: string): void {
    if (suggestion.logged) {
      return;
    }
    suggestion.logged = true;
    const position = `${suggestion.position.line + 1}:${suggestion.position.character + 1}`;
    const name = vscode.workspace.asRelativePath(suggestion.document.uri, false);
    this.deps.log(`#${suggestion.id} ${name}:${position} ${Date.now() - suggestion.startedAt}ms ${result}`);
  }
}

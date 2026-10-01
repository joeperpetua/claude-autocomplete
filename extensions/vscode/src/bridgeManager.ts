import type { Health } from "./client.ts";
import { NOT_SIGNED_IN } from "./preflight.ts";
import type { PreflightResult } from "./preflight.ts";

export type BridgeState = "unknown" | "starting" | "ready" | "unavailable";

export interface BridgeManagerDeps {
  clientId: string;
  health: () => Promise<Health | undefined>;
  renewLease: (clientId: string) => Promise<boolean>;
  releaseLease: (clientId: string) => Promise<void>;
  preflight: () => Promise<PreflightResult>;
  checkAuth: () => Promise<string | undefined>;
  spawnBridge: (python: string[]) => Promise<void>;
  autoStart: () => boolean;
  log: (message: string) => void;
  sleep?: (ms: number) => Promise<void>;
  now?: () => number;
  startTimeoutMs?: number;
  pollMs?: number;
  retryAfterMs?: number;
}

export type StateListener = (state: BridgeState, problem: string | undefined) => void;

export class BridgeManager {
  state: BridgeState = "unknown";
  problem: string | undefined;
  private readonly deps: BridgeManagerDeps;
  private readonly sleep: (ms: number) => Promise<void>;
  private readonly now: () => number;
  private readonly listeners: StateListener[] = [];
  private pending: Promise<boolean> | undefined;
  private failedAt: number | undefined;
  private signedOut = false;

  constructor(deps: BridgeManagerDeps) {
    this.deps = deps;
    this.sleep = deps.sleep ?? ((ms) => new Promise((resolve) => setTimeout(resolve, ms)));
    this.now = deps.now ?? Date.now;
  }

  onChange(listener: StateListener): void {
    this.listeners.push(listener);
  }

  ensureRunning(force = false): Promise<boolean> {
    if (this.pending) {
      return this.pending;
    }
    const retryAfter = this.deps.retryAfterMs ?? 30000;
    if (!force && this.state === "unavailable" && this.failedAt !== undefined && this.now() - this.failedAt < retryAfter) {
      return Promise.resolve(false);
    }
    this.pending = this.start().finally(() => {
      this.pending = undefined;
    });
    return this.pending;
  }

  async renew(): Promise<void> {
    if (this.pending) {
      return;
    }
    if (await this.deps.renewLease(this.deps.clientId)) {
      if (this.state !== "ready" && !this.signedOut) {
        this.setState("ready", undefined);
      }
      return;
    }
    this.deps.log("Lease renewal failed, checking the bridge");
    this.setState("unknown", undefined);
    await this.ensureRunning();
  }

  reportConnectionFailure(): void {
    if (this.state === "ready") {
      this.deps.log("A request could not reach the bridge, checking the bridge");
      this.setState("unknown", undefined);
    }
    void this.ensureRunning();
  }

  reportAuthFailure(): void {
    this.signedOut = true;
    this.fail(NOT_SIGNED_IN);
  }

  async dispose(): Promise<void> {
    await this.deps.releaseLease(this.deps.clientId);
  }

  private async start(): Promise<boolean> {
    if (this.signedOut) {
      const problem = await this.deps.checkAuth();
      if (problem) {
        return this.fail(problem);
      }
      this.signedOut = false;
    }
    if (await this.deps.health()) {
      return this.becomeReady("found a running bridge");
    }
    if (!this.deps.autoStart()) {
      return this.fail("No bridge answers and claudeAutocomplete.autoStart is off. Start it with `python bridge/app.py`.");
    }
    this.setState("starting", undefined);
    this.deps.log("No bridge answers, checking Python and the claude CLI");
    const checked = await this.deps.preflight();
    if (!checked.ok) {
      return this.fail(checked.problem);
    }
    this.deps.log(`Starting the bridge with ${checked.python.join(" ")}`);
    try {
      await this.deps.spawnBridge(checked.python);
    } catch (error) {
      return this.fail(`The bridge process could not start: ${error instanceof Error ? error.message : String(error)}`);
    }
    const timeout = this.deps.startTimeoutMs ?? 20000;
    const poll = this.deps.pollMs ?? 200;
    const deadline = this.now() + timeout;
    while (this.now() < deadline) {
      await this.sleep(poll);
      if (await this.deps.health()) {
        return this.becomeReady("started the bridge");
      }
    }
    return this.fail(`The bridge did not answer within ${Math.round(timeout / 1000)}s. See the bridge log.`);
  }

  private async becomeReady(reason: string): Promise<boolean> {
    await this.deps.renewLease(this.deps.clientId);
    this.failedAt = undefined;
    this.deps.log(`Bridge ready (${reason})`);
    this.setState("ready", undefined);
    return true;
  }

  private fail(problem: string): boolean {
    this.failedAt = this.now();
    this.deps.log(`Bridge unavailable: ${problem}`);
    this.setState("unavailable", problem);
    return false;
  }

  private setState(state: BridgeState, problem: string | undefined): void {
    const changed = state !== this.state || problem !== this.problem;
    this.state = state;
    this.problem = problem;
    if (changed) {
      for (const listener of this.listeners) {
        listener(state, problem);
      }
    }
  }
}

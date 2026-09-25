import assert from "node:assert/strict";
import { test } from "node:test";
import { BridgeManager } from "../../src/bridgeManager.ts";
import type { BridgeManagerDeps, BridgeState } from "../../src/bridgeManager.ts";
import type { PreflightResult } from "../../src/preflight.ts";

interface Harness {
  manager: BridgeManager;
  events: string[];
  states: BridgeState[];
  setHealthy: (healthy: boolean) => void;
  advance: (ms: number) => void;
}

function harness(overrides: Partial<BridgeManagerDeps> & { healthy?: boolean; healthyAfterSpawn?: boolean; check?: PreflightResult } = {}): Harness {
  let healthy = overrides.healthy ?? false;
  let clock = 0;
  const events: string[] = [];
  const states: BridgeState[] = [];
  const manager = new BridgeManager({
    clientId: "w1",
    health: async () => {
      events.push("health");
      return healthy ? { status: "ok", managed: true, leases: 0 } : undefined;
    },
    renewLease: async (id) => {
      events.push(`renew ${id}`);
      return healthy;
    },
    releaseLease: async (id) => {
      events.push(`release ${id}`);
    },
    preflight: async () => {
      events.push("preflight");
      return overrides.check ?? { ok: true, python: ["py", "-3"] };
    },
    spawnBridge: async (python) => {
      events.push(`spawn ${python.join(" ")}`);
      if (overrides.healthyAfterSpawn ?? true) {
        healthy = true;
      }
    },
    autoStart: () => true,
    log: () => undefined,
    sleep: async (ms) => {
      clock += ms;
    },
    now: () => clock,
    startTimeoutMs: 1000,
    pollMs: 100,
    retryAfterMs: 30000,
    ...overrides,
  });
  manager.onChange((state) => states.push(state));
  return {
    manager,
    events,
    states,
    setHealthy: (value) => {
      healthy = value;
    },
    advance: (ms) => {
      clock += ms;
    },
  };
}

test("uses a running bridge without spawning", async () => {
  const { manager, events, states } = harness({ healthy: true });
  assert.equal(await manager.ensureRunning(), true);
  assert.deepEqual(events, ["health", "renew w1"]);
  assert.deepEqual(states, ["ready"]);
});

test("starts a bridge when none answers", async () => {
  const { manager, events, states } = harness();
  assert.equal(await manager.ensureRunning(), true);
  assert.deepEqual(events, ["health", "preflight", "spawn py -3", "health", "renew w1"]);
  assert.deepEqual(states, ["starting", "ready"]);
});

test("reports the preflight problem and does not spawn", async () => {
  const { manager, events, states } = harness({ check: { ok: false, problem: "The claude CLI is not signed in." } });
  assert.equal(await manager.ensureRunning(), false);
  assert.equal(events.some((event) => event.startsWith("spawn")), false);
  assert.deepEqual(states, ["starting", "unavailable"]);
  assert.equal(manager.problem, "The claude CLI is not signed in.");
});

test("gives up when the spawned bridge never answers", async () => {
  const { manager } = harness({ healthyAfterSpawn: false });
  assert.equal(await manager.ensureRunning(), false);
  assert.match(manager.problem ?? "", /did not answer within 1s/);
});

test("reports a spawn error", async () => {
  const { manager } = harness({ spawnBridge: async () => { throw new Error("spawn py ENOENT"); } });
  assert.equal(await manager.ensureRunning(), false);
  assert.match(manager.problem ?? "", /could not start: spawn py ENOENT/);
});

test("does not start when auto start is off", async () => {
  const { manager, events } = harness({ autoStart: () => false });
  assert.equal(await manager.ensureRunning(), false);
  assert.deepEqual(events, ["health"]);
  assert.match(manager.problem ?? "", /autoStart is off/);
});

test("concurrent calls share one start", async () => {
  const { manager, events } = harness();
  const results = await Promise.all([manager.ensureRunning(), manager.ensureRunning(), manager.ensureRunning()]);
  assert.deepEqual(results, [true, true, true]);
  assert.equal(events.filter((event) => event.startsWith("spawn")).length, 1);
});

test("waits before retrying after a failure unless forced", async () => {
  const { manager, events, advance } = harness({ check: { ok: false, problem: "no python" } });
  await manager.ensureRunning();
  const preflights = () => events.filter((event) => event === "preflight").length;
  assert.equal(await manager.ensureRunning(), false);
  assert.equal(preflights(), 1);
  await manager.ensureRunning(true);
  assert.equal(preflights(), 2);
  advance(31000);
  await manager.ensureRunning();
  assert.equal(preflights(), 3);
});

test("a failed lease renewal starts a new bridge", async () => {
  const { manager, events, setHealthy } = harness({ healthy: true });
  await manager.ensureRunning();
  setHealthy(false);
  events.length = 0;
  await manager.renew();
  assert.deepEqual(events, ["renew w1", "health", "preflight", "spawn py -3", "health", "renew w1"]);
  assert.equal(manager.state, "ready");
});

test("a connection failure during a request checks the bridge again", async () => {
  const { manager, events, setHealthy } = harness({ healthy: true });
  await manager.ensureRunning();
  setHealthy(false);
  events.length = 0;
  manager.reportConnectionFailure();
  await manager.ensureRunning();
  assert.ok(events.includes("spawn py -3"));
});

test("dispose releases the lease", async () => {
  const { manager, events } = harness({ healthy: true });
  await manager.dispose();
  assert.deepEqual(events, ["release w1"]);
});

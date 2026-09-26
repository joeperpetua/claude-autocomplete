import { existsSync, readFileSync, rmSync } from "node:fs";
import { createServer } from "node:net";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { runTests } from "@vscode/test-electron";
import { BridgeClient } from "../../src/client.ts";

const VSCODE_VERSION = "1.139.1";
const FAKE_COMPLETION = "length;";
const FAKE_DELAY_MS = 1500;
const IDLE_EXIT_SECONDS = 3;
const LEFTOVER_EXIT_TIMEOUT_MS = 90000;
const IDLE_EXIT_TIMEOUT_MS = 20000;
// A Claude Code session that runs this script exports these; they must not reach VS Code, the bridge or the claude CLI.
const STRIPPED_ENV_PREFIXES = ["CLAUDE", "ANTHROPIC_", "MCP_"];
const STRIPPED_ENV_NAMES = new Set(["USE_LOCAL_OAUTH", "USE_STAGING_OAUTH", "CHROME_CRASHPAD_PIPE_NAME"]);

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..");
const cache = join(root, ".vscode-test");
const userDataDir = join(cache, "user-data");
const fixture = join(root, "test", "integration", "fixture");
const bridgeLog = join(userDataDir, "User", "globalStorage", "joeperpetua.claude-autocomplete", "bridge.log");
const port = Number(JSON.parse(readFileSync(join(fixture, ".vscode", "settings.json"), "utf8"))["claudeAutocomplete.port"]);
const client = new BridgeClient(port);

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function portFree(): Promise<boolean> {
  return new Promise((resolve) => {
    const server = createServer();
    server.once("error", () => resolve(false));
    server.listen(port, "127.0.0.1", () => server.close(() => resolve(true)));
  });
}

async function waitUntilGone(timeoutMs: number): Promise<boolean> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (!(await client.health())) {
      return true;
    }
    await sleep(250);
  }
  return !(await client.health());
}

async function ensurePortIsFree(): Promise<string | undefined> {
  const leftover = await client.health();
  if (leftover && !leftover.managed) {
    return `An unmanaged bridge answers on port ${port}. Stop it before running the integration tests.`;
  }
  if (leftover) {
    console.log(`A managed bridge still answers on port ${port} (${leftover.leases} leases), waiting until it exits by itself`);
    if (!(await waitUntilGone(LEFTOVER_EXIT_TIMEOUT_MS))) {
      return `The managed bridge on port ${port} did not exit within ${LEFTOVER_EXIT_TIMEOUT_MS / 1000}s. Stop it before running the integration tests.`;
    }
  }
  return (await portFree()) ? undefined : `Port ${port} is in use by another program.`;
}

async function checkIdleExit(): Promise<string | undefined> {
  const closedAt = Date.now();
  if (!(await waitUntilGone(IDLE_EXIT_TIMEOUT_MS))) {
    const health = await client.health();
    return `The bridge on port ${port} still answers ${IDLE_EXIT_TIMEOUT_MS / 1000}s after VS Code closed (${health?.leases ?? "?"} leases). It exits by itself when its leases expire.`;
  }
  const log = existsSync(bridgeLog) ? readFileSync(bridgeLog, "utf8") : "";
  if (!log.includes("shutting down the managed bridge")) {
    return `The bridge on port ${port} stopped, but its log does not show an idle exit: ${bridgeLog}`;
  }
  console.log(`Idle exit: the bridge on port ${port} exited ${Date.now() - closedAt}ms after VS Code closed`);
  return undefined;
}

async function main(): Promise<number> {
  if (!Number.isInteger(port) || port === 11435) {
    console.error(`The fixture must set a dedicated claudeAutocomplete.port, got ${port}`);
    return 1;
  }
  const blocked = await ensurePortIsFree();
  if (blocked) {
    console.error(blocked);
    return 1;
  }
  rmSync(userDataDir, { recursive: true, force: true });
  for (const name of Object.keys(process.env)) {
    const upper = name.toUpperCase();
    if (STRIPPED_ENV_PREFIXES.some((prefix) => upper.startsWith(prefix)) || STRIPPED_ENV_NAMES.has(upper)) {
      delete process.env[name];
    }
  }

  const startedAt = Date.now();
  let failed = false;
  try {
    await runTests({
      version: VSCODE_VERSION,
      cachePath: cache,
      extensionDevelopmentPath: root,
      extensionTestsPath: join(root, "out", "integration", "index.js"),
      launchArgs: [
        fixture,
        "--disable-extensions",
        "--disable-telemetry",
        `--user-data-dir=${userDataDir}`,
        `--extensions-dir=${join(cache, "extensions")}`,
      ],
      extensionTestsEnv: {
        BRIDGE_FAKE_COMPLETION: FAKE_COMPLETION,
        BRIDGE_FAKE_DELAY_MS: String(FAKE_DELAY_MS),
        BRIDGE_IDLE_EXIT_SECONDS: String(IDLE_EXIT_SECONDS),
        BRIDGE_POOL_SIZE: "0",
        AUTOCOMPLETE_TEST_BRIDGE_LOG: bridgeLog,
      },
    });
  } catch (error) {
    console.error(error instanceof Error ? error.message : String(error));
    failed = true;
  }
  const idleProblem = await checkIdleExit();
  if (idleProblem) {
    console.error(idleProblem);
    failed = true;
  }
  console.log(`Integration tests ${failed ? "failed" : "passed"} in ${((Date.now() - startedAt) / 1000).toFixed(1)}s`);
  return failed ? 1 : 0;
}

process.exitCode = await main();

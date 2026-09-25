import { execFile, spawn } from "node:child_process";
import { closeSync, mkdirSync, openSync } from "node:fs";
import { dirname, join } from "node:path";
import type { CommandResult } from "./preflight.ts";

export function runCommand(command: string, args: string[], timeoutMs: number, shell = false): Promise<CommandResult> {
  return new Promise((resolve) => {
    execFile(command, args, { timeout: timeoutMs, windowsHide: true, encoding: "utf8", shell }, (error, stdout, stderr) => {
      const code = error ? (typeof error.code === "number" ? error.code : null) : 0;
      resolve({ code, stdout: stdout ?? "", stderr: stderr ?? "" });
    });
  });
}

export interface SpawnOptions {
  python: string[];
  script: string;
  port: number;
  model: string;
  logDir: string;
}

export function bridgeLogFile(logDir: string): string {
  return join(logDir, "bridge.log");
}

export function spawnBridge(options: SpawnOptions): Promise<void> {
  mkdirSync(options.logDir, { recursive: true });
  const consoleLog = openSync(join(options.logDir, "bridge-console.log"), "w");
  return new Promise((resolve, reject) => {
    const child = spawn(options.python[0], [...options.python.slice(1), options.script], {
      cwd: dirname(options.script),
      detached: true,
      stdio: ["ignore", consoleLog, consoleLog],
      windowsHide: true,
      env: {
        ...process.env,
        BRIDGE_PORT: String(options.port),
        BRIDGE_MANAGED: "1",
        BRIDGE_PREWARM: options.model,
        BRIDGE_LOG_FILE: bridgeLogFile(options.logDir),
        PYTHONDONTWRITEBYTECODE: "1",
        PYTHONUNBUFFERED: "1",
      },
    });
    child.once("error", (error) => {
      closeSync(consoleLog);
      reject(error);
    });
    child.once("spawn", () => {
      closeSync(consoleLog);
      child.unref();
      resolve();
    });
  });
}

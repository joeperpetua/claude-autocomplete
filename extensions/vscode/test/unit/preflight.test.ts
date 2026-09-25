import assert from "node:assert/strict";
import { test } from "node:test";
import { findPython, preflight, pythonCandidates } from "../../src/preflight.ts";
import type { CommandResult, RunCommand } from "../../src/preflight.ts";

function fakeRun(answers: Record<string, CommandResult>): { run: RunCommand; commands: string[] } {
  const commands: string[] = [];
  const run: RunCommand = async (command, args, _timeout, shell) => {
    const parts = [command.replace(/ "import sys.*"$/, ""), ...args.filter((arg) => !arg.startsWith("import sys"))];
    const line = `${shell ? "shell " : ""}${parts.join(" ")}`;
    commands.push(line);
    return answers[line] ?? { code: null, stdout: "", stderr: "not found" };
  };
  return { run, commands };
}

const ok = (stdout: string): CommandResult => ({ code: 0, stdout, stderr: "" });
const signedIn = ok(JSON.stringify({ loggedIn: true, authMethod: "claude.ai" }));
const unixPython = ok("/usr/bin/python3\nTrue\n");

test("python candidates per platform", () => {
  assert.deepEqual(pythonCandidates("", "win32"), [["py", "-3"], ["python3"], ["python"]]);
  assert.deepEqual(pythonCandidates("", "linux"), [["python3"], ["python"]]);
  assert.deepEqual(pythonCandidates(" C:\\Python312\\python.exe ", "win32"), [["C:\\Python312\\python.exe"]]);
});

test("findPython skips old interpreters and returns the real executable", async () => {
  const { run, commands } = fakeRun({
    "python3 -c": ok("/usr/bin/python3.8\nFalse\n"),
    "python -c": ok("/usr/bin/python3.12\nTrue\n"),
  });
  assert.deepEqual(await findPython(run, "", "linux"), ["/usr/bin/python3.12"]);
  assert.deepEqual(commands, ["python3 -c", "python -c"]);
});

test("findPython on Windows retries through the shell for .bat shims", async () => {
  const real = "C:\\Users\\me\\.pyenv\\versions\\3.12.10\\python.exe";
  const { run, commands } = fakeRun({
    "python3 -c": { code: 9009, stdout: "", stderr: "store stub" },
    'shell "python3" -c': ok(`${real}\r\nTrue\r\n`),
  });
  assert.deepEqual(await findPython(run, "", "win32"), [real]);
  assert.deepEqual(commands, ["py -3 -c", 'shell "py" -3 -c', "python3 -c", 'shell "python3" -c']);
});

test("findPython does not use the shell outside Windows", async () => {
  const { run, commands } = fakeRun({});
  assert.equal(await findPython(run, "", "linux"), undefined);
  assert.equal(commands.some((command) => command.startsWith("shell")), false);
});

test("preflight succeeds with python, claude and a signed-in account", async () => {
  const { run } = fakeRun({
    "py -3 -c": ok("C:\\Python312\\python.exe\r\nTrue"),
    "claude --version": ok("2.1.281"),
    "claude auth status --json": signedIn,
  });
  assert.deepEqual(await preflight(run, "", "win32"), { ok: true, python: ["C:\\Python312\\python.exe"] });
});

test("preflight reports missing python", async () => {
  const { run } = fakeRun({});
  const result = await preflight(run, "", "linux");
  assert.equal(result.ok, false);
  assert.match(result.ok ? "" : result.problem, /Python 3\.10 or newer was not found \(tried: python3, python\)/);
});

test("preflight reports a missing claude CLI", async () => {
  const { run } = fakeRun({ "python3 -c": unixPython });
  const result = await preflight(run, "", "linux");
  assert.match(result.ok ? "" : result.problem, /claude CLI was not found/);
});

test("preflight reports a signed-out claude CLI", async () => {
  const { run } = fakeRun({
    "python3 -c": unixPython,
    "claude --version": ok("2.1.281"),
    "claude auth status --json": { code: 1, stdout: JSON.stringify({ loggedIn: false }), stderr: "" },
  });
  const result = await preflight(run, "", "linux");
  assert.match(result.ok ? "" : result.problem, /not signed in/);
});

test("preflight treats unreadable auth output as signed out", async () => {
  const { run } = fakeRun({ "python3 -c": unixPython, "claude --version": ok("1"), "claude auth status --json": ok("Logged in") });
  const result = await preflight(run, "", "linux");
  assert.equal(result.ok, false);
});

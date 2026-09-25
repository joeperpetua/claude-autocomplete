export interface CommandResult {
  code: number | null;
  stdout: string;
  stderr: string;
}

export type RunCommand = (command: string, args: string[], timeoutMs: number, shell?: boolean) => Promise<CommandResult>;

export type PreflightResult =
  | { ok: true; python: string[] }
  | { ok: false; problem: string };

const PYTHON_CHECK = "import sys; print(sys.executable); print(sys.version_info >= (3, 10))";

export function pythonCandidates(configured: string, platform: string): string[][] {
  if (configured.trim()) {
    return [[configured.trim()]];
  }
  return platform === "win32" ? [["py", "-3"], ["python3"], ["python"]] : [["python3"], ["python"]];
}

function parsePythonCheck(result: CommandResult): string | undefined {
  if (result.code !== 0) {
    return undefined;
  }
  const [executable, supported] = result.stdout.trim().split(/\r?\n/).map((line) => line.trim());
  return executable && supported === "True" ? executable : undefined;
}

export async function findPython(run: RunCommand, configured: string, platform: string): Promise<string[] | undefined> {
  for (const candidate of pythonCandidates(configured, platform)) {
    const direct = await run(candidate[0], [...candidate.slice(1), "-c", PYTHON_CHECK], 10000);
    const executable = parsePythonCheck(direct)
      ?? (platform === "win32"
        ? parsePythonCheck(await run([`"${candidate[0]}"`, ...candidate.slice(1), "-c", `"${PYTHON_CHECK}"`].join(" "), [], 10000, true))
        : undefined);
    if (executable) {
      return [executable];
    }
  }
  return undefined;
}

export async function checkClaude(run: RunCommand): Promise<string | undefined> {
  const version = await run("claude", ["--version"], 15000);
  if (version.code !== 0) {
    return "The claude CLI was not found as an executable. Install Claude Code with the native installer and make sure `claude` is on the PATH.";
  }
  const status = await run("claude", ["auth", "status", "--json"], 15000);
  let loggedIn = false;
  try {
    loggedIn = JSON.parse(status.stdout).loggedIn === true;
  } catch {
    loggedIn = false;
  }
  return loggedIn ? undefined : "The claude CLI is not signed in. Run `claude auth login` in a terminal.";
}

export async function preflight(run: RunCommand, configuredPython: string, platform: string): Promise<PreflightResult> {
  const python = await findPython(run, configuredPython, platform);
  if (!python) {
    const tried = pythonCandidates(configuredPython, platform).map((candidate) => candidate.join(" ")).join(", ");
    return { ok: false, problem: `Python 3.10 or newer was not found (tried: ${tried}). Install Python or set claudeAutocomplete.pythonPath.` };
  }
  const claudeProblem = await checkClaude(run);
  return claudeProblem ? { ok: false, problem: claudeProblem } : { ok: true, python };
}

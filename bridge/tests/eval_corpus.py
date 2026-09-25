import json
import logging
import math
import os
import re
import statistics
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from collections import Counter, defaultdict

os.environ.setdefault("BRIDGE_LOG_FILE", os.path.join(tempfile.gettempdir(), "autocomplete-bridge-eval.log"))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app

CLOSERS = ")]}"
PROSE_OPENERS = ("looking at", "based on", "i notice", "i can see", "i need", "i'll", "here's", "here is", "the code",
                 "the cursor", "this code", "it looks", "it seems", "to complete", "sure", "sorry")
DEFAULT_CONFIG = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "configs", "continue", "config.yaml")
WORD_END_BUDGET_MS = 750
COMMENT_BY_EXTENSION = {".py": "#", ".rb": "#", ".yml": "#", ".yaml": "#", ".sh": "#", ".sql": "--", ".lua": "--"}


def indent(line):
    expanded = line.expandtabs(4)
    return len(expanded) - len(expanded.lstrip())


def content_lines(text):
    lines = text.split("\n")
    return lines[1:] if text.startswith("\n") else lines


def check_same_line(text, case):
    return bool(text.strip()) and not text.startswith("\n")


def check_new_line(text, case):
    return text.startswith("\n") and bool(text.strip())


def check_new_line_or_empty(text, case):
    return text == "" or text.startswith("\n")


def check_single_line(text, case):
    return "\n" not in text


def check_no_prose(text, case):
    return app.CURSOR not in text and not text.strip().lower().startswith(PROSE_OPENERS)


def check_no_suffix_duplicate(text, case):
    tail, head = text.rstrip(), case["suffix"].lstrip()
    if not tail or not head:
        return True
    if tail[-1] in CLOSERS and head[0] == tail[-1]:
        return tail.count("([{"[CLOSERS.index(tail[-1])]) >= tail.count(tail[-1])
    if tail[-1] == ";" and head[0] == ";":
        return False
    return not any(len(head) >= k and tail.endswith(head[:k]) for k in range(2, min(len(tail), 12) + 1))


def check_balanced(text, case):
    """Check if brackets/parentheses are balanced in the completion text."""
    pending = []
    for char in case["prefix"]:
        if char in "([{":
            pending.append(char)
        elif char in CLOSERS and pending and "([{"[CLOSERS.index(char)] == pending[-1]:
            pending.pop()
    for char in text:
        if char in "([{":
            pending.append(char)
        elif char in CLOSERS:
            if not pending or pending[-1] != "([{"[CLOSERS.index(char)]:
                return False
            pending.pop()
    return True


def optional(check):
    def wrapped(text, case):
        return text == "" or check(text, case)
    wrapped.__name__ = f"optional({check.__name__})"
    return wrapped


def starts_with(expected):
    def check(text, case):
        return text.startswith(expected)
    check.__name__ = f"starts_with({expected!r})"
    return check


def contains_any(*options):
    def check(text, case):
        return any(option in text for option in options)
    check.__name__ = f"contains_any{options!r}"
    return check


def first_line_indent(width):
    def check(text, case):
        lines = [line for line in content_lines(text) if line.strip()]
        return bool(lines) and indent(lines[0]) == width
    check.__name__ = f"first_line_indent({width})"
    return check


def lines_at_least(width, branches=()):
    def check(text, case):
        later = [line for line in (content_lines(text) if text.startswith("\n") else text.split("\n")[1:]) if line.strip()]
        return all(indent(line) >= width or line.split()[0] in branches for line in later)
    check.__name__ = f"lines_at_least({width}, {branches!r})" if branches else f"lines_at_least({width})"
    return check


def uses_tabs(text, case):
    lines = [line for line in content_lines(text) if line.strip() and line[:1].isspace()]
    return bool(lines) and all(line.startswith("\t") for line in lines)


def python_block_structure(text, case):
    opens_block = text.split("\n")[0].rstrip().endswith(":")
    for line in text.split("\n")[1:]:
        if not line.strip():
            continue
        branch = line.strip().startswith(("elif", "else"))
        if indent(line) not in ((8,) if opens_block else (4,) if branch else (4, 8)):
            return False
        opens_block = line.rstrip().endswith(":")
    return True


TS = 'let fullName = "Carl Max";\n\nfunction getNames(fullName: string) {\n  return fullName.split(" ");\n}\n\n'
CASES = [
    {"name": "ts end of line", "file": "names.ts", "prefix": TS + "const names = ", "suffix": "\n",
     "checks": [check_same_line, contains_any("getNames(fullName)")]},
    {"name": "ts mid identifier", "file": "names.ts", "prefix": TS + "const names = getNames", "suffix": "\n",
     "checks": [starts_with("(")]},
    {"name": "ts inside call", "file": "names.ts", "prefix": TS + "const names = getNames(fullName);\nconsole.log(",
     "suffix": ");\n", "checks": [check_single_line, contains_any("names"), check_no_suffix_duplicate]},
    {"name": "ts after brace", "file": "math.ts", "prefix": "function add(a: number, b: number) {", "suffix": "\n}\n",
     "checks": [check_new_line, first_line_indent(2), contains_any("return")]},
    {"name": "tsx element on empty line", "file": "Page.tsx",
     "prefix": "export function Page({ title }: { title: string }) {\n  return (\n    <div className=\"p-4\">\n      <h1>{title}</h1>\n      ",
     "suffix": "\n    </div>\n  );\n}\n", "checks": [optional(check_same_line), optional(starts_with("<")), lines_at_least(6)]},
    {"name": "ts object literal", "file": "config.ts", "prefix": "export function config() {\n  const options = ",
     "suffix": ";\n  return options;\n}\n", "checks": [starts_with("{"), lines_at_least(2), check_no_suffix_duplicate]},
    {"name": "ts after statement", "file": "names.ts", "prefix": TS + "const names = getNames(fullName);",
     "suffix": "\n", "checks": [check_new_line_or_empty]},
    {"name": "jsx text right of cursor", "file": "Page.tsx",
     "prefix": "export function Page() {\n  return (\n    <main className=\"p-4\">\n      <div> ",
     "suffix": "</div>\n    </main>\n  );\n}\n", "checks": [check_single_line]},
    {"name": "py return expression", "file": "check.py", "prefix": "def is_even(n):\n    return ", "suffix": "\n",
     "checks": [check_same_line, contains_any("%")]},
    {"name": "py after def", "file": "check.py", "prefix": "def is_even(n):", "suffix": "\n",
     "checks": [check_new_line, first_line_indent(4)]},
    {"name": "py elif on empty line", "file": "check.py",
     "prefix": "def check(x):\n    if x > 0:\n        return 'positive'\n    ", "suffix": "\n\nprint(check(1))\n",
     "checks": [starts_with("el"), python_block_structure]},
    {"name": "py call arguments", "file": "paths.py", "prefix": "import os\n\npath = os.path.join(", "suffix": ")\n",
     "checks": [check_single_line, check_no_suffix_duplicate]},
    {"name": "go func body, no indented lines yet", "file": "main.go",
     "prefix": "package main\n\nfunc add(a int, b int) int {", "suffix": "\n}\n",
     "checks": [check_new_line, uses_tabs, contains_any("return")]},
    {"name": "go if err body", "file": "main.go",
     "prefix": "package main\n\nimport \"os\"\n\nfunc main() {\n\tf, err := os.Open(\"a.txt\")\n\tif err != nil {",
     "suffix": "\n\t}\n\tdefer f.Close()\n}\n", "checks": [check_new_line, uses_tabs, first_line_indent(8)]},
    {"name": "rust fn body", "file": "lib.rs", "prefix": "fn add(a: i32, b: i32) -> i32 {", "suffix": "\n}\n",
     "checks": [check_new_line, first_line_indent(4)]},
    {"name": "rust macro arguments", "file": "main.rs",
     "prefix": "fn main() {\n    let name = \"x\";\n    println!(\"Hello, {}\", ", "suffix": ");\n}\n",
     "checks": [check_single_line, contains_any("name"), check_no_suffix_duplicate]},
    {"name": "java method body", "file": "Calc.java",
     "prefix": "public class Calc {\n    public int add(int a, int b) {", "suffix": "\n    }\n}\n",
     "checks": [check_new_line, first_line_indent(8)]},
    {"name": "java new expression", "file": "A.java",
     "prefix": "import java.util.*;\n\npublic class A {\n    List<String> names = new ", "suffix": ";\n}\n",
     "checks": [check_same_line, contains_any("ArrayList"), check_no_suffix_duplicate]},
    {"name": "csharp allman braces", "file": "Calc.cs",
     "prefix": "public class Calc\n{\n    public int Add(int a, int b)\n    {\n        return a + b;\n    }\n\n    public int Sub(int a, int b)",
     "suffix": "\n}\n", "checks": [starts_with("\n    {"), lines_at_least(4)]},
    {"name": "ruby def body", "file": "greeter.rb", "prefix": "class Greeter\n  def greet(name)",
     "suffix": "\n  end\nend\n", "checks": [check_new_line, first_line_indent(4)]},
    {"name": "ruby each do", "file": "items.rb", "prefix": "items = [1, 2, 3]\nitems.each do |item|",
     "suffix": "\nend\n", "checks": [check_new_line, first_line_indent(2)]},
    {"name": "yaml nested key", "file": "docker-compose.yml",
     "prefix": "services:\n  web:\n    image: nginx\n    ports:", "suffix": "\n",
     "checks": [check_new_line, lines_at_least(4)]},
    {"name": "sql where clause", "file": "query.sql", "prefix": "SELECT id, name\nFROM users\nWHERE ", "suffix": ";\n",
     "checks": [check_same_line, check_no_suffix_duplicate]},
    {"name": "html list item", "file": "index.html", "prefix": "<ul class=\"menu\">\n  ", "suffix": "\n</ul>\n",
     "checks": [optional(check_same_line), optional(starts_with("<li")), lines_at_least(2)]},
    {"name": "css rule body", "file": "styles.css", "prefix": ".button {", "suffix": "\n}\n",
     "checks": [check_new_line, first_line_indent(2), contains_any(":")]},
    {"name": "bash if then", "file": "run.sh", "prefix": "#!/bin/bash\nif [ -f \"$1\" ]; then", "suffix": "\nfi\n",
     "checks": [check_new_line, lines_at_least(2, ("else", "elif"))]},
    {"name": "json value", "file": "package.json", "prefix": "{\n  \"name\": \"bridge\",\n  \"version\": ",
     "suffix": "\n}\n", "checks": [check_same_line]},
    {"name": "lua function body", "file": "math.lua", "prefix": "local function add(a, b)", "suffix": "\nend\n",
     "checks": [check_new_line, lines_at_least(2)]},
]
DEFAULT_CHECKS = [check_no_prose, check_balanced]


def continue_prefix(case):
    extension = os.path.splitext(case["file"])[1]
    if extension == ".html":
        header = f"<!-- {case['file']} -->"
    else:
        header = f"{COMMENT_BY_EXTENSION.get(extension, '//')} {case['file']}"
    return f"{header}\n{case['prefix']}"


def load_models(path):
    models = []
    for line in open(path, encoding="utf-8"):
        entry = re.match(r"^\s*-\s+name:\s*(.+?)\s*$", line)
        if entry:
            models.append({"name": entry.group(1)})
            continue
        setting = re.match(r"^\s+(model|debounceDelay|modelTimeout|autocomplete):\s*(.+?)\s*$", line)
        if setting and models:
            value = setting.group(2)
            models[-1][setting.group(1)] = json.loads(value) if value.startswith('"') or value.isdigit() else value
    return [model for model in models if "model" in model]


def fim_prompt(model, prefix, suffix):
    template = model.get("autocomplete", "<fim_prefix>{{{prefix}}}<fim_suffix>{{{suffix}}}<fim_middle>")
    return template.replace("{{{prefix}}}", prefix).replace("{{{suffix}}}", suffix)


def percentile(values, share):
    ordered = sorted(values)
    return ordered[max(math.ceil(len(ordered) * share) - 1, 0)]


def start_bridge():
    server = app.ThreadingHTTPServer(("127.0.0.1", 0), app.CLIBridgeHandler)
    threading.Thread(target=server.serve_forever, name="eval-bridge", daemon=True).start()
    return server


def warm_pools(models):
    started = time.time()
    pools = {model["model"]: app.get_pool(*app.parse_model(model["model"])) for model in models}
    startup = {}
    deadline = started + app.WORKER_READY_TIMEOUT_SECONDS * 2
    while len(startup) < len(pools) and time.time() < deadline:
        for name, pool in pools.items():
            if name not in startup and (pool.idle.qsize() >= pool.slots or pool.slots <= 0):
                startup[name] = round((time.time() - started) * 1000)
        time.sleep(0.02)
    for name, pool in pools.items():
        if pool.slots <= 0:
            raise RuntimeError(f"No worker could start for {name}: {pool.last_error}")
        startup.setdefault(name, round((time.time() - started) * 1000))
    return startup


def request_completion(port, model, prompt):
    body = json.dumps({"model": model["model"], "prompt": prompt, "stream": True}).encode("utf-8")
    request = urllib.request.Request(f"http://127.0.0.1:{port}/v1/completions", data=body, method="POST",
                                     headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=model.get("modelTimeout", 10000) / 1000) as response:
        lines = response.read().decode("utf-8").splitlines()
    chunks = [json.loads(line[6:]) for line in lines if line.startswith("data: {")]
    return "".join(chunk["choices"][0]["text"] for chunk in chunks)


def run_case(port, model, case):
    prefix = continue_prefix(case)
    debounce = model.get("debounceDelay", 0)
    started = time.time()
    time.sleep(debounce / 1000)
    sent = time.time()
    try:
        text = request_completion(port, model, fim_prompt(model, prefix, case["suffix"]))
    except (urllib.error.URLError, TimeoutError) as error:
        text, error_name = "", f"request_failed({error})"
    else:
        error_name = None
    finished = time.time()
    failed = [error_name] if error_name else [
        check.__name__ for check in DEFAULT_CHECKS + case["checks"] if not check(text, {**case, "prefix": prefix})]
    return {"case": case["name"], "ms": round((finished - started) * 1000), "bridge_ms": round((finished - sent) * 1000),
            "debounce_ms": debounce, "failed": failed, "text": text}


def print_model_report(model, results):
    by_case = defaultdict(lambda: [0, 0])
    failures = Counter()
    for result in results:
        by_case[result["case"]][0] += not result["failed"]
        by_case[result["case"]][1] += 1
        failures.update(result["failed"])
    if not failures:
        return
    print(f"\n[{model}]")
    for name, (ok, count) in by_case.items():
        if ok < count:
            print(f"  {name:36} {ok}/{count}")
    print("  failed checks:", dict(failures))


def print_benchmark(models, startup, results):
    header = (f"{'model':14} {'passed':>7} {'startup':>8} {'debounce':>8} {'bridge':>7} {'e2e':>7} "
              f"{'mean':>7} {'p90':>7} {'max':>7} {f'<={WORD_END_BUDGET_MS}ms':>8}")
    print(f"\n{header}\n{'-' * len(header)}")
    for model in models:
        rows = results[model["model"]]
        times = [row["ms"] for row in rows]
        bridge = [row["bridge_ms"] for row in rows]
        passed = sum(not row["failed"] for row in rows)
        in_budget = sum(ms <= WORD_END_BUDGET_MS for ms in times)
        print(f"{model['model']:14} {f'{passed}/{len(rows)}':>7} {startup[model['model']]:>6}ms "
              f"{model.get('debounceDelay', 0):>6}ms {statistics.median(bridge):>5.0f}ms {statistics.median(times):>5.0f}ms "
              f"{statistics.mean(times):>5.0f}ms {percentile(times, 0.9):>5}ms {max(times):>5}ms "
              f"{f'{in_budget}/{len(rows)}':>8}")


def main():
    reps = int(os.environ.get("REPS", "2"))
    wanted = [word.strip() for word in os.environ.get("MODELS", "").split(",") if word.strip()]
    models = [model for model in load_models(os.environ.get("EVAL_CONFIG", DEFAULT_CONFIG))
              if not wanted or any(word in model["model"] for word in wanted)]
    if not models:
        sys.exit("No models selected from the config")
    selected = [case for case in CASES if not sys.argv[1:] or any(word in case["name"] for word in sys.argv[1:])]
    app.console_handler.setLevel(logging.WARNING)
    server = start_bridge()
    port = server.server_address[1]
    results = {model["model"]: [] for model in models}
    try:
        startup = warm_pools(models)
        for case in selected * reps:
            for model in models:
                result = run_case(port, model, case)
                results[model["model"]].append(result)
                if result["failed"]:
                    print(f"FAIL {model['model']:14} {case['name']:36} {result['failed']} final={result['text'][:70]!r}")
                sys.stdout.flush()
    finally:
        server.shutdown()
        server.server_close()
        for pool in list(app.pools.values()):
            pool.close()
    for model in models:
        print_model_report(model["model"], results[model["model"]])
    print_benchmark(models, startup, results)
    output = os.environ.get("EVAL_OUTPUT")
    if output:
        rows = [{"model": model, **row} for model, model_rows in results.items() for row in model_rows]
        json.dump({"startup_ms": startup, "results": rows}, open(output, "w", encoding="utf-8"), indent=1)


if __name__ == "__main__":
    main()

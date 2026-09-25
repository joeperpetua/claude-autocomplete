import re

CURSOR = "<CURSOR>"
INSERT_OPEN = "<INSERT>"
INSERT_CLOSE = "</INSERT>"
FIM_PREFIX = "<fim_prefix>"
FIM_SUFFIX = "<fim_suffix>"
FIM_MIDDLE = "<fim_middle>"
MAX_COMPLETION_CHARS = 2000

SYSTEM_PROMPT = (
    f"You are a code completion engine. Reply with only the exact code to insert at {CURSOR}, "
    f"inside {INSERT_OPEN}{INSERT_CLOSE} tags. Never describe or explain the code. "
    f"Never repeat code before or after {CURSOR}. Prefer short completions: finish the current line, "
    f"and add more lines only for an obvious block body. "
    f"If nothing should be inserted, reply with {INSERT_OPEN}{INSERT_CLOSE}."
)

INSERT_PATTERN = re.compile(f"{re.escape(INSERT_OPEN)}(.*?){re.escape(INSERT_CLOSE)}", re.DOTALL)
FENCE_LINE = re.compile(r"^\s*```[\w+-]*\s*$")
IDENTIFIER_CHAR = re.compile(r"[A-Za-z0-9_$]")
HEADER_LINE = re.compile(r"^\s*(?://|#|--|;|<!--)\s*(?!Path:)(\S+\.([A-Za-z0-9]+))\s*(?:-->)?\s*$")
PROSE_START = re.compile(
    r"^(looking at|based on|i notice|i can see|i need|i'll|i will|here's|here is|the code|the cursor|"
    r"this code|it looks|it seems|to complete|sure|sorry|certainly)\b", re.I)
CODE_CHAR = re.compile(r"[;{}()=\[\]<>]")
PUNCTUATION_LINE = re.compile(r"^[\s)\]};,]*$|^</[\w.-]+>$")
CLOSERS_ONLY = re.compile(r"^\s*[)\]}][\s)\]};,]*$")
MEMBER_ACCESS = re.compile(r"[\w$)\]]\.$")
NUMBER_BEFORE_DOT = re.compile(r"(?<![\w$.])\d+\.$")
MEMBER_START_EXTRAS = {"go": "(", "rs": "0123456789", "swift": "0123456789", "java": "<", "sql": "*"}
NO_MEMBER_RULE = {"php", "pl", "pm", "md", "markdown", "txt", "rst", "tex", "adoc", "org"}
BLOCK_OPENERS = (":", "{", "(", "[", "=>")
OPEN_TO_CLOSE = {"(": ")", "[": "]", "{": "}"}
CLOSE_TO_OPEN = {close: open_ for open_, close in OPEN_TO_CLOSE.items()}

BRACE_BLOCK = re.compile(r"\{\s*$")
PYTHON_BLOCK = re.compile(r"^\s*(async\s+def|def|class|if|elif|else|for|while|try|except|finally|with|match|case)\b.*:\s*$")
RUBY_BLOCK = re.compile(
    r"^\s*(def|class|module|if|unless|while|until|case|begin|elsif|else|when|rescue|ensure)\b(?!.*\bend\s*$)"
    r"|\bdo(\s*\|[^|]*\|)?\s*$")
LUA_BLOCK = re.compile(r"(\bthen|\bdo|\belse|\brepeat|\bfunction\b[^()]*\([^)]*\))\s*$")
SHELL_BLOCK = re.compile(r"(\bthen|\bdo|\belse|\{)\s*$")
YAML_BLOCK = re.compile(r"^\s*(-\s+)?[^\s#:][^#:]*:\s*$")

DEFAULT_LANGUAGE = {
    "blocks": (BRACE_BLOCK,),
    "closers": ("}", ")", "]", "</", "/>"),
    "comments": ("//", "/*"),
    "step": 2,
    "tabs": False,
    "statement_end": ";",
}
LANGUAGES = {
    "python": {"blocks": (PYTHON_BLOCK, BRACE_BLOCK), "comments": ("#",), "step": 4},
    "ruby": {"blocks": (RUBY_BLOCK, BRACE_BLOCK), "comments": ("#",), "extra_closers": ("end",)},
    "lua": {"blocks": (LUA_BLOCK, BRACE_BLOCK), "comments": ("--",), "extra_closers": ("end", "until")},
    "shell": {"blocks": (SHELL_BLOCK,), "comments": ("#",), "extra_closers": ("fi", "done", "esac")},
    "yaml": {"blocks": (YAML_BLOCK,), "comments": ("#",)},
    "hash_comments": {"comments": ("#",)},
    "sql": {"comments": ("--",)},
    "html": {"comments": ("<!--",)},
    "go": {"tabs": True, "step": 4},
    "four_spaces": {"step": 4},
}
EXTENSIONS = {
    "py": "python", "pyi": "python", "rb": "ruby", "lua": "lua",
    "sh": "shell", "bash": "shell", "zsh": "shell",
    "yml": "yaml", "yaml": "yaml",
    "r": "hash_comments", "pl": "hash_comments", "toml": "hash_comments", "ps1": "hash_comments",
    "ex": "hash_comments", "exs": "hash_comments", "jl": "hash_comments", "nim": "hash_comments",
    "cmake": "hash_comments", "conf": "hash_comments",
    "sql": "sql", "html": "html", "htm": "html", "xml": "html", "vue": "html", "svelte": "html",
    "go": "go",
    "rs": "four_spaces", "java": "four_spaces", "cs": "four_spaces", "c": "four_spaces", "h": "four_spaces",
    "cpp": "four_spaces", "cc": "four_spaces", "hpp": "four_spaces", "php": "four_spaces", "kt": "four_spaces",
    "swift": "four_spaces", "scala": "four_spaces", "dart": "four_spaces",
}


def normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def split_fim(raw_prompt: str) -> tuple:
    """Extracts Fill-In-The-Middle contexts from a raw autocomplete prompt."""
    start = raw_prompt.find(FIM_PREFIX)
    split = raw_prompt.rfind(FIM_SUFFIX)
    end = raw_prompt.rfind(FIM_MIDDLE)

    if start != -1 and start < split < end:
        prefix = raw_prompt[start + len(FIM_PREFIX):split]
        suffix = raw_prompt[split + len(FIM_SUFFIX):end]
    else:
        prefix = raw_prompt
        suffix = ""

    if prefix.endswith("\r") and suffix.startswith("\n"):
        prefix = prefix[:-1]

    return normalize_newlines(prefix), normalize_newlines(suffix)


def transform_fim_to_prompt(prefix: str, suffix: str, include_instructions: bool) -> str:
    """Creates an autocomplete prompt from Fill-In-The-Middle contexts."""
    code = f"[CODE]\n{prefix}{CURSOR}{suffix}\n[/CODE]"
    return f"{SYSTEM_PROMPT}\n\n{code}" if include_instructions else code


def indent_width(line: str) -> int:
    expanded = line.expandtabs(4)
    return len(expanded) - len(expanded.lstrip())


def render_indent(width: int, tabs: bool) -> str:
    return "\t" * (width // 4) + " " * (width % 4) if tabs else " " * width


class Context:
    def __init__(self, prefix: str, suffix: str):
        self.prefix = prefix
        self.suffix = suffix
        self.line_before = prefix.rsplit("\n", 1)[-1]
        self.rest_of_line = suffix.split("\n", 1)[0]
        self.base = indent_width(self.line_before)
        following = [line for line in suffix.split("\n")[1:] if line.strip()]
        self.floor = indent_width(following[0]) if following else None
        name, self.extension, code_lines = detect_language(prefix)
        language = {**DEFAULT_LANGUAGE, **LANGUAGES.get(name, {})}
        self.language = name
        self.blocks = language["blocks"]
        self.closers = language["closers"] + language.get("extra_closers", ())
        self.comments = language["comments"]
        self.statement_end = language["statement_end"]
        self.step, self.tabs, self.allman = formatting_profile(code_lines, language)

    @property
    def code_before(self) -> str:
        return self.line_before.rstrip()

    @property
    def at_line_end(self) -> bool:
        return bool(self.line_before.strip()) and not self.rest_of_line.strip()

    @property
    def before_closers(self) -> bool:
        return bool(CLOSERS_ONLY.match(self.rest_of_line)) and self.opens_block(self.code_before)

    @property
    def after_member_access(self) -> bool:
        line = self.line_before
        if self.extension in NO_MEMBER_RULE or not MEMBER_ACCESS.search(line) or NUMBER_BEFORE_DOT.search(line):
            return False
        return ends_in_code(self.prefix, self.comments)

    def opens_block(self, line: str) -> bool:
        return any(pattern.search(line) for pattern in self.blocks)

    def width(self, line: str) -> int:
        leading = line[:len(line) - len(line.lstrip())]
        if "\t" in leading and not self.tabs:
            return leading.count("\t") * self.step + leading.count(" ")
        return indent_width(line)

    def restyle(self, line: str, shift: int) -> str:
        if not line.strip():
            return line
        return render_indent(max(0, self.width(line) + shift), self.tabs) + line.lstrip()


def detect_language(prefix: str) -> tuple:
    lines = prefix.split("\n")
    for index in range(len(lines) - 1, -1, -1):
        match = HEADER_LINE.match(lines[index])
        if match:
            extension = match.group(2).lower()
            return EXTENSIONS.get(extension, extension), extension, lines[index + 1:]
    return None, None, lines


def formatting_profile(code_lines: list, language: dict) -> tuple:
    widths = [indent_width(line) for line in code_lines if line.strip()]
    steps = [after - before for before, after in zip(widths, widths[1:]) if after - before >= 2]
    step = min(steps) if steps else language["step"]
    indented = [line for line in code_lines if line.strip() and line[:1].isspace()]
    tabs = sum(line.startswith("\t") for line in indented) * 2 > len(indented) if indented else language["tabs"]
    brace_only = sum(1 for line in code_lines if line.strip() == "{")
    brace_at_end = sum(1 for line in code_lines if line.rstrip().endswith("{") and line.strip() != "{")
    return step, tabs, brace_only > 0 and brace_only >= brace_at_end


def strip_markup(body: str) -> str:
    lines = [line for line in normalize_newlines(body).split("\n") if not FENCE_LINE.match(line)]
    code = "\n".join(lines)
    for token in (FIM_PREFIX, FIM_SUFFIX, FIM_MIDDLE):
        code = code.replace(token, "")
    return code.strip("\n").rstrip()


def looks_like_prose(code: str) -> bool:
    first = code.strip().split("\n", 1)[0]
    return bool(PROSE_START.match(first)) and " " in first and not CODE_CHAR.search(first)


def trim_prefix_overlap(completion: str, prefix: str) -> str:
    line = prefix.rsplit("\n", 1)[-1]
    indent = len(line) - len(line.lstrip())
    for start in range(indent, len(line)):
        at_boundary = start == indent or not IDENTIFIER_CHAR.match(line[start - 1])
        overlap = line[start:]
        if at_boundary and not line[start].isspace() and completion.startswith(overlap):
            return completion[len(overlap):]
    return completion


def closes_own_openers(kept: str, duplicate: str, comments: tuple) -> bool:
    stack = []
    for _, char in code_chars(kept, comments):
        if char in OPEN_TO_CLOSE:
            stack.append(char)
        elif char in CLOSE_TO_OPEN and stack and stack[-1] == CLOSE_TO_OPEN[char]:
            stack.pop()
    for char in duplicate:
        if char in CLOSE_TO_OPEN and stack and stack[-1] == CLOSE_TO_OPEN[char]:
            return True
    return False


def trim_suffix_overlap(completion: str, ctx: "Context") -> str:
    def overlap(text: str, head: str) -> int:
        for size in range(min(len(text), len(head)), 0, -1):
            if text[len(text) - size:] == head[:size]:
                return size
        return 0

    cut = overlap(completion, ctx.suffix)
    if not cut:
        stripped = completion.rstrip()
        relaxed = overlap(stripped, ctx.suffix.lstrip())
        cut = relaxed + len(completion) - len(stripped) if relaxed else 0
    duplicate = completion[len(completion) - cut:] if cut else ""
    if not duplicate.strip() or (len(duplicate.strip()) == 1 and IDENTIFIER_CHAR.match(duplicate.strip())):
        return completion
    kept = completion[:len(completion) - cut]
    if closes_own_openers(kept, duplicate, ctx.comments):
        return completion
    return kept.rstrip()


def code_chars(text: str, comments: tuple):
    mode = None
    index = 0
    while index < len(text):
        char = text[index]
        pair = text[index:index + 2]
        if mode is None:
            if char == "\\":
                index += 2
                continue
            if pair == "//" and "//" in comments or char == "#" and "#" in comments or pair == "--" and "--" in comments:
                mode = "line"
            elif pair == "/*" and "/*" in comments:
                mode = "block"
                index += 1
            elif text.startswith("<!--", index) and "<!--" in comments:
                mode = "markup"
                index += 3
            elif char in "\"'`":
                mode = char
            else:
                yield index, char
        elif mode == "line":
            if char == "\n":
                mode = None
        elif mode == "block":
            if pair == "*/":
                mode = None
                index += 1
        elif mode == "markup":
            if text.startswith("-->", index):
                mode = None
                index += 2
        else:
            if char == "\\":
                index += 2
                continue
            if char == mode or char == "\n" and mode != "`":
                mode = None
        index += 1


def ends_in_code(prefix: str, comments: tuple) -> bool:
    last = len(prefix) - 1
    return any(index == last for index, _ in code_chars(prefix, comments))


def starts_member_name(completion: str, ctx: Context) -> bool:
    first = completion[:1]
    return first.isalpha() or first in "_$#" or first in MEMBER_START_EXTRAS.get(ctx.extension, "")


def pending_openers(prefix: str, comments: tuple) -> list:
    stack = []
    for _, char in code_chars(prefix, comments):
        if char in OPEN_TO_CLOSE:
            stack.append(char)
        elif char in CLOSE_TO_OPEN and stack and stack[-1] == CLOSE_TO_OPEN[char]:
            stack.pop()
    return stack


def balance(completion: str, ctx: Context, dangling: str) -> tuple:
    pending = pending_openers(ctx.prefix, ctx.comments)
    stack, overclose = [], None
    for index, char in code_chars(completion, ctx.comments):
        if char in OPEN_TO_CLOSE:
            stack.append((char, index))
        elif char in CLOSE_TO_OPEN:
            wanted = CLOSE_TO_OPEN[char]
            if stack and stack[-1][0] == wanted:
                stack.pop()
            elif not stack and pending and pending[-1] == wanted:
                pending.pop()
            else:
                overclose = index
                break
    cut, reason = (overclose, "over-close") if overclose is not None else (len(completion), "")
    open_before_cut = [(char, index) for char, index in stack if index < cut]
    if open_before_cut and "\n" in completion[open_before_cut[0][1]:cut]:
        closer = OPEN_TO_CLOSE[open_before_cut[-1][0]]
        closed_by_suffix = ctx.suffix.lstrip().startswith(closer) and len(open_before_cut) == 1
        if not closed_by_suffix:
            if dangling == "discard":
                return "", "dangling-open"
            cut, reason = open_before_cut[0][1], "dangling-open"
    return completion[:cut].rstrip(), reason


def has_runaway_repetition(text: str) -> bool:
    lines = [line.strip() for line in text.split("\n") if line.strip() and not PUNCTUATION_LINE.match(line)]
    if any(lines[i] == lines[i + 1] == lines[i + 2] for i in range(len(lines) - 2)):
        return True
    stripped = text.rstrip()
    if len(stripped) < 20:
        return False
    for period in range(1, 33):
        for start in range(0, len(stripped) - period * 3 + 1):
            unit = stripped[start:start + period]
            if not IDENTIFIER_CHAR.search(unit):
                continue
            repeats = 1
            while stripped.startswith(unit, start + repeats * period):
                repeats += 1
            if repeats >= 5 or repeats >= 3 and repeats * period >= max(16, len(stripped) * 0.6):
                return True
    return False


def new_line_target(first: str, ctx: Context):
    code_before = ctx.code_before
    closes = first.lstrip().startswith(ctx.closers)
    if ctx.opens_block(code_before) and not (code_before.endswith("{") and first.rstrip().endswith("}")):
        return True, ctx.base if closes else ctx.base + ctx.step
    if ctx.allman and first.lstrip().startswith("{") and not code_before.endswith("{"):
        return True, ctx.base
    if ctx.statement_end and code_before.endswith(ctx.statement_end):
        return True, None if closes else ctx.base
    if ctx.width(first) >= 2:
        return True, None
    return False, None


def place_on_new_line(lines: list, target, ctx: Context) -> str:
    if target is not None:
        shift = max(0, target - ctx.width(lines[0]))
    else:
        widths = [ctx.width(line) for line in lines if line.strip()]
        shift = ctx.base if ctx.floor is not None and ctx.base and min(widths) < ctx.floor else 0
    return "\n" + "\n".join(ctx.restyle(line, shift) for line in lines)


def fix_relative_indentation(completion: str, ctx: Context) -> str:
    lines = completion.split("\n")
    later = [line for line in lines[1:] if line.strip()]
    if not later:
        return completion
    shift = 0
    if ctx.base:
        below_floor = ctx.floor is not None and min(ctx.width(line) for line in later) < ctx.floor
        full_first_line = ctx.line_before + lines[0]
        first_opens = full_first_line.rstrip().endswith(BLOCK_OPENERS) or ctx.opens_block(full_first_line)
        closes_first = later[0].lstrip().startswith(ctx.closers)
        shallow = first_opens and (
            ctx.width(later[0]) < ctx.base if closes_first else ctx.width(later[0]) <= ctx.base
        )
        shift = ctx.base if below_floor or shallow else 0
    return "\n".join(lines[:1] + [ctx.restyle(line, shift) for line in lines[1:]])


def place(code: str, ctx: Context) -> str:
    lines = code.split("\n")
    if ctx.at_line_end or ctx.before_closers:
        new_line, target = new_line_target(lines[0], ctx)
        if new_line:
            return place_on_new_line(lines, target, ctx)
    completion = trim_prefix_overlap(code.strip(), ctx.prefix)
    keeps_space = ctx.line_before.strip() and not ctx.line_before[-1:].isspace() and not ctx.after_member_access
    if code[:1] == " " and keeps_space and completion[:1] not in ("", " "):
        completion = " " + completion
    return fix_relative_indentation(completion, ctx)


def cap_length(completion: str, ctx: Context) -> tuple:
    if len(completion) <= MAX_COMPLETION_CHARS:
        return completion, ""
    clipped = completion[:MAX_COMPLETION_CHARS]
    last_break = clipped.rfind("\n")
    clipped = clipped[:last_break] if last_break > 0 else clipped
    text, _ = balance(clipped, ctx, dangling="cut")
    return text, "too-long"


def extract_completion(output: str, prefix: str, suffix: str) -> tuple:
    match = INSERT_PATTERN.search(output)
    if match is None:
        return "", "untagged"
    if CURSOR in match.group(1):
        return "", "mentions-cursor"
    code = strip_markup(match.group(1))
    if not code.strip():
        return "", "empty"
    if looks_like_prose(code):
        return "", "prose"
    ctx = Context(prefix, suffix)
    completion = place(code, ctx)
    if ctx.after_member_access and not starts_member_name(completion, ctx):
        return "", "not-a-member"
    if ctx.rest_of_line.strip() and not ctx.before_closers and "\n" in completion:
        completion = completion.split("\n", 1)[0]
        if not completion.strip():
            return "", "multi-line-mid-line"
    completion = trim_suffix_overlap(completion, ctx)
    if not completion.strip():
        return "", "duplicate-suffix"
    if has_runaway_repetition(completion):
        return "", "repetition"
    completion, reason = balance(completion, ctx, dangling="discard")
    if not completion.strip():
        return "", reason or "empty"
    completion, capped = cap_length(completion, ctx)
    completion = completion.rstrip()
    if not completion.strip():
        return "", capped or "empty"
    if ctx.before_closers and completion.startswith("\n"):
        completion += "\n" + render_indent(ctx.base, ctx.tabs)
    return completion, reason or capped

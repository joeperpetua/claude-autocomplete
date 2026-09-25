const HASH_COMMENTS = new Set([
  "py", "pyi", "rb", "sh", "bash", "zsh", "yml", "yaml", "r", "pl", "toml", "ps1",
  "ex", "exs", "jl", "nim", "cmake", "conf",
]);
const DASH_COMMENTS = new Set(["sql", "lua"]);
const MARKUP_COMMENTS = new Set(["html", "htm", "xml", "vue", "svelte"]);

export const FIM_PREFIX = "<fim_prefix>";
export const FIM_SUFFIX = "<fim_suffix>";
export const FIM_MIDDLE = "<fim_middle>";

export interface PromptInput {
  relativePath: string;
  before: string;
  after: string;
  maxPrefixChars: number;
  maxSuffixChars: number;
}

function isAbsolutePath(path: string): boolean {
  return /^[A-Za-z]:\//.test(path) || path.startsWith("/");
}

export function headerLine(relativePath: string): string | undefined {
  const normalized = relativePath.replace(/\\/g, "/");
  const fileName = normalized.slice(normalized.lastIndexOf("/") + 1);
  const name = /\s/.test(normalized) || isAbsolutePath(normalized) ? fileName : normalized;
  const extension = /\.([A-Za-z0-9]+)$/.exec(name);
  if (!extension || !name || /\s/.test(name)) {
    return undefined;
  }
  const kind = extension[1].toLowerCase();
  if (MARKUP_COMMENTS.has(kind)) {
    return `<!-- ${name} -->`;
  }
  if (HASH_COMMENTS.has(kind)) {
    return `# ${name}`;
  }
  if (DASH_COMMENTS.has(kind)) {
    return `-- ${name}`;
  }
  return `// ${name}`;
}

export function clipPrefix(text: string, maxChars: number): string {
  if (text.length <= maxChars) {
    return text;
  }
  const start = text.length - maxChars;
  if (text[start - 1] === "\n") {
    return text.slice(start);
  }
  const clipped = text.slice(start);
  const lineStart = clipped.indexOf("\n");
  return lineStart === -1 ? clipped : clipped.slice(lineStart + 1);
}

export function clipSuffix(text: string, maxChars: number): string {
  if (text.length <= maxChars) {
    return text;
  }
  const clipped = text.slice(0, maxChars);
  const lineEnd = clipped.lastIndexOf("\n");
  return lineEnd === -1 ? clipped : clipped.slice(0, lineEnd + 1);
}

function normalizeNewlines(text: string): string {
  return text.replace(/\r\n?/g, "\n");
}

export function buildPrompt(input: PromptInput): string {
  const header = headerLine(input.relativePath);
  const prefix = clipPrefix(normalizeNewlines(input.before), input.maxPrefixChars);
  const suffix = clipSuffix(normalizeNewlines(input.after), input.maxSuffixChars);
  return `${FIM_PREFIX}${header ? `${header}\n` : ""}${prefix}${FIM_SUFFIX}${suffix}${FIM_MIDDLE}`;
}

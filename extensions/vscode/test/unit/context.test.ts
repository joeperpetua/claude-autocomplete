import assert from "node:assert/strict";
import { test } from "node:test";
import { buildPrompt, clipPrefix, clipSuffix, headerLine } from "../../src/context.ts";

test("header comment matches the file type", () => {
  assert.equal(headerLine("src/names.ts"), "// src/names.ts");
  assert.equal(headerLine("app/check.py"), "# app/check.py");
  assert.equal(headerLine("db/query.sql"), "-- db/query.sql");
  assert.equal(headerLine("web/index.html"), "<!-- web/index.html -->");
  assert.equal(headerLine("docker-compose.yml"), "# docker-compose.yml");
});

test("header uses forward slashes", () => {
  assert.equal(headerLine("src\\lib\\main.rs"), "// src/lib/main.rs");
});

test("header falls back to the file name when the path has spaces", () => {
  assert.equal(headerLine("My Project/src/app.go"), "// app.go");
});

test("header uses only the file name for files outside the workspace", () => {
  assert.equal(headerLine("c:\\Users\\me\\scratch\\test.ts"), "// test.ts");
  assert.equal(headerLine("/home/me/scratch/check.py"), "# check.py");
});

test("no header without a usable file name", () => {
  assert.equal(headerLine("Dockerfile"), undefined);
  assert.equal(headerLine("Untitled-1"), undefined);
  assert.equal(headerLine("notes/my file.py"), undefined);
});

test("prefix is clipped at a line start", () => {
  assert.equal(clipPrefix("line one\nline two\nline three", 14), "line three");
  assert.equal(clipPrefix("aaaa\nbbbb", 4), "bbbb");
  assert.equal(clipPrefix("short", 100), "short");
  assert.equal(clipPrefix("oneverylongline", 5), "gline");
});

test("suffix is clipped at a line end", () => {
  assert.equal(clipSuffix(");\nnext();\nlast();\n", 12), ");\nnext();\n");
  assert.equal(clipSuffix("short", 100), "short");
  assert.equal(clipSuffix("oneverylongline", 5), "oneve");
});

test("prompt uses the StarCoder tokens and a header line", () => {
  const prompt = buildPrompt({ relativePath: "src/names.ts", before: "const names = ", after: "\n", maxPrefixChars: 4000, maxSuffixChars: 1000 });
  assert.equal(prompt, "<fim_prefix>// src/names.ts\nconst names = <fim_suffix>\n<fim_middle>");
});

test("prompt normalizes Windows line endings", () => {
  const prompt = buildPrompt({ relativePath: "a.py", before: "def f():\r\n    ", after: "\r\npass\r\n", maxPrefixChars: 4000, maxSuffixChars: 1000 });
  assert.equal(prompt, "<fim_prefix># a.py\ndef f():\n    <fim_suffix>\npass\n<fim_middle>");
});

test("prompt without header when the file has no extension", () => {
  const prompt = buildPrompt({ relativePath: "Makefile", before: "all:", after: "", maxPrefixChars: 4000, maxSuffixChars: 1000 });
  assert.equal(prompt, "<fim_prefix>all:<fim_suffix><fim_middle>");
});

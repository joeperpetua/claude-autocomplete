import assert from "node:assert/strict";
import { test } from "node:test";
import { previewParts } from "../../src/preview.ts";

const NBSP = " ";

test("single line: text and a plain Ctrl+Tab badge", () => {
  assert.deepEqual(previewParts("(fullName);"), { line: "(fullName);", badge: "Ctrl+Tab" });
});

test("multi line: first line and a line count in the badge", () => {
  assert.deepEqual(previewParts("(fullName);\nconsole.log(names);"), { line: "(fullName);", badge: `Ctrl+Tab${NBSP}·${NBSP}+1${NBSP}line` });
  assert.deepEqual(previewParts("a\nb\nc"), { line: "a", badge: `Ctrl+Tab${NBSP}·${NBSP}+2${NBSP}lines` });
});

test("leading line breaks are skipped for the visible line", () => {
  assert.deepEqual(previewParts("\n    return a + b;"), { line: `${NBSP.repeat(4)}return${NBSP}a${NBSP}+${NBSP}b;`, badge: "Ctrl+Tab" });
});

test("a trailing line break before an existing closer is not counted", () => {
  assert.deepEqual(previewParts("\n  count: 0,\n"), { line: `${NBSP.repeat(2)}count:${NBSP}0,`, badge: "Ctrl+Tab" });
  assert.deepEqual(previewParts("\n    a();\n    b();\n  "), { line: `${NBSP.repeat(4)}a();`, badge: `Ctrl+Tab${NBSP}·${NBSP}+1${NBSP}line` });
});

test("spaces become non-breaking so the editor keeps them", () => {
  assert.equal(previewParts("= 'pepe';").line, `=${NBSP}'pepe';`);
});

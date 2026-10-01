const NBSP = " ";

function keepSpaces(text: string): string {
  return text.replace(/ /g, NBSP);
}

export function previewParts(text: string): { line: string; badge: string } {
  const lines = text.replace(/^\n+/, "").replace(/(\n[ \t]*)+$/, "").split("\n");
  const extra = lines.length - 1;
  const more = extra > 0 ? ` · +${extra} ${extra === 1 ? "line" : "lines"}` : "";
  return { line: keepSpaces(lines[0]), badge: keepSpaces(`Ctrl+Tab${more}`) };
}

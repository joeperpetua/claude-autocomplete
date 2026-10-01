import { build } from "esbuild";
import { readdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const source = dirname(fileURLToPath(import.meta.url));
const root = dirname(dirname(source));
const tests = readdirSync(source).filter((file) => file.endsWith(".test.ts")).map((file) => join(source, file));

await build({
  entryPoints: [join(source, "index.ts"), ...tests],
  outdir: join(root, "out", "integration"),
  bundle: true,
  platform: "node",
  format: "cjs",
  target: "node20",
  external: ["vscode", "mocha"],
  sourcemap: true,
  logLevel: "info",
});

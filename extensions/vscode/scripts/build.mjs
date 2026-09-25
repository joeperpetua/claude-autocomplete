import { build } from "esbuild";
import { copyFileSync, mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const bridgeSource = join(root, "..", "..", "bridge");
const bridgeTarget = join(root, "bridge");

mkdirSync(bridgeTarget, { recursive: true });
for (const file of ["app.py", "completion.py"]) {
  copyFileSync(join(bridgeSource, file), join(bridgeTarget, file));
}

await build({
  entryPoints: [join(root, "src", "extension.ts")],
  outfile: join(root, "dist", "extension.js"),
  bundle: true,
  platform: "node",
  format: "cjs",
  target: "node20",
  external: ["vscode"],
  sourcemap: true,
  logLevel: "info",
});

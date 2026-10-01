import { readdirSync } from "node:fs";
import { join } from "node:path";
import Mocha from "mocha";

export function run(): Promise<void> {
  const mocha = new Mocha({ ui: "tdd", color: true, timeout: 30000 });
  for (const file of readdirSync(__dirname).filter((name) => name.endsWith(".test.js")).sort()) {
    mocha.addFile(join(__dirname, file));
  }
  return new Promise((resolve, reject) => {
    mocha.run((failures) => {
      if (failures > 0) {
        reject(new Error(`${failures} integration ${failures === 1 ? "test" : "tests"} failed`));
      } else {
        resolve();
      }
    });
  });
}

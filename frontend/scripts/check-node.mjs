// Fails fast when the Node.js major version differs from the project runtime (ADR-0003
// MVP-02: Node 22). A mismatched runtime or a node_modules tree installed on another
// platform shows up later as opaque Next/TypeScript or Playwright start-up errors.
import { readFileSync } from "node:fs";

const wanted = readFileSync(new URL("../.nvmrc", import.meta.url), "utf8").trim().replace(/^v/, "");
const actual = process.versions.node;
const [major, minor] = actual.split(".").map(Number);

if (String(major) !== wanted.split(".")[0] || (major === 22 && minor < 6)) {
  const msg =
    `GoalPulse frontend needs Node ${wanted}.x (>= 22.6), but this is Node ${actual}.\n` +
    `  macOS: brew install node@22 && export PATH="$(brew --prefix node@22)/bin:$PATH"\n` +
    `  nvm:   nvm use   (reads frontend/.nvmrc)\n` +
    `Then reinstall dependencies on this machine: rm -rf node_modules && npm ci`;
  if (process.env.GOALPULSE_ALLOW_NODE_MISMATCH === "1") {
    console.warn(`WARNING (override active): ${msg}`);
  } else {
    console.error(msg);
    process.exit(1);
  }
}

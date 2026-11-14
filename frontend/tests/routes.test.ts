// D2-FE-04 acceptance "no unapproved routes merged": only paths from the frozen
// contracts/openapi/gateway.v1.yaml may appear in frontend source.
import assert from "node:assert/strict";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, it } from "node:test";

const SRC = new URL("../src/", import.meta.url).pathname;
const FROZEN_PREFIXES = ["/v1/goal-sessions", "/v1/goals"];

function files(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const p = join(dir, name);
    return statSync(p).isDirectory() ? files(p) : /\.(ts|tsx)$/.test(name) ? [p] : [];
  });
}

describe("gateway routes used by the frontend", () => {
  it("only calls frozen /v1 paths; no ICP-02 or internal routes", () => {
    for (const file of files(SRC)) {
      const text = readFileSync(file, "utf8");
      for (const match of text.matchAll(/["'`](\/(?:v1|internal)\/[^"'`$?]*)/g)) {
        const path = match[1];
        assert.ok(
          FROZEN_PREFIXES.some((prefix) => path.startsWith(prefix)) && !path.includes("/evaluations"),
          `${file} references non-frozen route ${path}`,
        );
      }
      assert.doesNotMatch(text, /\/internal\/v1\//, `${file} references an internal route`);
    }
  });
});

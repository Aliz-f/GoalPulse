// Only approved public gateway routes may appear in frontend source:
//   contracts/openapi/gateway.v1.yaml                   /v1/goal-sessions*, /v1/goals*
//   contracts/openapi/gateway.phase2.extension.v1.yaml  demo sign-in (R2-D2-02),
//     evaluations and notifications (R2-D2-04). Action packages (P1) are not called.
import assert from "node:assert/strict";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, it } from "node:test";

const SRC = new URL("../src/", import.meta.url).pathname;
const FROZEN_PREFIXES = ["/v1/goal-sessions", "/v1/goals"];
const DEMO_AUTH = ["/v2/demo/sessions", "/v2/demo/session", "/v2/demo/sessions/logout"];
const EXTENSION_PREFIXES = ["/v1/notifications"];

function files(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const p = join(dir, name);
    return statSync(p).isDirectory() ? files(p) : /\.(ts|tsx)$/.test(name) ? [p] : [];
  });
}

/** Source without comments, so explanations of what we avoid don't count as uses. */
function code(text: string): string {
  return text.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:])\/\/.*$/gm, "$1");
}

describe("gateway routes used by the frontend", () => {
  it("only calls approved public paths; no internal routes", () => {
    for (const file of files(SRC)) {
      const text = readFileSync(file, "utf8");
      for (const match of text.matchAll(/["'`](\/(?:v1|v2|internal)\/[^"'`$?]*)/g)) {
        const path = match[1];
        const approved =
          FROZEN_PREFIXES.some((prefix) => path.startsWith(prefix)) ||
          EXTENSION_PREFIXES.some((prefix) => path.startsWith(prefix)) ||
          DEMO_AUTH.includes(path);
        assert.ok(approved, `${file} references unapproved route ${path}`);
        assert.doesNotMatch(path, /action-packages/, `${file} calls the P1 package route`);
      }
      assert.doesNotMatch(text, /\/internal\/v1\//, `${file} references an internal route`);
    }
  });
});

describe("browser credentials (ADR-0003 MVP-06)", () => {
  it("never stores, reads or sends a token from the browser", () => {
    for (const file of files(SRC)) {
      const text = code(readFileSync(file, "utf8"));
      assert.doesNotMatch(text, /\b(sessionStorage|localStorage|indexedDB)\b/, `${file} uses browser storage`);
      if (file.endsWith("/lib/api/http.ts")) {
        assert.match(text, /document\.cookie/, "the HTTP client reads the intentionally browser-readable CSRF cookie");
        assert.match(text, /goalpulse_csrf/);
        assert.doesNotMatch(text, /goalpulse_session/, "the HTTP client must not read the HttpOnly identity cookie");
      } else {
        assert.doesNotMatch(text, /document\.cookie/, `${file} reads browser cookies`);
      }
      assert.doesNotMatch(text, /Authorization|Bearer /, `${file} builds an Authorization header`);
      assert.doesNotMatch(text, /credentials:\s*["']omit["']/, `${file} drops the session cookie`);
    }
  });

  it("reads only public, non-secret build settings", () => {
    const allowed = new Set(["NEXT_PUBLIC_GOALPULSE_PROFILE", "NEXT_PUBLIC_API_MODE", "NEXT_PUBLIC_GATEWAY_URL"]);
    for (const file of files(SRC)) {
      for (const match of code(readFileSync(file, "utf8")).matchAll(/process\.env\.([A-Z0-9_]+)/g)) {
        assert.ok(allowed.has(match[1]), `${file} reads ${match[1]}`);
      }
    }
  });
});

// R2-D2-02: after `npm run build`, prove the browser bundle carries no credentials,
// no private service settings and no leftover token store.
// Usage: node scripts/check-bundle.mjs [distDir]   (default .next)
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";

const dist = process.argv[2] ?? process.env.NEXT_DIST_DIR ?? ".next";
const root = join(dist, "static");

const FORBIDDEN = [
  [/ANTHROPIC_API_KEY|sk-ant-[A-Za-z0-9_-]{8,}/, "an Anthropic key or its setting"],
  [/SERVICE_JWT_SECRET|GATEWAY_DEV_JWT_SECRET|GATEWAY_DEMO_SESSION_SECRET|CONTEXT_DEV_API_KEY/, "a server-side secret setting"],
  [/goalpulse\.accessToken/, "the old pasted-token store"],
  [/eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\./, "a JWT"],
  [/goal-intelligence:8|goal-management:8|\/internal\/v1\//, "a private service route"],
];

function files(dir) {
  return readdirSync(dir).flatMap((name) => {
    const p = join(dir, name);
    return statSync(p).isDirectory() ? files(p) : [p];
  });
}

let all;
try {
  all = files(root).filter((f) => /\.(js|css|html|json|txt)$/.test(f));
} catch {
  console.error(`check-bundle: ${root} not found; run \`npm run build\` first.`);
  process.exit(2);
}

const problems = [];
for (const file of all) {
  const text = readFileSync(file, "utf8");
  for (const [pattern, what] of FORBIDDEN) {
    if (pattern.test(text)) problems.push(`${file}: contains ${what}`);
  }
}

if (problems.length) {
  console.error(`check-bundle: FAIL\n  ${problems.join("\n  ")}`);
  process.exit(1);
}
console.log(`check-bundle: ok (${all.length} browser files scanned in ${root})`);

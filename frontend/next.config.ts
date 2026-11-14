import type { NextConfig } from "next";

// R2-D2-02: refuse to start or build without an explicit, valid runtime. These are the
// rules of src/lib/runtime.ts (unit-tested there), repeated here because next.config.ts
// can't safely import app code. The app re-checks them on load, so this is defense in
// depth: a demo build can never fall back to the mock. `next typegen` (typecheck) only
// reads routes and needs no runtime, so it is exempt.
function checkRuntime(env: NodeJS.ProcessEnv): void {
  const profile = env.NEXT_PUBLIC_GOALPULSE_PROFILE?.trim() ?? "";
  const mode = env.NEXT_PUBLIC_API_MODE?.trim() ?? "";
  const rawUrl = env.NEXT_PUBLIC_GATEWAY_URL?.trim() ?? "";
  const problems: string[] = [];
  if (!["test", "dev", "demo"].includes(profile)) problems.push("NEXT_PUBLIC_GOALPULSE_PROFILE must be test, dev or demo");
  if (!["mock", "gateway"].includes(mode)) problems.push("NEXT_PUBLIC_API_MODE must be mock or gateway");
  if (profile === "demo" && mode !== "gateway") problems.push("profile 'demo' requires NEXT_PUBLIC_API_MODE=gateway; the mock is refused");
  if (mode === "gateway") {
    try {
      const url = new URL(rawUrl);
      if (
        !["http:", "https:"].includes(url.protocol) ||
        url.username !== "" ||
        url.password !== "" ||
        url.pathname !== "/" ||
        url.search !== "" ||
        url.hash !== ""
      ) {
        problems.push("NEXT_PUBLIC_GATEWAY_URL must be an http(s) origin without credentials, path, query or fragment");
      }
    } catch {
      problems.push("NEXT_PUBLIC_GATEWAY_URL must be the http(s) origin of the public gateway");
    }
  }
  if (problems.length) {
    throw new Error(`GoalPulse frontend configuration:\n  - ${problems.join("\n  - ")}\nSee frontend/README.md#modes and .env.example.`);
  }
}

if (!process.argv.includes("typegen")) checkRuntime(process.env);

const nextConfig: NextConfig = {
  // Playwright runs its own `next dev` with NEXT_DIST_DIR=.next-e2e. Next 16 holds an
  // exclusive lock inside the dist dir while `next dev` runs, so sharing `.next` with a
  // developer's `npm run dev` made the e2e web server exit 1 (R2-D2-03).
  distDir: process.env.NEXT_DIST_DIR || ".next",
  cacheComponents: true,
  partialPrefetching: true,
};

export default nextConfig;

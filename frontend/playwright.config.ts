import { defineConfig, devices } from "@playwright/test";

// Browser end-to-end tests run the real UI against mock mode (recorded Goal Intelligence
// conversations), so they need no backend. Run: npm run e2e
const PORT = 3100;

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? "list" : [["list"], ["html", { open: "never" }]],
  timeout: 60_000,
  expect: { timeout: 10_000 },
  use: {
    baseURL: `http://localhost:${PORT}`,
    trace: "retain-on-failure",
  },
  projects: [
    { name: "chromium", use: { ...devices["Desktop Chrome"] }, testIgnore: /demo\.spec\.ts$/ },
    {
      // Handoff screencast (D2 integration handoff): `npm run demo` → demo-output/
      name: "demo",
      testMatch: /demo\.spec\.ts$/,
      use: {
        ...devices["Desktop Chrome"],
        viewport: { width: 1280, height: 800 },
        video: { mode: "on", size: { width: 1280, height: 800 } },
        launchOptions: { slowMo: 250 },
      },
    },
  ],
  webServer: {
    command: `npm run dev -- --port ${PORT}`,
    url: `http://localhost:${PORT}`,
    reuseExistingServer: !process.env.CI,
    timeout: 180_000,
    // Mock lane: recorded Goal Intelligence conversations, labelled "Mock mode" in the UI.
    // Own dist dir so a developer's `npm run dev` (which locks `.next`) can keep running.
    env: { NEXT_PUBLIC_API_MODE: "mock", NEXT_DIST_DIR: ".next-e2e" },
  },
});

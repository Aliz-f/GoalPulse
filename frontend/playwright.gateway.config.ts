import { defineConfig, devices } from "@playwright/test";

// Gateway lane (R2-D2-02): the real UI built in gateway mode with the demo profile,
// signing in through the HttpOnly cookie flow. Until D1's gateway (R2-D1-01) exists it
// talks to e2e/gateway/stub-gateway.mjs, a contract test double; set
// GOALPULSE_E2E_GATEWAY_URL to run the same specs against the real gateway instead.
// Run: npm run e2e:gateway
//
// Both servers use 127.0.0.1 so the page and the gateway are the same site and the
// SameSite=Lax cookie is sent, exactly as in the local demo.
const APP_PORT = 3101;
const STUB_PORT = 8100;
const APP = `http://127.0.0.1:${APP_PORT}`;
const realGateway = process.env.GOALPULSE_E2E_GATEWAY_URL;
const GATEWAY = realGateway ?? `http://127.0.0.1:${STUB_PORT}`;

export default defineConfig({
  testDir: "./e2e/gateway",
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? "list" : [["list"], ["html", { open: "never", outputFolder: "playwright-report-gateway" }]],
  timeout: 60_000,
  expect: { timeout: 10_000 },
  use: { ...devices["Desktop Chrome"], baseURL: APP, trace: "retain-on-failure" },
  webServer: [
    ...(realGateway
      ? []
      : [
          {
            command: `node e2e/gateway/stub-gateway.mjs ${STUB_PORT} ${APP}`,
            port: STUB_PORT,
            reuseExistingServer: !process.env.CI,
            timeout: 30_000,
          },
        ]),
    {
      command: `npm run dev -- --hostname 127.0.0.1 --port ${APP_PORT}`,
      url: APP,
      reuseExistingServer: !process.env.CI,
      timeout: 180_000,
      env: {
        NEXT_PUBLIC_GOALPULSE_PROFILE: "demo",
        NEXT_PUBLIC_API_MODE: "gateway",
        NEXT_PUBLIC_GATEWAY_URL: GATEWAY,
        // Own dist dir: may run while `npm run dev` or the mock lane holds theirs.
        NEXT_DIST_DIR: ".next-e2e-gw",
      },
    },
  ],
});

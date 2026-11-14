// R2-D2-02 browser checks in gateway mode (demo profile): cookie sign-in, persistence
// across reload and a browser restart, two-account isolation, no token in the browser.
// Against the stub by default; against D1's gateway with GOALPULSE_E2E_GATEWAY_URL and
// the GOALPULSE_E2E_USER_* / GOALPULSE_E2E_PASS_* accounts.
import { expect, test, type Page } from "@playwright/test";

import { view } from "../helpers";

const A = { user: process.env.GOALPULSE_E2E_USER_A ?? "demo-a", pass: process.env.GOALPULSE_E2E_PASS_A ?? "demo-a-pass" };
const B = { user: process.env.GOALPULSE_E2E_USER_B ?? "demo-b", pass: process.env.GOALPULSE_E2E_PASS_B ?? "demo-b-pass" };
// Stub data; with the real gateway, set these to a goal title each account owns.
const A_GOAL = process.env.GOALPULSE_E2E_GOAL_A ?? "Kindle Paperwhite under €180";
const B_GOAL = process.env.GOALPULSE_E2E_GOAL_B ?? "Café supplies every month";

async function signIn(page: Page, who: { user: string; pass: string }) {
  await page.getByLabel("Username").fill(who.user);
  await page.getByLabel("Password").fill(who.pass);
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
}

async function expectNoBrowserCredentials(page: Page) {
  const stored = await page.evaluate(() => ({
    local: Object.keys(localStorage),
    session: Object.keys(sessionStorage),
    cookie: document.cookie,
  }));
  expect(stored.local).toEqual([]);
  expect(stored.session).toEqual([]);
  expect(stored.cookie).not.toContain("goalpulse_session"); // HttpOnly: invisible to scripts
}

test("wrong password is refused, explained and not kept", async ({ page }) => {
  await page.goto("/goals");
  await expect(page.getByRole("heading", { name: "Sign in to GoalPulse" })).toBeVisible();
  await signIn(page, { user: A.user, pass: "not-the-password" });
  await expect(view(page).getByText("don't match a demo account")).toBeVisible();
  await expect(page.getByLabel("Password")).toHaveValue("");
  await expect(page.locator("header")).not.toContainText("Sign out");
});

test("a signed-in account survives reload and a browser restart, with no token in the page", async ({ page, browser }) => {
  await page.goto("/goals");
  await signIn(page, A);
  await expect(page.locator("header")).toContainText("Demo user A");
  await expect(view(page).getByRole("heading", { name: "My goals" })).toBeVisible();
  await expect(view(page).getByText(A_GOAL)).toBeVisible();
  await expect(page.locator("header")).not.toContainText("Mock mode");
  await expectNoBrowserCredentials(page);

  await page.reload();
  await expect(view(page).getByText(A_GOAL)).toBeVisible();

  // "Restart": a fresh browser context that only keeps the cookie jar.
  const state = await page.context().storageState();
  const restarted = await browser.newContext({ storageState: { cookies: state.cookies, origins: [] } });
  const again = await restarted.newPage();
  await again.goto("/goals");
  await expect(view(again).getByText(A_GOAL)).toBeVisible();
  await restarted.close();
});

test("two accounts never see each other's goals", async ({ page }) => {
  await page.goto("/goals");
  await signIn(page, A);
  await expect(view(page).getByText(A_GOAL)).toBeVisible();
  await expect(view(page).getByText(B_GOAL)).toHaveCount(0);
  const aGoalHref = await view(page).getByRole("link", { name: A_GOAL }).getAttribute("href");

  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page.getByRole("heading", { name: "Sign in to GoalPulse" })).toBeVisible();
  await signIn(page, B);
  await expect(page.locator("header")).toContainText("Demo user B");
  await expect(view(page).getByText(B_GOAL)).toBeVisible();
  await expect(view(page).getByText(A_GOAL)).toHaveCount(0);

  // B opening A's goal gets the same answer as a goal that doesn't exist.
  await page.goto(aGoalHref ?? "/goals");
  await expect(view(page).getByText("This doesn't exist or isn't yours.")).toBeVisible();
});

test("the new-goal form says where goal text goes", async ({ page }) => {
  await page.goto("/");
  await signIn(page, A);
  await expect(view(page).getByText(/sent to Anthropic's Claude/)).toBeVisible();
});

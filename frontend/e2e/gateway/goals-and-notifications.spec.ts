// R2-D2-04 browser checks in gateway mode (demo profile): what the goal page and the
// notification feed show comes from the evaluation and notification APIs, truthfully.
// Against the stub by default (data in e2e/gateway/stub-gateway.mjs); the real-stack
// run happens once R2-D1-01 and R2-D4-02 land.
import { expect, test, type Page } from "@playwright/test";

import { view } from "../helpers";

const A = { user: process.env.GOALPULSE_E2E_USER_A ?? "demo-a", pass: process.env.GOALPULSE_E2E_PASS_A ?? "demo-a-pass" };
const B = { user: process.env.GOALPULSE_E2E_USER_B ?? "demo-b", pass: process.env.GOALPULSE_E2E_PASS_B ?? "demo-b-pass" };
const UNKNOWN_GOAL = process.env.GOALPULSE_E2E_UNKNOWN_GOAL ?? "Kindle Paperwhite under €180";
const VERIFIED_GOAL = process.env.GOALPULSE_E2E_VERIFIED_GOAL ?? "RTX 5070 under €600";

async function signIn(page: Page, who: { user: string; pass: string }, path = "/goals") {
  await page.goto(path);
  await page.getByLabel("Username").fill(who.user);
  await page.getByLabel("Password").fill(who.pass);
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page.getByRole("button", { name: "Sign out" })).toBeVisible();
}

const panel = (page: Page) => view(page).locator("section.evaluation");

test("a recorded unknown result shows Unknown with its check time, never Verified", async ({ page }) => {
  await signIn(page, A);
  await view(page).getByRole("link", { name: UNKNOWN_GOAL }).click();
  await expect(panel(page).getByText("Not enough evidence")).toBeVisible();
  await expect(panel(page).getByText(/^Last completed check:/)).toBeVisible();
  await expect(panel(page).locator("time")).toHaveAttribute("datetime", /Z$/);
  await expect(panel(page).locator(".condition-unknown").first()).toContainText("Unknown");
  await expect(panel(page)).not.toContainText("Verified offer found");
  await expect(panel(page)).not.toContainText("%");
  await expect(panel(page).getByText(/limited search budget/)).toBeVisible();
});

test("a verified result shows Verified and exactly one notification, whose read state survives a restart", async ({ page, browser }) => {
  await signIn(page, A);
  await view(page).getByRole("link", { name: VERIFIED_GOAL }).click();
  await expect(panel(page).getByText("Verified offer found")).toBeVisible();
  await expect(panel(page).getByText(/Nothing has been bought/)).toBeVisible();

  await page.getByRole("link", { name: "Notifications" }).click();
  const items = view(page).locator(".notification");
  await expect(items).toHaveCount(1);
  await expect(items.first()).toContainText("Verified offer · not ordered");
  // The public Notification has no package link, so no package button in gateway mode.
  await expect(view(page).getByRole("link", { name: "View prepared purchase" })).toHaveCount(0);

  await items.first().getByRole("button", { name: "Mark as read" }).click();
  await expect(view(page).getByText("All read.")).toBeVisible();

  const state = await page.context().storageState();
  const restarted = await browser.newContext({ storageState: { cookies: state.cookies, origins: [] } });
  const again = await restarted.newPage();
  await again.goto("/notifications");
  await expect(view(again).locator(".notification")).toHaveCount(1);
  await expect(view(again).getByText("All read.")).toBeVisible();
  await restarted.close();
});

test("another account has an empty feed, and an unavailable evaluation service doesn't hide the goal", async ({ page }) => {
  await signIn(page, B, "/notifications");
  await expect(view(page).getByText(/No notifications yet/)).toBeVisible();

  await page.getByRole("link", { name: "My goals", exact: true }).click();
  await view(page).getByRole("link", { name: "Café supplies every month" }).click();
  await expect(view(page).getByRole("heading", { name: "Café supplies every month" })).toBeVisible();
  await expect(panel(page).getByText(/Check results are unavailable right now/)).toBeVisible();
  await expect(panel(page).getByRole("button", { name: "Try again" })).toBeVisible();
});

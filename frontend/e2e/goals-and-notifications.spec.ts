// D2-FE-03 / D2-FE-04 browser checks (mock mode; ICP-02 screens are mock-only until approved).
import { expect, test } from "@playwright/test";

import { view } from "./helpers";

test("My goals: statuses are worded honestly and filters show empty states", async ({ page }) => {
  await page.goto("/goals");
  const cards = view(page).locator(".goal-card");
  await expect(cards).toHaveCount(2);
  await expect(view(page).getByText("Verified offer found")).toBeVisible();
  await expect(view(page).getByText("Not enough evidence")).toBeVisible();
  await expect(view(page)).not.toContainText(/%|purchased/i);

  await page.getByRole("button", { name: "Paused", exact: true }).click();
  await expect(view(page).getByText("No paused goals.")).toBeVisible();
});

test("goal detail: pause, resume, then cancel only after a second click", async ({ page }) => {
  await page.goto("/goals");
  await view(page).locator(".goal-card h2 a").first().click();
  await expect(page).toHaveURL(/\/goals\/[0-9a-f-]{36}$/);

  await page.getByRole("button", { name: "Pause", exact: true }).click();
  await expect(view(page).locator(".badge").filter({ hasText: "Paused" })).toBeVisible();
  await page.getByRole("button", { name: "Resume", exact: true }).click();
  await expect(view(page).locator(".badge").filter({ hasText: "Watching" })).toBeVisible();

  await page.getByRole("button", { name: "Cancel goal", exact: true }).click();
  await expect(view(page).locator(".badge").filter({ hasText: "Watching" })).toBeVisible(); // not yet
  await page.getByRole("button", { name: "Yes, cancel goal" }).click();
  await expect(view(page).locator(".badge").filter({ hasText: "Cancelled" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Pause", exact: true })).toHaveCount(0);
});

test.describe("notifications and prepared purchase", () => {
  test.use({ permissions: ["clipboard-read", "clipboard-write"] });

  test("verified offer opens a read-only package that says it was not ordered", async ({ page }) => {
    await page.goto("/notifications");
    await expect(view(page).getByText("New", { exact: true }).first()).toBeVisible();
    await page.getByRole("link", { name: "View prepared purchase" }).first().click();

    await expect(page.getByRole("heading", { name: "Prepared purchase" })).toBeVisible();
    await expect(view(page).getByText("Not ordered.", { exact: true })).toBeVisible();
    await expect(view(page).getByText(/Re-check everything on the seller's site/)).toBeVisible();

    const seller = page.getByRole("link", { name: /shop\.example/ });
    await expect(seller).toHaveAttribute("href", "https://shop.example/products/rtx-5070-12gb");
    await expect(seller).toHaveAttribute("target", "_blank");
    await expect(seller).toHaveAttribute("rel", /noopener/);

    await page.getByRole("button", { name: "Copy details" }).click();
    await expect(view(page).getByText("Details copied.")).toBeVisible();
    const copied = await page.evaluate(() => navigator.clipboard.readText());
    expect(copied).toContain("Nothing has been ordered");
    await expect(view(page)).not.toContainText(/order (was )?placed/i);
  });

  test("expired package is shown as expired; unknown package is not found", async ({ page }) => {
    await page.goto("/notifications");
    await page.getByRole("link", { name: "View prepared purchase" }).nth(1).click();
    await expect(page.getByRole("heading", { name: "This prepared purchase expired" })).toBeVisible();

    await page.goto("/packages/00000000-0000-4000-8000-000000000000");
    await expect(view(page).getByText(/doesn't exist or isn't yours/)).toBeVisible();
  });
});

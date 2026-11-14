// D2-FE-02 browser acceptance: incomplete → clarified → reviewed → confirmed; stale 409
// reloads the summary; unsupported/incomplete drafts never offer confirm.
import { expect, test } from "@playwright/test";

import { answerUntilReady, CAFE_EXAMPLE, confirmButton, PRODUCT_EXAMPLE, revisionBadge, startGoal, view } from "./helpers";

test("product goal: clarify, review the exact revision, confirm, see it in My goals", async ({ page }) => {
  await startGoal(page, PRODUCT_EXAMPLE);

  await expect(view(page).getByText(/include shipping and taxes, with delivery to Finland\?/)).toBeVisible();
  await expect(view(page).getByText(/Still needed:.*shipping & tax/)).toBeVisible();
  await expect(confirmButton(page)).toHaveCount(0);

  await answerUntilReady(page);

  const review = page.getByLabel("Review and confirm");
  await expect(review).toContainText("it never places an order");
  await expect(revisionBadge(page)).toHaveText("Revision 5");
  await expect(confirmButton(page)).toHaveText(/Confirm revision 5/);
  const summary = view(page).locator(".summary");
  await expect(summary).toContainText("RTX 5070");
  await expect(summary).toContainText("New only");
  await expect(summary).toContainText("Never automatic");

  await confirmButton(page).click();
  await expect(page.getByRole("heading", { name: "Monitoring is active" })).toBeVisible();
  await expect(view(page).getByText(/Nothing has been bought/)).toBeVisible();

  await page.getByRole("link", { name: "My goals" }).last().click();
  await expect(page).toHaveURL(/\/goals$/);
  const newest = view(page).locator(".goal-card").first();
  await expect(newest).toContainText("RTX 5070");
  await expect(newest).toContainText("Waiting for first check");
  await expect(newest).toContainText("Watching");
});

test("café goal: items table and monthly target before confirm", async ({ page }) => {
  await startGoal(page, CAFE_EXAMPLE);
  await answerUntilReady(page);
  const summary = view(page).locator(".summary");
  await expect(summary.getByRole("table")).toContainText("Lactose-free milk");
  await expect(summary.getByRole("table")).toContainText("20 L");
  await expect(summary).toContainText("212.50");
  await expect(confirmButton(page)).toBeEnabled();
});

test("edit from another tab: 409 reloads the latest revision and keeps the typed text", async ({ page }) => {
  await startGoal(page, PRODUCT_EXAMPLE);
  await expect(revisionBadge(page)).toHaveText("Revision 1");

  await page.getByRole("button", { name: "Simulate an edit from another tab" }).click();
  const box = page.getByRole("textbox", { name: "Your answer" });
  await box.fill("yes");
  await page.getByRole("button", { name: "Send", exact: true }).click();

  await expect(view(page).getByRole("alert")).toContainText("We loaded the latest version");
  await expect(revisionBadge(page)).toHaveText("Revision 2");
  await expect(box).toHaveValue("yes");
});

test("409 on confirm reloads instead of activating a stale revision", async ({ page }) => {
  await startGoal(page, PRODUCT_EXAMPLE);
  await answerUntilReady(page);
  await page.getByRole("button", { name: "Simulate an edit from another tab" }).click();
  await confirmButton(page).click();
  await expect(view(page).getByRole("alert")).toContainText("review it before confirming");
  await expect(page.getByRole("heading", { name: "Monitoring is active" })).toHaveCount(0);
});

test("unsupported goal is explained and never offers confirm", async ({ page }) => {
  await startGoal(page, "Book me a flight to Rome under 200 EUR");
  await expect(view(page).getByText(/can't monitor this kind of goal yet/)).toBeVisible();
  await expect(view(page).getByText(/Travel can't be tracked yet/)).toBeVisible();
  await expect(confirmButton(page)).toHaveCount(0);
});

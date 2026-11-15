import { expect, type Page } from "@playwright/test";

export const PRODUCT_EXAMPLE = "I want to buy an RTX 5070 for under €600";
export const CAFE_EXAMPLE = /^Our café needs 20 L lactose-free milk/;

/** Start a goal from the home page and wait for the session page. */
export async function startGoal(page: Page, text: string | RegExp) {
  await page.goto("/");
  if (typeof text === "string" && !/RTX 5070 for under €600/.test(text)) {
    await page.getByLabel("What should GoalPulse watch for?").fill(text);
  } else {
    await page.getByRole("button", { name: text }).click();
  }
  await page.getByRole("button", { name: "Start", exact: true }).click();
  await expect(page).toHaveURL(/\/sessions\/[0-9a-f-]{36}$/);
}

/**
 * The visible page. Next 16 (cacheComponents) keeps recently visited routes mounted but
 * hidden, and its route announcer also has role="alert", so page-wide text/CSS locators
 * can match invisible copies. Scope assertions to this.
 */
export const view = (page: Page) => page.locator("main").filter({ visible: true });

export const revisionBadge = (page: Page) => view(page).locator(".summary .badge").first();
export const confirmButton = (page: Page) => page.getByRole("button", { name: /^Confirm revision \d+ and start monitoring$/ });

/** Answer with the scripted mock replies until the Review and confirm panel appears. */
export async function answerUntilReady(page: Page) {
  for (let turn = 0; turn < 10; turn++) {
    if (await confirmButton(page).isVisible()) return;
    const before = await revisionBadge(page).textContent();
    const approve = page.getByRole("button", { name: "Approve this summary", exact: true });
    if (await approve.isVisible()) {
      await approve.click();
    } else {
      await page.getByRole("button", { name: /^Scripted reply:/ }).click();
      await page.getByRole("button", { name: "Send", exact: true }).click();
    }
    await expect(revisionBadge(page)).not.toHaveText(before ?? "");
  }
  throw new Error("draft never became ready for confirmation");
}

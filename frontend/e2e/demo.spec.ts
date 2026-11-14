// Handoff screencast for the team (D2 "integration handoff": UI screencast of the
// clarification and explicit confirm flow). Not part of the regular e2e run.
//   npm run demo   →  demo-output/*.png (numbered steps) + demo-output/*.webm (video)
// Uses mock mode: replies are recorded from the real Goal Intelligence service.
import { mkdirSync } from "node:fs";

import { expect, test, type Page } from "@playwright/test";

import { confirmButton, revisionBadge, view } from "./helpers";

const OUT = "demo-output";
mkdirSync(OUT, { recursive: true });

let step = 0;
async function shot(page: Page, name: string) {
  step += 1;
  await page.waitForTimeout(400); // let the UI settle so the frame is clean
  await page.screenshot({ path: `${OUT}/${String(step).padStart(2, "0")}-${name}.png`, fullPage: true });
}

async function say(page: Page, label: string, text: string) {
  const box = page.getByLabel(label);
  await box.click();
  await box.pressSequentially(text, { delay: 18 });
}

test("demo: describe → clarify → review → confirm → track", async ({ page }) => {
  test.setTimeout(180_000);
  step = 0;

  await page.goto("/");
  await expect(page.getByRole("heading", { name: /Tell GoalPulse/ })).toBeVisible();
  await shot(page, "start");

  await say(page, "What should GoalPulse watch for?", "I want to buy an RTX 5070 for under €600");
  await shot(page, "describe-goal");
  await page.getByRole("button", { name: "Start", exact: true }).click();
  await expect(page).toHaveURL(/\/sessions\//);
  await expect(revisionBadge(page)).toHaveText("Revision 1");
  await shot(page, "first-clarifying-question");

  for (let turn = 0; turn < 8 && !(await confirmButton(page).isVisible()); turn++) {
    const chip = page.getByRole("button", { name: /^Scripted reply:/ });
    const reply = ((await chip.textContent()) ?? "").replace(/^Scripted reply:\s*/, "");
    await say(page, "Your answer", reply);
    const before = await revisionBadge(page).textContent();
    await page.getByRole("button", { name: "Send", exact: true }).click();
    await expect(revisionBadge(page)).not.toHaveText(before ?? "");
    await shot(page, `answer-${turn + 1}`);
  }

  const review = page.getByLabel("Review and confirm");
  await expect(review).toBeVisible();
  await review.scrollIntoViewIfNeeded();
  await shot(page, "review-exact-revision");

  await confirmButton(page).click();
  await expect(page.getByRole("heading", { name: "Monitoring is active" })).toBeVisible();
  await shot(page, "confirmed-not-bought");

  await view(page).getByRole("link", { name: "My goals" }).click();
  await expect(view(page).locator(".goal-card").first()).toContainText("Waiting for first check");
  await shot(page, "my-goals");

  await page.getByRole("link", { name: "Notifications" }).click();
  await expect(view(page).getByRole("heading", { name: "Notifications" })).toBeVisible();
  await shot(page, "notifications");

  await view(page).getByRole("link", { name: "View prepared purchase" }).first().click();
  await expect(view(page).getByText("Not ordered.", { exact: true })).toBeVisible();
  await shot(page, "prepared-purchase-not-ordered");
});

test("demo: edited in another tab → 409 → latest revision reloaded", async ({ page }) => {
  test.setTimeout(120_000);
  await page.goto("/");
  await say(page, "What should GoalPulse watch for?", "I want to buy an RTX 5070 for under €600");
  await page.getByRole("button", { name: "Start", exact: true }).click();
  await expect(revisionBadge(page)).toHaveText("Revision 1");

  await page.getByRole("button", { name: "Simulate an edit from another tab" }).click();
  await say(page, "Your answer", "yes");
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await expect(view(page).getByRole("alert")).toContainText("We loaded the latest version");
  await expect(revisionBadge(page)).toHaveText("Revision 2");
  await page.waitForTimeout(400);
  await page.screenshot({ path: `${OUT}/zz-stale-revision-409.png`, fullPage: true });
});

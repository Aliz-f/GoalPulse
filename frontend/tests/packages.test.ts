import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { ApiError, adviceFor } from "../src/lib/api/errors.ts";
import { createMockApi } from "../src/lib/api/mock.ts";
import { isExpired, offerRows, packageText, safeHref } from "../src/lib/packages.ts";
import type { ActionPackage } from "../src/lib/proposed.ts";

const pkg: ActionPackage = {
  package_id: "p1",
  goal_id: "g1",
  goal_version: 1,
  evaluation_id: "e1",
  created_at: "2026-11-14T10:00:00Z",
  expires_at: "2026-11-15T10:00:00Z",
  approval_required: true,
  auto_execute: false,
  next_action: "request_user_approval",
  requires_checkout_revalidation: true,
  seller_offers: [
    {
      seller_domain: "shop.example",
      source_url: "javascript:alert(1)",
      observation_id: "o1",
      item_name: "Widget",
      quantity: "2",
      unit: "pcs",
      total_item_eur_cents: 1000,
    },
  ],
};

describe("package presentation", () => {
  it("only links http(s) sources", () => {
    assert.equal(safeHref("https://shop.example/x"), "https://shop.example/x");
    assert.equal(safeHref("javascript:alert(1)"), null);
    assert.equal(safeHref("not a url"), null);
    assert.equal(offerRows(pkg.seller_offers)[0].href, null);
  });

  it("shows missing shipping/tax as not stated, never zero", () => {
    const [row] = offerRows(pkg.seller_offers, "en-IE");
    assert.equal(row.shipping, "Not stated");
    assert.equal(row.tax, "Not stated");
    assert.equal(row.itemTotal, "€10.00");
  });

  it("copy text says nothing was ordered and to re-check", () => {
    const text = packageText(pkg, "Buy widgets", "en-IE");
    assert.match(text, /Nothing has been ordered/);
    assert.match(text, /Re-check everything/);
    assert.doesNotMatch(text, /order (was )?placed|purchased successfully|confirmed order/i);
  });

  it("expiry is checked against now", () => {
    assert.equal(isExpired(pkg, new Date("2026-11-15T09:59:00Z")), false);
    assert.equal(isExpired(pkg, new Date("2026-11-15T10:00:00Z")), true);
    assert.equal(isExpired({ expires_at: "nonsense" }), true);
  });

  it("410 advice explains the package expired", () => {
    assert.match(adviceFor(new ApiError(410, "gone", "x"), "load").message, /expired/);
  });
});

describe("mock notifications and packages (ICP-02 proposal)", () => {
  it("feed links to a valid prepare-only package; mark-read is idempotent", async () => {
    const api = createMockApi({ delayMs: 0 });
    const feed = await api.listNotifications();
    assert.equal(feed.length, 2);
    assert.ok(feed[0].created_at > feed[1].created_at, "newest first");
    const fresh = feed.find((n) => !n.is_read)!;
    const p = await api.getActionPackage(fresh.action_package_id!);
    assert.equal(p.auto_execute, false);
    assert.equal(p.approval_required, true);
    assert.equal(p.requires_checkout_revalidation, true);
    assert.equal(p.goal_id, fresh.goal_id);
    await api.markNotificationRead(fresh.notification_id);
    await api.markNotificationRead(fresh.notification_id);
    assert.ok((await api.listNotifications()).every((n) => n.is_read));
  });

  it("expired package is 410 and unknown/foreign package is 404", async () => {
    const api = createMockApi({ delayMs: 0 });
    const old = (await api.listNotifications()).find((n) => n.is_read)!;
    await assert.rejects(api.getActionPackage(old.action_package_id!), (e: ApiError) => e.status === 410);
    await assert.rejects(api.getActionPackage("someone-elses"), (e: ApiError) => e.status === 404);
    await assert.rejects(api.markNotificationRead("someone-elses"), (e: ApiError) => e.status === 404);
  });

  it("no notifications without seeded verified goals", async () => {
    assert.deepEqual(await createMockApi({ delayMs: 0, seedGoals: false }).listNotifications(), []);
  });
});

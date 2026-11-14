import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { formatDuration, formatEurCents } from "../src/lib/format.ts";
import { MOCK_SCRIPTS } from "../src/lib/api/mock-scripts.ts";
import { isUnsupported, labelForField, summarizeCandidate } from "../src/lib/summary.ts";

const last = (name: string) => {
  const s = MOCK_SCRIPTS.find((x) => x.name === name)!;
  return s.steps[s.steps.length - 1].draft;
};
const rows = (summary: ReturnType<typeof summarizeCandidate>) =>
  Object.fromEntries(summary.sections.flatMap((s) => s.rows.map((r) => [r.label, r])));

describe("format", () => {
  it("formats integer cents as EUR and refuses fractional cents", () => {
    assert.equal(formatEurCents(60000, "en-IE"), "€600.00");
    assert.equal(formatEurCents(21250, "en-IE"), "€212.50");
    assert.throws(() => formatEurCents(599.5));
  });
  it("formats durations", () => {
    assert.equal(formatDuration(1440, true), "day");
    assert.equal(formatDuration(60), "1 hour");
    assert.equal(formatDuration(360), "6 hours");
    assert.equal(formatDuration(45), "45 minutes");
  });
});

describe("summarizeCandidate", () => {
  it("renders a ready product goal with every constraint and default visible", () => {
    const s = summarizeCandidate(last("product").candidate_goal);
    assert.equal(s.goalType, "product_purchase");
    const r = rows(s);
    assert.equal(r["Product"].value, "RTX 5070");
    assert.equal(r["Condition"].value, "New only");
    assert.equal(r["Match"].value, "Equivalent models OK");
    assert.match(r["Budget (total)"].value, /600/);
    assert.match(r["Delivery"].value, /Finland, shipping and tax included/);
    assert.equal(r["Check offers"].source, "proposed");
    assert.match(r["Buying"].value, /Never automatic/);
    assert.ok(!Object.values(r).some((x) => x.missing));
  });

  it("renders procurement items including empty requirements as None", () => {
    const s = summarizeCandidate(last("procurement").candidate_goal);
    assert.equal(s.items.length, 2);
    assert.ok(s.items.every((i) => i.amount !== "Not set yet"));
    const r = rows(s);
    assert.match(r["Target (max per month)"].value, /212/);
  });

  it("shows partial drafts as 'Not set yet' instead of guessing", () => {
    const first = MOCK_SCRIPTS.find((x) => x.name === "product")!.steps[0].draft;
    const r = rows(summarizeCandidate(first.candidate_goal));
    assert.equal(r["Condition"].missing, true);
    assert.equal(r["Delivery"].missing, true);
    assert.equal(r["Product"].value, "RTX 5070");
  });

  it("ignores malformed or hostile candidate values", () => {
    const s = summarizeCandidate({
      goal_type: "product_purchase",
      criteria: { product: { query: 42 }, max_total_eur_cents: 599.99, include_shipping_and_tax: "yes" },
      actions: { auto_purchase: true },
    });
    const r = rows(s);
    assert.equal(r["Product"].missing, true);
    assert.equal(r["Budget (total)"].missing, true);
    assert.equal(r["Delivery"].missing, true);
    assert.match(r["Buying"].value, /Never automatic/);
    assert.equal(summarizeCandidate({ unsupported_request: "Travel" }).goalType, null);
  });

  it("labels missing fields and detects unsupported drafts", () => {
    assert.equal(labelForField("include_shipping_and_tax"), "shipping & tax");
    assert.equal(labelForField("something_new"), "something new");
    assert.ok(isUnsupported(MOCK_SCRIPTS.find((x) => x.name === "unsupported")!.steps[0].draft.missing_fields));
  });
});

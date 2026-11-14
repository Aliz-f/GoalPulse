// R2-D2-04: evaluation results are shown truthfully; notification/evaluation routes match the contract.
import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { ApiError } from "../src/lib/api/errors.ts";
import { createGatewayApi } from "../src/lib/api/gateway.ts";
import { createMockApi } from "../src/lib/api/mock.ts";
import { conditionLabel, headlineFor, isConsistentlyVerified, progressText, summarizeEvaluations } from "../src/lib/evaluations.ts";
import { timestampTitle } from "../src/lib/format.ts";
import type { GoalEvaluation } from "../src/lib/extension.ts";

const GOAL = "11111111-1111-4111-8111-111111111111";
const OBS = "22222222-2222-4222-8222-222222222222";

function evaluation(over: Partial<GoalEvaluation> = {}): GoalEvaluation {
  return {
    evaluation_id: crypto.randomUUID(),
    goal_id: GOAL,
    goal_version: 2,
    evaluated_at: "2026-11-14T10:00:00Z",
    status: "unknown",
    conditions: [
      { code: "identity_match", status: "satisfied", reason: "Model matches", observation_ids: [OBS] },
      { code: "shipping_known", status: "unknown", reason: "Shipping to FI not stated", observation_ids: [] },
      { code: "total_within_budget", status: "unsatisfied", reason: "EUR 640 is over EUR 600", observation_ids: [OBS] },
    ],
    candidate_observation_ids: [],
    is_opportunity_verified: false,
    ...over,
  };
}

const allMet = (e: GoalEvaluation) => e.conditions.map((c) => ({ ...c, status: "satisfied" as const, observation_ids: [OBS] }));

describe("evaluation headline", () => {
  it("recorded unknown renders Not enough evidence, never Verified", () => {
    const h = headlineFor(evaluation());
    assert.equal(h.label, "Not enough evidence");
    assert.equal(h.verified, false);
    assert.match(h.detail, /not a failure and not a success/);
  });

  it("verified only when the record is consistent", () => {
    const good = evaluation({ status: "satisfied", is_opportunity_verified: true, candidate_observation_ids: [OBS] });
    good.conditions = allMet(good);
    assert.equal(headlineFor(good).label, "Verified offer found");
    assert.match(headlineFor(good).detail, /Nothing has been bought/);

    // A flag that contradicts the rest of the record is not trusted.
    const flagOnly = evaluation({ is_opportunity_verified: true });
    assert.equal(isConsistentlyVerified(flagOnly), false);
    assert.equal(headlineFor(flagOnly).label, "Not enough evidence");
    const noEvidence = { ...good, candidate_observation_ids: [] };
    assert.equal(headlineFor(noEvidence).verified, false);
    const satisfiedNotVerified = { ...good, is_opportunity_verified: false };
    assert.equal(headlineFor(satisfiedNotVerified).label, "Possible offer");
    assert.equal(headlineFor(evaluation({ status: "unsatisfied" })).label, "No matching offer");
  });
});

describe("summarizeEvaluations", () => {
  it("uses the newest check of the CURRENT goal version only", () => {
    const old = evaluation({ goal_version: 1, evaluated_at: "2026-11-14T12:00:00Z", status: "satisfied" });
    const newest = evaluation({ evaluated_at: "2026-11-14T11:00:00Z" });
    const earlier = evaluation({ evaluated_at: "2026-11-14T09:00:00Z" });
    const other = evaluation({ goal_id: "33333333-3333-4333-8333-333333333333", evaluated_at: "2026-11-15T00:00:00Z" });
    const s = summarizeEvaluations([earlier, old, other, newest], GOAL, 2);
    assert.equal(s.kind, "result");
    if (s.kind !== "result") return;
    assert.equal(s.latest.evaluation_id, newest.evaluation_id);
    assert.deepEqual(
      s.history.map((e) => e.evaluation_id),
      [earlier.evaluation_id],
    );
    assert.equal(s.olderVersionCount, 1);
  });

  it("orders RFC 3339 timestamps by instant, not by their written offsets", () => {
    const newer = evaluation({ evaluation_id: "newer", evaluated_at: "2026-11-14T09:00:00Z" });
    const older = evaluation({ evaluation_id: "older", evaluated_at: "2026-11-14T10:00:00+02:00" });
    const s = summarizeEvaluations([older, newer], GOAL, 2);
    assert.ok(s.kind === "result");
    if (s.kind !== "result") return;
    assert.equal(s.latest.evaluation_id, "newer");
    assert.equal(timestampTitle(older.evaluated_at), "2026-11-14T08:00:00.000Z (UTC)");
  });

  it("lists failed conditions first, then unknown, then met, with evidence counts", () => {
    const s = summarizeEvaluations([evaluation()], GOAL, 2);
    assert.ok(s.kind === "result");
    if (s.kind !== "result") return;
    assert.deepEqual(
      s.conditions.map((c) => [c.label, c.statusLabel, c.evidenceCount]),
      [
        ["Total within budget", "Not met", 1],
        ["Shipping cost known", "Unknown", 0],
        ["Right product", "Met", 1],
      ],
    );
  });

  it("is waiting when there is no check for this version", () => {
    assert.deepEqual(summarizeEvaluations([], GOAL, 2), { kind: "none", olderVersionCount: 0 });
    assert.deepEqual(summarizeEvaluations([evaluation({ goal_version: 1 })], GOAL, 2), { kind: "none", olderVersionCount: 1 });
  });

  it("names unknown condition codes readably", () => {
    assert.equal(conditionLabel("full_basket_coverage"), "Every item found");
    assert.equal(conditionLabel("new_code_from_ge"), "New code from ge");
  });
});

describe("progress", () => {
  it("shows a percentage only when Goal Evaluation reported one for a verified offer", () => {
    const verified = evaluation({ status: "satisfied", is_opportunity_verified: true, candidate_observation_ids: [OBS] });
    verified.conditions = allMet(verified);
    assert.match(progressText({ ...verified, progress: { metric: "savings_progress", value: 62.5, unit: "percent" } }) ?? "", /62\.5%/);
    assert.equal(progressText({ ...evaluation(), progress: { metric: "savings_progress", value: 62.5, unit: "percent" } }), null);
    assert.equal(progressText({ ...verified, progress: { metric: "not_applicable", value: null, unit: "none" } }), null);
    assert.equal(progressText(verified), null);
  });
});

describe("gateway extension routes", () => {
  it("calls the contract paths with clamped limits, and PATCH read", async () => {
    const calls: string[] = [];
    const api = createGatewayApi({
      baseUrl: "http://gw",
      fetchImpl: async (url, init) => {
        calls.push(`${init.method} ${url}`);
        return init.method === "PATCH" ? new Response(null, { status: 204 }) : Response.json([]);
      },
    });
    await api.listEvaluations("g 1");
    await api.listEvaluations("g1", 500);
    await api.listNotifications(50);
    await api.markNotificationRead("n/1");
    assert.deepEqual(calls, [
      "GET http://gw/v1/goals/g%201/evaluations?limit=20",
      "GET http://gw/v1/goals/g1/evaluations?limit=100",
      "GET http://gw/v1/notifications?limit=50",
      "PATCH http://gw/v1/notifications/n%2F1/read",
    ]);
  });

  it("cross-owner reads are a plain 404", async () => {
    const api = createGatewayApi({ baseUrl: "http://gw", fetchImpl: async () => new Response(null, { status: 404 }) });
    await assert.rejects(api.listEvaluations("theirs"), (e: ApiError) => e.kind === "not_found");
    await assert.rejects(api.markNotificationRead("theirs"), (e: ApiError) => e.kind === "not_found");
  });
});

describe("mock lane mirrors the contract", () => {
  it("seeded verified goal is verified; the café goal is unknown; notifications are contract-shaped", async () => {
    const api = createMockApi({ delayMs: 0 });
    const goals = await api.listGoals();
    for (const g of goals) {
      const spec = await api.getGoal(g.goal_id);
      const s = summarizeEvaluations(await api.listEvaluations(g.goal_id), g.goal_id, spec.version);
      assert.ok(s.kind === "result");
      if (s.kind !== "result") continue;
      assert.equal(s.headline.verified, g.opportunity_status === "verified");
    }
    const keys = new Set(["notification_id", "goal_id", "goal_version", "title", "summary", "created_at", "is_read"]);
    for (const n of await api.listNotifications()) assert.deepEqual(new Set(Object.keys(n)), keys);
    await assert.rejects(api.listEvaluations("nope"), (e: ApiError) => e.status === 404);
  });
});

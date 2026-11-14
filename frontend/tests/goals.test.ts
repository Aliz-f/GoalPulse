import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { ApiError } from "../src/lib/api/errors.ts";
import { createGatewayApi } from "../src/lib/api/gateway.ts";
import { createMockApi } from "../src/lib/api/mock.ts";
import type { Lifecycle, OpportunityStatus } from "../src/lib/contracts.ts";
import { allowedActions, freshness, lifecycleAfter, opportunityView } from "../src/lib/goals.ts";

describe("status presentation", () => {
  it("never presents unknown as success or verified as purchased", () => {
    const unknown = opportunityView("unknown", "2026-11-14T10:00:00Z");
    assert.notEqual(unknown.tone, "positive");
    assert.doesNotMatch(unknown.label, /found|achieved|done|match/i);
    const verified = opportunityView("verified", "2026-11-14T10:00:00Z");
    assert.equal(verified.tone, "positive");
    assert.match(verified.detail, /Nothing has been bought/);
    for (const s of ["verified", "candidate", "unknown", "not_found"] as OpportunityStatus[]) {
      const v = opportunityView(s, "2026-11-14T10:00:00Z");
      assert.doesNotMatch(`${v.label} ${v.detail}`, /purchased|ordered|\d+\s?%/i);
    }
  });

  it("goals never evaluated say so instead of reporting a result", () => {
    assert.equal(opportunityView("unknown", null).label, "Waiting for first check");
    assert.equal(opportunityView("not_found", undefined).label, "Waiting for first check");
  });

  it("freshness is relative and honest about missing timestamps", () => {
    const now = new Date("2026-11-14T12:00:00Z");
    assert.equal(freshness(null, now), "Not checked yet");
    assert.equal(freshness("2026-11-14T11:35:00Z", now), "Checked 25 minutes ago");
    assert.equal(freshness("2026-11-14T09:00:00Z", now), "Checked 3 hours ago");
    assert.equal(freshness("2026-11-10T12:00:00Z", now), "Checked 4 days ago");
    assert.equal(freshness("garbage", now), "Last check time unavailable");
  });

  it("mirrors the Goal Management transition matrix", () => {
    assert.deepEqual(allowedActions("active"), ["pause", "complete", "cancel"]);
    assert.deepEqual(allowedActions("paused"), ["resume", "complete", "cancel"]);
    for (const terminal of ["completed", "cancelled"] as Lifecycle[]) assert.deepEqual(allowedActions(terminal), []);
    assert.equal(lifecycleAfter("active", "resume"), null);
    assert.equal(lifecycleAfter("paused", "resume"), "active");
  });
});

describe("mock goals", () => {
  it("lists seeded and newly confirmed goals, newest first, with honest initial status", async () => {
    const api = createMockApi({ delayMs: 0 });
    const seeded = await api.listGoals();
    assert.equal(seeded.length, 2);
    assert.deepEqual(new Set(seeded.map((g) => g.opportunity_status)), new Set(["verified", "unknown"]));

    let d = await api.createSession("I want to buy an RTX 5070 for under €600");
    while (!d.ready_for_confirmation) d = await api.sendMessage(d.session_id, api.suggestedReply(d.session_id)!, d.revision);
    const spec = await api.confirm(d.session_id, d.revision);

    const all = await api.listGoals();
    assert.equal(all.length, 3);
    assert.equal(all[0].goal_id, spec.goal_id);
    assert.equal(all[0].lifecycle, "active");
    assert.equal(all[0].last_evaluated_at, null);
    assert.deepEqual(await api.getGoal(spec.goal_id), spec);
  });

  it("applies valid lifecycle actions and 409s invalid ones", async () => {
    const api = createMockApi({ delayMs: 0 });
    const [goal] = await api.listGoals();
    await api.updateLifecycle(goal.goal_id, "pause");
    assert.equal((await api.listGoals("paused")).length, 1);
    await assert.rejects(api.updateLifecycle(goal.goal_id, "pause"), (e: ApiError) => e.status === 409);
    await api.updateLifecycle(goal.goal_id, "cancel");
    await assert.rejects(api.updateLifecycle(goal.goal_id, "resume"), (e: ApiError) => e.status === 409);
    await assert.rejects(api.getGoal("missing"), (e: ApiError) => e.status === 404);
  });

  it("can start empty", async () => {
    assert.deepEqual(await createMockApi({ delayMs: 0, seedGoals: false }).listGoals(), []);
  });
});

describe("gateway goal routes", () => {
  it("uses the frozen paths and handles 204", async () => {
    const calls: { url: string; init: RequestInit }[] = [];
    const api = createGatewayApi({
      baseUrl: "http://gw",
      getToken: () => "t",
      fetchImpl: async (url, init) => {
        calls.push({ url, init });
        return init.method === "PATCH" ? new Response(null, { status: 204 }) : Response.json([]);
      },
    });
    await api.listGoals("active");
    await api.listGoals();
    await api.getGoal("g 1");
    assert.equal(await api.updateLifecycle("g1", "pause"), undefined);
    assert.deepEqual(
      calls.map((c) => `${c.init.method} ${c.url}`),
      ["GET http://gw/v1/goals?status=active", "GET http://gw/v1/goals", "GET http://gw/v1/goals/g%201", "PATCH http://gw/v1/goals/g1/lifecycle"],
    );
    assert.deepEqual(JSON.parse(String(calls[3].init.body)), { action: "pause" });
  });
});

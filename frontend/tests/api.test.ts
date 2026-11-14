// Run: npm test   (node:test with built-in TypeScript type stripping; no extra deps)
import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { adviceFor, ApiError } from "../src/lib/api/errors.ts";
import { createGatewayApi, normalizeBaseUrl } from "../src/lib/api/gateway.ts";
import { createMockApi, pickScript } from "../src/lib/api/mock.ts";
import { MOCK_SCRIPTS } from "../src/lib/api/mock-scripts.ts";

const ids = () => {
  let n = 0;
  return () => `00000000-0000-4000-8000-${String(++n).padStart(12, "0")}`;
};

describe("mock api (recorded from Goal Intelligence)", () => {
  it("walks the product script to ready and confirms the exact revision", async () => {
    const api = createMockApi({ delayMs: 0, newId: ids(), now: () => new Date("2026-11-14T12:00:00.123Z") });
    let d = await api.createSession("I want to buy an RTX 5070 for under €600");
    assert.equal(d.revision, 1);
    assert.equal(d.ready_for_confirmation, false);
    await assert.rejects(api.confirm(d.session_id, d.revision), (e: ApiError) => e.status === 409);
    while (!d.ready_for_confirmation) {
      const reply = api.suggestedReply(d.session_id);
      assert.ok(reply, "script ran out before ready");
      d = await api.sendMessage(d.session_id, reply, d.revision);
    }
    assert.equal(d.state, "ready_for_confirmation");
    assert.deepEqual(d.missing_fields, []);
    assert.equal(d.next_question, null);
    const spec = await api.confirm(d.session_id, d.revision);
    assert.equal(spec.version, 1);
    assert.equal(spec.approved_at, "2026-11-14T12:00:00Z");
    assert.deepEqual(spec.actions, { notify_user: true, agent_handoff: "prepare_only", auto_purchase: false });
    assert.equal(spec.criteria.kind, "product_purchase");
    // confirmed drafts accept nothing more
    await assert.rejects(api.sendMessage(d.session_id, "x", d.revision), (e: ApiError) => e.status === 409);
    await assert.rejects(api.confirm(d.session_id, d.revision), (e: ApiError) => e.status === 409);
  });

  it("returns 409 with the current revision after a concurrent edit", async () => {
    const api = createMockApi({ delayMs: 0, newId: ids() });
    const d = await api.createSession("I want to buy an RTX 5070");
    api.simulateConcurrentEdit(d.session_id);
    await assert.rejects(api.sendMessage(d.session_id, "yes", d.revision), (e: ApiError) => {
      assert.equal(e.kind, "conflict");
      assert.equal(e.code, "conflict");
      return true;
    });
    const fresh = await api.getSession(d.session_id);
    assert.equal(fresh.revision, 2);
  });

  it("never lets an unsupported goal become ready", async () => {
    const api = createMockApi({ delayMs: 0, newId: ids() });
    const d = await api.createSession("Book me a flight to Rome");
    assert.ok(d.missing_fields.includes("supported_goal_type"));
    assert.equal(d.ready_for_confirmation, false);
  });

  it("picks scripts by intent and unknown sessions are 404", async () => {
    assert.equal(pickScript("Our café restocks every month").name, "procurement");
    assert.equal(pickScript("hotel in Lapland").name, "unsupported");
    assert.equal(pickScript("buy a Kindle").name, "product");
    const api = createMockApi({ delayMs: 0 });
    await assert.rejects(api.getSession("nope"), (e: ApiError) => e.status === 404);
  });

  it("recorded scripts satisfy the GoalDraft ready invariants", () => {
    for (const script of MOCK_SCRIPTS) {
      for (const { draft } of script.steps) {
        if (draft.ready_for_confirmation) {
          assert.deepEqual(draft.missing_fields, []);
          assert.equal(draft.next_question, null);
          assert.equal(draft.state, "ready_for_confirmation");
        }
      }
    }
  });
});

describe("gateway client (frozen public contract)", () => {
  type Call = { url: string; init: RequestInit };

  function fakeFetch(status: number, body: unknown, calls: Call[]) {
    return async (url: string, init: RequestInit) => {
      calls.push({ url, init });
      return new Response(body === undefined ? null : JSON.stringify(body), {
        status,
        headers: { "Content-Type": "application/json" },
      });
    };
  }

  it("uses the session cookie, a correlation id and exact bodies on /v1 paths; never a token", async () => {
    const calls: Call[] = [];
    const draft = { session_id: "s1", revision: 3 };
    const api = createGatewayApi({
      baseUrl: "http://localhost:8000/",
      fetchImpl: fakeFetch(200, draft, calls),
      newCorrelationId: () => "corr-1",
    });
    await api.sendMessage("s 1", "yes", 2);
    await api.confirm("s1", 3);
    assert.equal(calls[0].url, "http://localhost:8000/v1/goal-sessions/s%201/messages");
    assert.deepEqual(JSON.parse(String(calls[0].init.body)), { message: "yes", expected_revision: 2 });
    const headers = calls[0].init.headers as Record<string, string>;
    assert.equal(headers["X-Correlation-ID"], "corr-1");
    assert.equal(calls[1].url, "http://localhost:8000/v1/goal-sessions/s1/confirm");
    assert.deepEqual(JSON.parse(String(calls[1].init.body)), { expected_revision: 3, approved: true });
    for (const c of calls) {
      // identity is the HttpOnly cookie: sent by the browser, never read or set here
      assert.equal(c.init.credentials, "include");
      assert.equal(c.init.redirect, "error");
      const h = c.init.headers as Record<string, string>;
      assert.ok(!Object.keys(h).some((k) => /^(authorization|cookie)$/i.test(k)), "no auth header from the browser");
      // never sends an owner id: ownership comes from the session
      assert.ok(!String(c.init.body).includes("owner_user_id"));
    }
  });

  it("reports a 401 so the app can ask the user to sign in again", async () => {
    let expired = 0;
    const api = createGatewayApi({
      baseUrl: "http://gw",
      fetchImpl: fakeFetch(401, { code: "unauthorized", message: "session expired" }, []),
      onUnauthorized: () => expired++,
    });
    await assert.rejects(api.listGoals(), (e: ApiError) => e.kind === "unauthorized");
    assert.equal(expired, 1);
    // other failures don't sign the user out
    const busy = createGatewayApi({ baseUrl: "http://gw", fetchImpl: fakeFetch(503, {}, []), onUnauthorized: () => expired++ });
    await assert.rejects(busy.listGoals(), (e: ApiError) => e.kind === "unavailable");
    assert.equal(expired, 1);
  });

  it("sends nothing while signed out, and doesn't treat that as an expired session", async () => {
    const calls: Call[] = [];
    let expired = 0;
    let signedIn = false;
    const api = createGatewayApi({
      baseUrl: "http://gw",
      fetchImpl: fakeFetch(200, [], calls),
      onUnauthorized: () => expired++,
      canSend: () => signedIn,
    });
    await assert.rejects(api.listGoals(), (e: ApiError) => e.kind === "not_signed_in");
    assert.equal(calls.length, 0);
    assert.equal(expired, 0);
    signedIn = true;
    assert.deepEqual(await api.listGoals(), []);
    assert.equal(calls.length, 1);
  });

  it("maps the ICP-01 {code, message} error body, non-JSON success and network failures", async () => {
    const api = createGatewayApi({
      baseUrl: "http://gw",
      fetchImpl: fakeFetch(409, { code: "conflict", message: "stale revision; reload the draft" }, []),
    });
    await assert.rejects(api.sendMessage("s", "m", 6), (e: ApiError) => e.kind === "conflict" && e.code === "conflict" && e.message === "stale revision; reload the draft");
    const html = createGatewayApi({ baseUrl: "http://gw", fetchImpl: async () => new Response("<html>", { status: 200 }) });
    await assert.rejects(html.getSession("s"), (e: ApiError) => e.kind === "server");
    const offline = createGatewayApi({
      baseUrl: "http://gw",
      fetchImpl: async () => {
        throw new TypeError("Failed to fetch");
      },
    });
    await assert.rejects(offline.getSession("s"), (e: ApiError) => e.kind === "network");
  });

  it("only accepts an http(s) gateway origin", () => {
    assert.equal(normalizeBaseUrl("https://api.example.com/"), "https://api.example.com");
    assert.throws(() => normalizeBaseUrl(""));
    assert.throws(() => normalizeBaseUrl("javascript:alert(1)"));
    assert.throws(() => normalizeBaseUrl("http://gw/v1"), "an origin, not a path");
  });
});

describe("error advice", () => {
  it("409 reloads and keeps the typed text; 404 does not", () => {
    const conflict = adviceFor(new ApiError(409, "conflict", "stale", "conflict"), "send");
    assert.equal(conflict.reload, true);
    assert.equal(conflict.keepInput, true);
    assert.match(conflict.message, /loaded the latest version/);
    assert.equal(adviceFor(new ApiError(404, "not_found", "x"), "load").keepInput, false);
    assert.equal(adviceFor(new ApiError(409, "conflict", "x"), "confirm").reload, true);
    assert.equal(adviceFor(new Error("boom"), "send").reload, false);
  });
});

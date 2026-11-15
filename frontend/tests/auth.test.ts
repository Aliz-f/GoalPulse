// R2-D2-02: fail-closed runtime selection, demo cookie sign-in, session store.
import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { createDemoAuthApi, parseDemoUser, signInMessage, type DemoAuthApi, type DemoUser } from "../src/lib/api/demo-auth.ts";
import { ApiError } from "../src/lib/api/errors.ts";
import { resolveRuntime, RuntimeConfigError } from "../src/lib/runtime.ts";
import { createSessionStore, userIdOf } from "../src/lib/session.ts";

const ALICE: DemoUser = {
  user_id: "11111111-1111-4111-8111-111111111111",
  display_name: "Demo user A",
  expires_at: "2026-11-14T22:00:00Z",
};
const BOB: DemoUser = { ...ALICE, user_id: "22222222-2222-4222-8222-222222222222", display_name: "Demo user B" };

describe("runtime selection fails closed", () => {
  const gw = { NEXT_PUBLIC_API_MODE: "gateway", NEXT_PUBLIC_GATEWAY_URL: "http://localhost:8000/" };
  const demoCredentials = {
    NEXT_PUBLIC_DEMO_USER_A_USERNAME: "demo-a",
    NEXT_PUBLIC_DEMO_USER_A_PASSWORD: "demo-a-pass",
    NEXT_PUBLIC_DEMO_USER_B_USERNAME: "demo-b",
    NEXT_PUBLIC_DEMO_USER_B_PASSWORD: "demo-b-pass",
  };

  it("accepts the demo profile only with the real gateway", () => {
    assert.deepEqual(resolveRuntime({ NEXT_PUBLIC_GOALPULSE_PROFILE: "demo", ...gw, ...demoCredentials }), {
      profile: "demo",
      mode: "gateway",
      gatewayUrl: "http://localhost:8000",
      demoCredentials: [
        { label: "Demo account A", username: "demo-a", password: "demo-a-pass" },
        { label: "Demo account B", username: "demo-b", password: "demo-b-pass" },
      ],
    });
    assert.throws(
      () => resolveRuntime({ NEXT_PUBLIC_GOALPULSE_PROFILE: "demo", NEXT_PUBLIC_API_MODE: "mock" }),
      /profile 'demo' requires NEXT_PUBLIC_API_MODE=gateway/,
    );
  });

  it("refuses a demo build that cannot show both prepared logins", () => {
    assert.throws(
      () => resolveRuntime({ NEXT_PUBLIC_GOALPULSE_PROFILE: "demo", ...gw }),
      /requires both public demo usernames and passwords/,
    );
  });

  it("allows the labelled mock only when asked for explicitly in test/dev", () => {
    for (const profile of ["test", "dev"]) {
      const rt = resolveRuntime({ NEXT_PUBLIC_GOALPULSE_PROFILE: profile, NEXT_PUBLIC_API_MODE: "mock" });
      assert.equal(rt.mode, "mock");
      assert.equal(rt.gatewayUrl, null);
      assert.deepEqual(rt.demoCredentials, []);
    }
  });

  const bad: [string, Record<string, string | undefined>][] = [
    ["nothing set", {}],
    ["mode missing", { NEXT_PUBLIC_GOALPULSE_PROFILE: "dev" }],
    ["profile missing", { NEXT_PUBLIC_API_MODE: "mock" }],
    ["unknown mode", { NEXT_PUBLIC_GOALPULSE_PROFILE: "dev", NEXT_PUBLIC_API_MODE: "fixture" }],
    ["unknown profile", { NEXT_PUBLIC_GOALPULSE_PROFILE: "prod", ...gw }],
    ["blank mode", { NEXT_PUBLIC_GOALPULSE_PROFILE: "dev", NEXT_PUBLIC_API_MODE: "  " }],
    ["gateway without URL", { NEXT_PUBLIC_GOALPULSE_PROFILE: "dev", NEXT_PUBLIC_API_MODE: "gateway" }],
    ["gateway URL with a path", { NEXT_PUBLIC_GOALPULSE_PROFILE: "dev", NEXT_PUBLIC_API_MODE: "gateway", NEXT_PUBLIC_GATEWAY_URL: "http://gw/internal" }],
    ["gateway URL with credentials", { NEXT_PUBLIC_GOALPULSE_PROFILE: "dev", NEXT_PUBLIC_API_MODE: "gateway", NEXT_PUBLIC_GATEWAY_URL: "http://user:secret@gw" }],
    ["gateway URL with query", { NEXT_PUBLIC_GOALPULSE_PROFILE: "dev", NEXT_PUBLIC_API_MODE: "gateway", NEXT_PUBLIC_GATEWAY_URL: "http://gw?route=internal" }],
    ["gateway URL with fragment", { NEXT_PUBLIC_GOALPULSE_PROFILE: "dev", NEXT_PUBLIC_API_MODE: "gateway", NEXT_PUBLIC_GATEWAY_URL: "http://gw#internal" }],
  ];
  for (const [name, env] of bad) {
    it(`refuses: ${name}`, () => assert.throws(() => resolveRuntime(env), RuntimeConfigError));
  }
});

type Call = { method: string; url: string; init: RequestInit };

function fakeGateway(routes: Record<string, () => Response>, calls: Call[] = []) {
  return async (url: string, init: RequestInit) => {
    const key = `${init.method} ${new URL(url).pathname}`;
    calls.push({ method: String(init.method), url, init });
    const route = routes[key];
    if (!route) throw new Error(`unexpected ${key}`);
    return route();
  };
}

describe("demo sign-in client (gateway.phase2.extension.v1.yaml)", () => {
  it("posts DemoLogin with the cookie and keeps only the DemoUser fields", async () => {
    const calls: Call[] = [];
    const auth = createDemoAuthApi({
      baseUrl: "http://gw",
      fetchImpl: fakeGateway({ "POST /v2/demo/sessions": () => Response.json({ ...ALICE, token: "leaked" }) }, calls),
    });
    const user = await auth.signIn("alice", "pw");
    assert.deepEqual(user, ALICE, "an unexpected token field is dropped, never kept");
    assert.deepEqual(JSON.parse(String(calls[0].init.body)), { username: "alice", password: "pw" });
    assert.equal(calls[0].init.credentials, "include");
    assert.equal(calls[0].url, "http://gw/v2/demo/sessions");
  });

  it("reads the current session; 401 means signed out", async () => {
    const signedIn = createDemoAuthApi({ baseUrl: "http://gw", fetchImpl: fakeGateway({ "GET /v2/demo/session": () => Response.json(ALICE) }) });
    assert.deepEqual(await signedIn.current(), ALICE);
    const signedOut = createDemoAuthApi({
      baseUrl: "http://gw",
      fetchImpl: fakeGateway({ "GET /v2/demo/session": () => new Response(null, { status: 401 }) }),
    });
    assert.equal(await signedOut.current(), null);
    const disabled = createDemoAuthApi({
      baseUrl: "http://gw",
      fetchImpl: fakeGateway({ "GET /v2/demo/session": () => new Response(null, { status: 503 }) }),
    });
    await assert.rejects(disabled.current(), (e: ApiError) => e.status === 503);
  });

  it("logs out with 204, treats 401 as already signed out, and surfaces 403", async () => {
    const ok = createDemoAuthApi({ baseUrl: "http://gw", fetchImpl: fakeGateway({ "POST /v2/demo/sessions/logout": () => new Response(null, { status: 204 }) }) });
    await ok.signOut();
    const gone = createDemoAuthApi({ baseUrl: "http://gw", fetchImpl: fakeGateway({ "POST /v2/demo/sessions/logout": () => new Response(null, { status: 401 }) }) });
    await gone.signOut();
    const csrf = createDemoAuthApi({ baseUrl: "http://gw", fetchImpl: fakeGateway({ "POST /v2/demo/sessions/logout": () => new Response(null, { status: 403 }) }) });
    await assert.rejects(csrf.signOut(), (e: ApiError) => e.status === 403);
  });

  it("rejects a malformed DemoUser", () => {
    for (const body of [null, {}, { ...ALICE, user_id: "alice" }, { ...ALICE, display_name: " " }, { ...ALICE, expires_at: "soon" }]) {
      assert.throws(() => parseDemoUser(body), ApiError);
    }
  });

  it("explains each documented failure without echoing input", () => {
    const secret = "hunter2-password";
    for (const status of [0, 401, 403, 429, 500, 503]) {
      const msg = signInMessage(new ApiError(status, status === 0 ? "network" : "server", secret));
      assert.ok(msg.length > 0);
      assert.ok(!msg.includes(secret));
    }
    assert.match(signInMessage(new ApiError(401, "unauthorized", "x")), /don't match/);
    assert.match(signInMessage(new ApiError(429, "unavailable", "x")), /Too many/);
    assert.match(signInMessage(new ApiError(503, "unavailable", "x")), /turned off/);
  });
});

function fakeAuth(overrides: Partial<DemoAuthApi> = {}): DemoAuthApi {
  return {
    current: async () => null,
    signIn: async () => ALICE,
    signOut: async () => {},
    ...overrides,
  };
}

describe("session store", () => {
  it("starts checking, then reflects the gateway's answer", async () => {
    const store = createSessionStore(fakeAuth({ current: async () => ALICE }));
    assert.equal(store.get().status, "checking");
    await store.refresh();
    assert.deepEqual(store.get(), { status: "signed_in", user: ALICE });
    assert.equal(userIdOf(store.get()), ALICE.user_id);
  });

  it("signs in, switches account and signs out, notifying each change", async () => {
    let next = ALICE;
    const store = createSessionStore(fakeAuth({ signIn: async () => next }));
    await store.refresh();
    const seen: (string | null)[] = [];
    store.subscribe(() => seen.push(userIdOf(store.get())));
    await store.signIn("a", "pw");
    await store.signOut();
    next = BOB;
    await store.signIn("b", "pw");
    assert.deepEqual(seen, [ALICE.user_id, null, BOB.user_id]);
  });

  it("stays signed out after a failed sign-in and stays signed in after a failed sign-out", async () => {
    const store = createSessionStore(
      fakeAuth({
        signIn: async () => {
          throw new ApiError(401, "unauthorized", "bad");
        },
      }),
    );
    await store.refresh();
    await assert.rejects(store.signIn("a", "wrong"));
    assert.deepEqual(store.get(), { status: "signed_out", expired: false });

    const sticky = createSessionStore(
      fakeAuth({
        current: async () => ALICE,
        signOut: async () => {
          throw new ApiError(403, "forbidden", "csrf");
        },
      }),
    );
    await sticky.refresh();
    await assert.rejects(sticky.signOut());
    assert.equal(sticky.get().status, "signed_in", "the cookie may still be valid");
  });

  it("marks an expired session only when someone was signed in", async () => {
    const store = createSessionStore(fakeAuth({ current: async () => ALICE }));
    store.expire();
    assert.equal(store.get().status, "checking");
    await store.refresh();
    store.expire();
    assert.deepEqual(store.get(), { status: "signed_out", expired: true });
  });

  it("reports an unreachable or disabled gateway instead of guessing", async () => {
    const offline = createSessionStore(
      fakeAuth({
        current: async () => {
          throw new ApiError(0, "network", "x");
        },
      }),
    );
    await offline.refresh();
    assert.equal(offline.get().status, "unavailable");
    const disabled = createSessionStore(
      fakeAuth({
        current: async () => {
          throw new ApiError(503, "unavailable", "x");
        },
      }),
    );
    await disabled.refresh();
    const s = disabled.get();
    assert.ok(s.status === "unavailable" && /turned off/.test(s.message));
  });

  it("a slow session check can't undo a sign-in that finished after it started", async () => {
    let release: (u: DemoUser | null) => void = () => {};
    const store = createSessionStore(fakeAuth({ current: () => new Promise((r) => (release = r)), signIn: async () => BOB }));
    const check = store.refresh();
    await store.signIn("b", "pw");
    release(null);
    await check;
    assert.equal(userIdOf(store.get()), BOB.user_id);
  });
});

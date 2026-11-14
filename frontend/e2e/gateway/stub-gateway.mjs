// TEST DOUBLE, not the gateway. A tiny in-memory stand-in that follows
// contracts/openapi/gateway.phase2.extension.v1.yaml (demo sign-in, evaluations,
// notifications) and the goal list/detail shapes of gateway.v1.yaml, so the browser
// lane can exercise the real flows before R2-D1-01 / R2-D4-02 land. It proves the frontend's half only: the
// acceptance runs against D1's gateway. Two synthetic accounts, synthetic goals.
//
// Usage: node e2e/gateway/stub-gateway.mjs <port> <allowed-origin>
import { randomUUID } from "node:crypto";
import { createServer } from "node:http";

const port = Number(process.argv[2] ?? 8100);
const allowedOrigin = process.argv[3] ?? "http://127.0.0.1:3101";

const USERS = {
  "demo-a": { password: "demo-a-pass", user_id: "aaaaaaaa-0000-4000-8000-000000000001", display_name: "Demo user A" },
  "demo-b": { password: "demo-b-pass", user_id: "bbbbbbbb-0000-4000-8000-000000000002", display_name: "Demo user B" },
};

const A = USERS["demo-a"].user_id;
const B = USERS["demo-b"].user_id;
const KINDLE = "aaaaaaaa-1111-4111-8111-000000000001";
const GPU = "aaaaaaaa-1111-4111-8111-000000000003";
const CAFE = "bbbbbbbb-2222-4222-8222-000000000002";
const OBS = "cccccccc-3333-4333-8333-000000000001";
const APPROVED = "2026-11-13T09:00:00Z";

function productSpec(goal_id, owner, title, query, max) {
  return {
    goal_id, owner_user_id: owner, version: 1, title, goal_type: "product_purchase",
    criteria: {
      kind: "product_purchase",
      product: { query, match_policy: "exact_model", condition: "new" },
      quantity: 1, max_total_eur_cents: max, delivery_country: "FI", include_shipping_and_tax: true,
    },
    monitoring: { frequency_minutes: 360, max_evidence_age_minutes: 1440 },
    actions: { notify_user: true, agent_handoff: "prepare_only", auto_purchase: false },
    approved_at: APPROVED,
  };
}

const GOALS = {
  [KINDLE]: {
    owner: A,
    summary: { title: "Kindle Paperwhite under €180", version: 1, lifecycle: "active", opportunity_status: "unknown", last_evaluated_at: "2026-11-14T08:00:00Z" },
    spec: productSpec(KINDLE, A, "Kindle Paperwhite under €180", "Kindle Paperwhite", 18000),
    evaluations: [{
      evaluation_id: "dddddddd-4444-4444-8444-000000000001", goal_id: KINDLE, goal_version: 1,
      evaluated_at: "2026-11-14T08:00:00Z", status: "unknown",
      conditions: [
        { code: "identity_match", status: "satisfied", reason: "Model matches the requested product", observation_ids: [OBS] },
        { code: "shipping_known", status: "unknown", reason: "Shipping cost to FI not stated on the checked page", observation_ids: [] },
        { code: "total_within_budget", status: "unknown", reason: "Complete payable total cannot be calculated", observation_ids: [] },
      ],
      candidate_observation_ids: [], is_opportunity_verified: false,
    }],
  },
  [GPU]: {
    owner: A,
    summary: { title: "RTX 5070 under €600", version: 1, lifecycle: "active", opportunity_status: "verified", last_evaluated_at: "2026-11-14T09:00:00Z" },
    spec: productSpec(GPU, A, "RTX 5070 under €600", "GeForce RTX 5070", 60000),
    evaluations: [{
      evaluation_id: "dddddddd-4444-4444-8444-000000000002", goal_id: GPU, goal_version: 1,
      evaluated_at: "2026-11-14T09:00:00Z", status: "satisfied",
      conditions: [
        { code: "identity_match", status: "satisfied", reason: "Model matches the requested product", observation_ids: [OBS] },
        { code: "total_within_budget", status: "satisfied", reason: "EUR 589.80 incl. shipping and tax is within EUR 600.00", observation_ids: [OBS] },
      ],
      candidate_observation_ids: [OBS], is_opportunity_verified: true,
    }],
  },
  [CAFE]: {
    owner: B,
    summary: { title: "Café supplies every month", version: 1, lifecycle: "paused", opportunity_status: "not_found", last_evaluated_at: null },
    spec: {
      goal_id: CAFE, owner_user_id: B, version: 1, title: "Café supplies every month", goal_type: "recurring_procurement",
      criteria: {
        kind: "recurring_procurement",
        items: [{ name: "lactose-free milk", quantity: 20, unit: "L", requirements: [] }, { name: "coffee beans", quantity: 10, unit: "kg", requirements: [] }],
        cadence: "monthly", current_total_eur_cents: 25000, max_total_eur_cents: 21250,
        delivery_country: "FI", include_shipping_and_tax: true,
      },
      monitoring: { frequency_minutes: 1440, max_evidence_age_minutes: 2880 },
      actions: { notify_user: true, agent_handoff: "prepare_only", auto_purchase: false },
      approved_at: APPROVED,
    },
    evaluations: "unavailable", // exercises the "provider unavailable" state
  },
};

const NOTIFICATIONS = [{
  owner: A, is_read: false,
  data: {
    notification_id: "eeeeeeee-5555-4555-8555-000000000001", goal_id: GPU, goal_version: 1,
    title: "Verified offer: RTX 5070 under €600",
    summary: "An offer at shop.example met every condition when checked: EUR 589.80 delivered to Finland. Not ordered.",
    created_at: "2026-11-14T09:01:00Z",
  },
}];

const sessions = new Map(); // cookie value -> user

function cors(res) {
  res.setHeader("Access-Control-Allow-Origin", allowedOrigin);
  res.setHeader("Access-Control-Allow-Credentials", "true");
  res.setHeader("Access-Control-Allow-Headers", "Accept, Content-Type, X-Correlation-ID");
  res.setHeader("Access-Control-Allow-Methods", "GET, POST, PATCH");
  res.setHeader("Vary", "Origin");
}

function send(res, status, body) {
  res.statusCode = status;
  if (body === undefined) return res.end();
  res.setHeader("Content-Type", "application/json");
  res.end(JSON.stringify(body));
}

function userOf(req) {
  const match = /(?:^|;\s*)goalpulse_session=([^;]+)/.exec(req.headers.cookie ?? "");
  return match ? (sessions.get(match[1]) ?? null) : null;
}

const expiresAt = () => new Date(Date.now() + 15 * 60_000).toISOString();
const identity = (u) => ({ user_id: u.user_id, display_name: u.display_name, expires_at: expiresAt() });

async function readBody(req) {
  let raw = "";
  for await (const chunk of req) raw += chunk;
  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

createServer(async (req, res) => {
  cors(res);
  if (req.method === "OPTIONS") return send(res, 204);
  const { pathname } = new URL(req.url ?? "/", "http://stub");
  const mutating = req.method !== "GET";
  if (mutating && req.headers.origin !== allowedOrigin) return send(res, 403, { code: "forbidden", message: "origin not allowed" });

  if (req.method === "POST" && pathname === "/v2/demo/sessions") {
    const body = await readBody(req);
    const user = body && USERS[body.username];
    if (!user || user.password !== body.password) return send(res, 401, { code: "unauthorized", message: "invalid demo credentials" });
    const value = randomUUID();
    sessions.set(value, user);
    res.setHeader("Set-Cookie", `goalpulse_session=${value}; HttpOnly; SameSite=Lax; Path=/; Max-Age=900`);
    return send(res, 200, identity(user));
  }

  const user = userOf(req);
  if (req.method === "POST" && pathname === "/v2/demo/sessions/logout") {
    if (!user) return send(res, 401, { code: "unauthorized", message: "no session" });
    res.setHeader("Set-Cookie", "goalpulse_session=; HttpOnly; SameSite=Lax; Path=/; Max-Age=0");
    sessions.forEach((u, k) => u === user && sessions.delete(k));
    return send(res, 204);
  }
  if (!user) return send(res, 401, { code: "unauthorized", message: "valid demo session required" });

  if (req.method === "GET" && pathname === "/v2/demo/session") return send(res, 200, identity(user));
  if (req.method === "GET" && pathname === "/v1/goals") {
    const own = Object.entries(GOALS).filter(([, g]) => g.owner === user.user_id);
    return send(res, 200, own.map(([goal_id, g]) => ({ goal_id, ...g.summary })));
  }
  let m = /^\/v1\/goals\/([^/]+)(\/evaluations)?$/.exec(pathname);
  if (req.method === "GET" && m) {
    const goal = GOALS[decodeURIComponent(m[1])];
    if (!goal || goal.owner !== user.user_id) return send(res, 404, { code: "not_found", message: "not found" });
    if (!m[2]) return send(res, 200, goal.spec);
    if (goal.evaluations === "unavailable") return send(res, 503, { code: "unavailable", message: "goal evaluation unavailable" });
    return send(res, 200, goal.evaluations);
  }
  if (req.method === "GET" && pathname === "/v1/notifications") {
    const own = NOTIFICATIONS.filter((n) => n.owner === user.user_id);
    return send(res, 200, own.map((n) => ({ ...n.data, is_read: n.is_read })));
  }
  m = /^\/v1\/notifications\/([^/]+)\/read$/.exec(pathname);
  if (req.method === "PATCH" && m) {
    const n = NOTIFICATIONS.find((x) => x.data.notification_id === decodeURIComponent(m[1]) && x.owner === user.user_id);
    if (!n) return send(res, 404, { code: "not_found", message: "not found" });
    n.is_read = true;
    return send(res, 204);
  }
  // Missing and cross-owner goals are the same 404.
  return send(res, 404, { code: "not_found", message: "not found" });
}).listen(port, "127.0.0.1", () => console.log(`stub gateway on :${port} (TEST DOUBLE, origin ${allowedOrigin})`));

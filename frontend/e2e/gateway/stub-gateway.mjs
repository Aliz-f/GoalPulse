// TEST DOUBLE, not the gateway. A tiny in-memory stand-in that follows
// contracts/openapi/gateway.phase2.extension.v1.yaml (demo sign-in) and the GoalSummary
// list/detail shapes of gateway.v1.yaml, so the browser lane can exercise the real
// cookie flow before R2-D1-01 lands. It proves the frontend's half only: the
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

const GOALS = {
  "aaaaaaaa-1111-4111-8111-000000000001": {
    owner: USERS["demo-a"].user_id,
    summary: { title: "Kindle Paperwhite under €180", version: 1, lifecycle: "active", opportunity_status: "unknown", last_evaluated_at: null },
  },
  "bbbbbbbb-2222-4222-8222-000000000002": {
    owner: USERS["demo-b"].user_id,
    summary: { title: "Café supplies every month", version: 1, lifecycle: "paused", opportunity_status: "not_found", last_evaluated_at: null },
  },
};

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
  // Missing and cross-owner goals are the same 404.
  return send(res, 404, { code: "not_found", message: "not found" });
}).listen(port, "127.0.0.1", () => console.log(`stub gateway on :${port} (TEST DOUBLE, origin ${allowedOrigin})`));

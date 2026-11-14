import type { ConfirmRequest, GoalDraft, GoalSpec, GoalSummary } from "../contracts.ts";
import { ApiError, kindForStatus } from "./errors.ts";
import type { GoalPulseApi } from "./types.ts";

type FetchLike = (input: string, init: RequestInit) => Promise<Response>;

export interface GatewayOptions {
  /** Public gateway origin, e.g. http://localhost:8000. Never a private service URL. */
  baseUrl: string;
  /** Returns the end-user access token (JWT) or null when signed out. */
  getToken: () => string | null;
  fetchImpl?: FetchLike;
  newCorrelationId?: () => string;
}

export function normalizeBaseUrl(raw: string | undefined): string {
  const value = (raw ?? "").trim().replace(/\/+$/, "");
  if (!/^https?:\/\/[^/]+/.test(value)) {
    throw new Error("NEXT_PUBLIC_GATEWAY_URL must be an http(s) URL of the public gateway");
  }
  return value;
}

/** Client for the frozen public gateway contract (contracts/openapi/gateway.v1.yaml). */
export function createGatewayApi(opts: GatewayOptions): GoalPulseApi {
  const base = normalizeBaseUrl(opts.baseUrl);
  const doFetch: FetchLike = opts.fetchImpl ?? ((input, init) => fetch(input, init));
  const correlationId = opts.newCorrelationId ?? (() => crypto.randomUUID());

  async function call<T>(method: "GET" | "POST" | "PATCH", path: string, body?: unknown): Promise<T> {
    const token = opts.getToken();
    if (!token) throw new ApiError(401, "not_signed_in", "not signed in");
    const headers: Record<string, string> = {
      Accept: "application/json",
      Authorization: `Bearer ${token}`,
      "X-Correlation-ID": correlationId(),
    };
    if (body !== undefined) headers["Content-Type"] = "application/json";

    let res: Response;
    try {
      res = await doFetch(`${base}${path}`, {
        method,
        headers,
        body: body === undefined ? undefined : JSON.stringify(body),
        cache: "no-store",
        credentials: "omit",
      });
    } catch {
      throw new ApiError(0, "network", "network error");
    }

    if (res.status === 204) return undefined as T;
    if (res.ok) return (await res.json()) as T;

    let detail: { message?: unknown; current_revision?: unknown } = {};
    try {
      detail = await res.json();
    } catch {
      // error bodies are optional in the frozen contract
    }
    const current = typeof detail.current_revision === "number" ? detail.current_revision : null;
    const message = typeof detail.message === "string" ? detail.message : `HTTP ${res.status}`;
    throw new ApiError(res.status, kindForStatus(res.status), message, current);
  }

  const session = (id: string) => `/v1/goal-sessions/${encodeURIComponent(id)}`;
  const goal = (id: string) => `/v1/goals/${encodeURIComponent(id)}`;

  return {
    mode: "gateway",
    createSession: (initialMessage) =>
      call<GoalDraft>("POST", "/v1/goal-sessions", { initial_message: initialMessage }),
    sendMessage: (sessionId, message, expectedRevision) =>
      call<GoalDraft>("POST", `${session(sessionId)}/messages`, { message, expected_revision: expectedRevision }),
    getSession: (sessionId) => call<GoalDraft>("GET", session(sessionId)),
    confirm: (sessionId, expectedRevision) => {
      const body: ConfirmRequest = { expected_revision: expectedRevision, approved: true };
      return call<GoalSpec>("POST", `${session(sessionId)}/confirm`, body);
    },
    listGoals: (status) => call<GoalSummary[]>("GET", status ? `/v1/goals?status=${encodeURIComponent(status)}` : "/v1/goals"),
    getGoal: (goalId) => call<GoalSpec>("GET", goal(goalId)),
    updateLifecycle: (goalId, action) => call<void>("PATCH", `${goal(goalId)}/lifecycle`, { action }),
  };
}

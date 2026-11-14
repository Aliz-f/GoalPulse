import type { ConfirmRequest, GoalDraft, GoalSpec, GoalSummary } from "../contracts.ts";
import { createHttp, readJson, type HttpOptions } from "./http.ts";
import type { GoalEvaluation, Notification } from "../extension.ts";
import type { GoalPulseApi } from "./types.ts";

export { normalizeBaseUrl } from "../runtime.ts";

export type GatewayOptions = HttpOptions;

/** The contract allows limit 1..100. */
const clamp = (n: number) => Math.max(1, Math.min(100, Math.trunc(n) || 20));

/**
 * Client for the frozen public gateway contract (contracts/openapi/gateway.v1.yaml) and the
 * owner-scoped evaluation/notification reads of gateway.phase2.extension.v1.yaml.
 * Authenticated by the gateway's HttpOnly demo session cookie (see http.ts).
 */
export function createGatewayApi(opts: GatewayOptions): GoalPulseApi {
  const http = createHttp(opts);
  const json = async <T>(method: "GET" | "POST", path: string, body?: unknown) =>
    readJson<T>(await http.send(method, path, body));

  const session = (id: string) => `/v1/goal-sessions/${encodeURIComponent(id)}`;
  const goal = (id: string) => `/v1/goals/${encodeURIComponent(id)}`;

  return {
    mode: "gateway",
    createSession: (initialMessage) => json<GoalDraft>("POST", "/v1/goal-sessions", { initial_message: initialMessage }),
    sendMessage: (sessionId, message, expectedRevision) =>
      json<GoalDraft>("POST", `${session(sessionId)}/messages`, { message, expected_revision: expectedRevision }),
    getSession: (sessionId) => json<GoalDraft>("GET", session(sessionId)),
    confirm: (sessionId, expectedRevision) => {
      const body: ConfirmRequest = { expected_revision: expectedRevision, approved: true };
      return json<GoalSpec>("POST", `${session(sessionId)}/confirm`, body);
    },
    listGoals: (status) =>
      json<GoalSummary[]>("GET", status ? `/v1/goals?status=${encodeURIComponent(status)}` : "/v1/goals"),
    getGoal: (goalId) => json<GoalSpec>("GET", goal(goalId)),
    updateLifecycle: async (goalId, action) => {
      await http.send("PATCH", `${goal(goalId)}/lifecycle`, { action });
    },
    listEvaluations: (goalId, limit = 20) =>
      json<GoalEvaluation[]>("GET", `${goal(goalId)}/evaluations?limit=${clamp(limit)}`),
    listNotifications: (limit = 20) => json<Notification[]>("GET", `/v1/notifications?limit=${clamp(limit)}`),
    markNotificationRead: async (notificationId) => {
      await http.send("PATCH", `/v1/notifications/${encodeURIComponent(notificationId)}/read`);
    },
  };
}

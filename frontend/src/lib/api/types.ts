import type { GoalDraft, GoalSpec, GoalSummary, Lifecycle, LifecycleAction } from "../contracts.ts";
import type { GoalEvaluation, Notification } from "../extension.ts";

/**
 * The seven frozen public gateway operations (contracts/openapi/gateway.v1.yaml) plus the
 * owner-scoped reads of the accepted Phase 2 extension (gateway.phase2.extension.v1.yaml).
 */
export interface GoalPulseApi {
  readonly mode: "mock" | "gateway";
  createSession(initialMessage: string): Promise<GoalDraft>;
  sendMessage(sessionId: string, message: string, expectedRevision: number): Promise<GoalDraft>;
  getSession(sessionId: string): Promise<GoalDraft>;
  confirm(sessionId: string, expectedRevision: number): Promise<GoalSpec>;
  listGoals(status?: Lifecycle): Promise<GoalSummary[]>;
  getGoal(goalId: string): Promise<GoalSpec>;
  updateLifecycle(goalId: string, action: LifecycleAction): Promise<void>;
  /** Newest first. Missing and cross-owner goals are the same 404. */
  listEvaluations(goalId: string, limit?: number): Promise<GoalEvaluation[]>;
  /** Newest first, only the signed-in user's. */
  listNotifications(limit?: number): Promise<Notification[]>;
  /** Idempotent. Missing and cross-owner IDs are the same 404. */
  markNotificationRead(notificationId: string): Promise<void>;
}

/** Extra controls that only exist in mock mode (demo + manual testing). */
export interface MockControls {
  /** The scripted next user reply, shown as a suggestion. */
  suggestedReply(sessionId: string): string | null;
  /** Pretend another tab answered, so the next write from this tab gets a 409. */
  simulateConcurrentEdit(sessionId: string): void;
  /**
   * Mock-only link from a notification to its sample prepared purchase. The public
   * Notification has no package reference, so gateway mode never shows this link.
   */
  packageIdFor(notificationId: string): string | null;
}

export function hasMockControls(api: GoalPulseApi): api is GoalPulseApi & MockControls {
  return api.mode === "mock";
}

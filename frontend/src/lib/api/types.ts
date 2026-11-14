import type { GoalDraft, GoalSpec, GoalSummary, Lifecycle, LifecycleAction } from "../contracts.ts";

/** The seven frozen public gateway operations (contracts/openapi/gateway.v1.yaml). */
export interface GoalPulseApi {
  readonly mode: "mock" | "gateway";
  createSession(initialMessage: string): Promise<GoalDraft>;
  sendMessage(sessionId: string, message: string, expectedRevision: number): Promise<GoalDraft>;
  getSession(sessionId: string): Promise<GoalDraft>;
  confirm(sessionId: string, expectedRevision: number): Promise<GoalSpec>;
  listGoals(status?: Lifecycle): Promise<GoalSummary[]>;
  getGoal(goalId: string): Promise<GoalSpec>;
  updateLifecycle(goalId: string, action: LifecycleAction): Promise<void>;
}

/** Extra controls that only exist in mock mode (demo + manual testing). */
export interface MockControls {
  /** The scripted next user reply, shown as a suggestion. */
  suggestedReply(sessionId: string): string | null;
  /** Pretend another tab answered, so the next write from this tab gets a 409. */
  simulateConcurrentEdit(sessionId: string): void;
}

export function hasMockControls(api: GoalPulseApi): api is GoalPulseApi & MockControls {
  return api.mode === "mock";
}

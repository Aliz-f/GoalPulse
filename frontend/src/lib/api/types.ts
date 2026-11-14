import type { GoalDraft, GoalSpec } from "../contracts.ts";

/** The four frozen gateway operations the goal wizard needs. */
export interface GoalPulseApi {
  readonly mode: "mock" | "gateway";
  createSession(initialMessage: string): Promise<GoalDraft>;
  sendMessage(sessionId: string, message: string, expectedRevision: number): Promise<GoalDraft>;
  getSession(sessionId: string): Promise<GoalDraft>;
  confirm(sessionId: string, expectedRevision: number): Promise<GoalSpec>;
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

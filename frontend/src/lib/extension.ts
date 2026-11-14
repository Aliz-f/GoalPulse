// TypeScript mirrors of the accepted Phase 2 additive gateway contract
// (contracts/openapi/gateway.phase2.extension.v1.yaml) used by R2-D2-04:
//   GET   /v1/goals/{goal_id}/evaluations        -> GoalEvaluation v1 (contracts/schemas/goal-evaluation.v1.schema.json)
//   GET   /v1/notifications                      -> Notification[]
//   PATCH /v1/notifications/{notification_id}/read -> 204
// Do not add fields the contract doesn't define: both schemas are additionalProperties: false.

export type CheckStatus = "satisfied" | "unsatisfied" | "unknown";

export interface ConditionResult {
  code: string;
  status: CheckStatus;
  reason: string;
  observation_ids: string[];
}

export interface EvaluationProgress {
  metric: "savings_progress" | "budget_gap" | "not_applicable";
  value: number | null;
  unit: "percent" | "eur_cents" | "none";
}

export interface GoalEvaluation {
  evaluation_id: string;
  goal_id: string;
  goal_version: number;
  evaluated_at: string;
  status: CheckStatus;
  conditions: ConditionResult[];
  candidate_observation_ids: string[];
  is_opportunity_verified: boolean;
  progress?: EvaluationProgress;
}

export interface Notification {
  notification_id: string;
  goal_id: string;
  goal_version: number;
  title: string;
  summary: string;
  created_at: string;
  is_read: boolean;
}

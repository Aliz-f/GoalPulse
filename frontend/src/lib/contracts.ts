// Hand-written TypeScript mirrors of the FROZEN contracts:
//   contracts/schemas/goal-draft.v1.schema.json
//   contracts/schemas/goal-spec.v1.schema.json
//   contracts/openapi/gateway.v1.yaml (session + confirm operations)
// Do not add fields here that the contracts do not define.

export type DraftState = "collecting" | "ready_for_confirmation" | "confirmed" | "abandoned";

export interface GoalDraft {
  session_id: string;
  owner_user_id: string;
  revision: number;
  state: DraftState;
  /** Untrusted, possibly incomplete. Read it through summarizeCandidate(), never directly. */
  candidate_goal: Record<string, unknown>;
  missing_fields: string[];
  next_question: string | null;
  ready_for_confirmation: boolean;
}

export type Unit = "pcs" | "kg" | "L";

export interface ProductPurchaseCriteria {
  kind: "product_purchase";
  product: {
    query: string;
    match_policy: "exact_model" | "equivalent_allowed";
    condition: "new" | "any";
    required_attributes?: Record<string, string>;
  };
  quantity: number;
  max_total_eur_cents: number;
  delivery_country: string;
  include_shipping_and_tax: true;
  allowed_seller_domains?: string[];
  blocked_seller_domains?: string[];
}

export interface RecurringProcurementCriteria {
  kind: "recurring_procurement";
  items: { name: string; quantity: number; unit: Unit; requirements: string[] }[];
  cadence: "monthly";
  current_total_eur_cents: number;
  max_total_eur_cents: number;
  delivery_country: string;
  include_shipping_and_tax: true;
  max_distinct_sellers?: number;
}

export interface GoalSpec {
  goal_id: string;
  owner_user_id: string;
  version: number;
  title: string;
  goal_type: "product_purchase" | "recurring_procurement";
  criteria: ProductPurchaseCriteria | RecurringProcurementCriteria;
  monitoring: { frequency_minutes: number; max_evidence_age_minutes: number };
  actions: { notify_user: boolean; agent_handoff: "prepare_only"; auto_purchase: false };
  approved_at: string;
}

/** Body of POST /v1/goal-sessions/{id}/confirm. `approved` is the constant true. */
export interface ConfirmRequest {
  expected_revision: number;
  approved: true;
}

// ---- Goal Management (frozen gateway.v1.yaml: /v1/goals*)

export type Lifecycle = "active" | "paused" | "completed" | "cancelled";
export type OpportunityStatus = "not_found" | "candidate" | "verified" | "unknown";
export type LifecycleAction = "pause" | "resume" | "cancel" | "complete";

/** components.schemas.GoalSummary — the only per-goal status the frozen API exposes. */
export interface GoalSummary {
  goal_id: string;
  title: string;
  version: number;
  lifecycle: Lifecycle;
  opportunity_status: OpportunityStatus;
  last_evaluated_at?: string | null;
}

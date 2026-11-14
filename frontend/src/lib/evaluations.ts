// How a goal's evaluation history is shown (R2-D2-04). Pure, so it is unit-tested.
//
// Honesty rules:
// - "Verified" only when the record says so AND is internally consistent (status
//   satisfied, every condition satisfied, at least one candidate observation).
//   Anything else is never shown as verified.
// - Only evaluations for the goal's CURRENT version count; results for an older
//   version of the goal are not the answer for the goal as it stands now.
// - Unknown is "not enough evidence", never success, never failure.
// - No percentage unless Goal Evaluation reported one (it only does for a verified basket).

import type { CheckStatus, ConditionResult, GoalEvaluation } from "./extension.ts";
import { formatEurCents, newestTimestampFirst } from "./format.ts";
import type { Tone } from "./goals.ts";

export interface Headline {
  label: string;
  detail: string;
  tone: Tone;
  verified: boolean;
}

export interface ConditionView {
  code: string;
  label: string;
  status: CheckStatus;
  statusLabel: string;
  reason: string;
  evidenceCount: number;
}

export type EvaluationSummary =
  | { kind: "none"; olderVersionCount: number }
  | {
      kind: "result";
      latest: GoalEvaluation;
      headline: Headline;
      conditions: ConditionView[];
      progress: string | null;
      /** Earlier checks of this version, newest first. */
      history: GoalEvaluation[];
      olderVersionCount: number;
    };

const CONDITION_LABEL: Record<string, string> = {
  identity_match: "Right product",
  condition: "Condition (new)",
  availability: "In stock",
  delivery: "Delivers to your country",
  seller_policy: "Seller allowed",
  seller_limit: "Number of sellers",
  freshness: "Evidence is recent",
  reliability: "Evidence is reliable",
  tax_known: "Tax known",
  shipping_known: "Shipping cost known",
  shipping_known_by_seller: "Shipping cost known for each seller",
  units_compatible: "Quantities and units match",
  total_within_budget: "Total within budget",
  full_basket_coverage: "Every item found",
  quality: "Quality requirements",
};

const STATUS_LABEL: Record<CheckStatus, string> = {
  satisfied: "Met",
  unsatisfied: "Not met",
  unknown: "Unknown",
};

const STATUS_ORDER: Record<CheckStatus, number> = { unsatisfied: 0, unknown: 1, satisfied: 2 };

export function conditionLabel(code: string): string {
  return CONDITION_LABEL[code] ?? code.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
}

export function isConsistentlyVerified(e: GoalEvaluation): boolean {
  return (
    e.is_opportunity_verified === true &&
    e.status === "satisfied" &&
    e.conditions.length > 0 &&
    e.conditions.every((c) => c.status === "satisfied") &&
    e.candidate_observation_ids.length > 0
  );
}

export function headlineFor(e: GoalEvaluation): Headline {
  if (isConsistentlyVerified(e)) {
    return {
      label: "Verified offer found",
      detail: "An offer met every condition when it was checked. Nothing has been bought — review it and buy it yourself.",
      tone: "positive",
      verified: true,
    };
  }
  switch (e.status) {
    case "satisfied":
      return {
        label: "Possible offer",
        detail: "The checked conditions passed, but the offer isn't verified yet.",
        tone: "caution",
        verified: false,
      };
    case "unsatisfied":
      return {
        label: "No matching offer",
        detail: "At least one of your conditions wasn't met by the offers checked.",
        tone: "muted",
        verified: false,
      };
    default:
      return {
        label: "Not enough evidence",
        detail: "Some facts couldn't be confirmed from the sources checked, so this is not a match. Unknown is not a failure and not a success.",
        tone: "neutral",
        verified: false,
      };
  }
}

function conditionView(c: ConditionResult): ConditionView {
  return {
    code: c.code,
    label: conditionLabel(c.code),
    status: c.status,
    statusLabel: STATUS_LABEL[c.status] ?? "Unknown",
    reason: c.reason,
    evidenceCount: c.observation_ids.length,
  };
}

export function progressText(e: GoalEvaluation): string | null {
  const p = e.progress;
  if (!p || p.metric === "not_applicable" || p.value === null || !Number.isFinite(p.value)) return null;
  if (p.metric === "savings_progress" && p.unit === "percent" && isConsistentlyVerified(e)) {
    return `This verified offer reaches ${p.value.toLocaleString(undefined, { maximumFractionDigits: 2 })}% of the saving you asked for.`;
  }
  if (p.metric === "budget_gap" && p.unit === "eur_cents" && Number.isInteger(p.value)) {
    return `Budget gap reported by the last check: ${formatEurCents(p.value)}.`;
  }
  return null;
}

const newestFirst = (a: GoalEvaluation, b: GoalEvaluation) => newestTimestampFirst(a.evaluated_at, b.evaluated_at);

export function summarizeEvaluations(evaluations: GoalEvaluation[], goalId: string, goalVersion: number): EvaluationSummary {
  const mine = evaluations.filter((e) => e.goal_id === goalId);
  const current = mine.filter((e) => e.goal_version === goalVersion).sort(newestFirst);
  const olderVersionCount = mine.length - current.length;
  if (current.length === 0) return { kind: "none", olderVersionCount };
  const [latest, ...history] = current;
  return {
    kind: "result",
    latest,
    headline: headlineFor(latest),
    conditions: latest.conditions.map(conditionView).sort((a, b) => STATUS_ORDER[a.status] - STATUS_ORDER[b.status]),
    progress: progressText(latest),
    history,
    olderVersionCount,
  };
}

// Presentation rules for goal status. Only the frozen GoalSummary fields are used:
// no progress percentage, no invented condition breakdown (that needs ICP-02).

import type { Lifecycle, LifecycleAction, OpportunityStatus } from "./contracts.ts";

export type Tone = "positive" | "neutral" | "caution" | "muted";

export interface StatusView {
  label: string;
  detail: string;
  tone: Tone;
}

// `verified` = evidence supported an offer at observation time. It is NOT a purchase,
// and `unknown` is NOT success: both are spelled out so nobody reads them that way.
const OPPORTUNITY: Record<OpportunityStatus, StatusView> = {
  verified: {
    label: "Verified offer found",
    detail: "An offer met every condition when it was checked. Nothing has been bought — review it and buy it yourself.",
    tone: "positive",
  },
  candidate: {
    label: "Possible offer",
    detail: "Something looks close, but not every condition is confirmed yet.",
    tone: "caution",
  },
  unknown: {
    label: "Not enough evidence",
    detail: "Some facts (like shipping or stock) couldn't be confirmed, so this is not a match yet.",
    tone: "neutral",
  },
  not_found: {
    label: "No matching offer yet",
    detail: "Nothing checked so far meets your conditions.",
    tone: "muted",
  },
};

const LIFECYCLE: Record<Lifecycle, string> = {
  active: "Watching",
  paused: "Paused",
  completed: "Done",
  cancelled: "Cancelled",
};

export function opportunityView(status: OpportunityStatus, lastEvaluatedAt?: string | null): StatusView {
  if (!lastEvaluatedAt && status !== "verified") {
    return { label: "Waiting for first check", detail: "GoalPulse hasn't evaluated any offers for this goal yet.", tone: "muted" };
  }
  return OPPORTUNITY[status] ?? { label: "Unknown status", detail: "", tone: "muted" };
}

export function lifecycleLabel(lifecycle: Lifecycle): string {
  return LIFECYCLE[lifecycle] ?? lifecycle;
}

/** Mirrors Goal Management's transition matrix; the server still decides (409 otherwise). */
export function allowedActions(lifecycle: Lifecycle): LifecycleAction[] {
  switch (lifecycle) {
    case "active":
      return ["pause", "complete", "cancel"];
    case "paused":
      return ["resume", "complete", "cancel"];
    default:
      return [];
  }
}

export const ACTION_LABEL: Record<LifecycleAction, string> = {
  pause: "Pause",
  resume: "Resume",
  complete: "Mark as done",
  cancel: "Cancel goal",
};

/** Actions that end the goal for good get an explicit second click. */
export const NEEDS_CONFIRMATION: ReadonlySet<LifecycleAction> = new Set(["complete", "cancel"]);

export function lifecycleAfter(lifecycle: Lifecycle, action: LifecycleAction): Lifecycle | null {
  if (!allowedActions(lifecycle).includes(action)) return null;
  return action === "pause" ? "paused" : action === "resume" ? "active" : action === "cancel" ? "cancelled" : "completed";
}

/** "Checked 5 minutes ago" style text; null timestamp means not evaluated yet. */
export function freshness(lastEvaluatedAt: string | null | undefined, now: Date = new Date()): string {
  if (!lastEvaluatedAt) return "Not checked yet";
  const t = new Date(lastEvaluatedAt).getTime();
  if (Number.isNaN(t)) return "Last check time unavailable";
  const minutes = Math.max(0, Math.round((now.getTime() - t) / 60000));
  if (minutes < 1) return "Checked just now";
  if (minutes < 60) return `Checked ${minutes} minute${minutes === 1 ? "" : "s"} ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 48) return `Checked ${hours} hour${hours === 1 ? "" : "s"} ago`;
  const days = Math.round(hours / 24);
  return `Checked ${days} days ago`;
}

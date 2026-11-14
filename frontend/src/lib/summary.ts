// Turns the untrusted, possibly partial `candidate_goal` into display rows.
// Nothing here guesses: a value that is absent is shown as "Not set yet".

import { formatDuration, formatEurCents } from "./format.ts";

export type RowSource = "you" | "proposed" | "fixed";

export interface SummaryRow {
  label: string;
  value: string;
  source: RowSource;
  missing?: boolean;
}

export interface SummarySection {
  title: string;
  rows: SummaryRow[];
}

export interface CandidateSummary {
  title: string | null;
  goalType: "product_purchase" | "recurring_procurement" | null;
  sections: SummarySection[];
  items: { name: string; amount: string; requirements: string }[];
}

type Obj = Record<string, unknown>;
const obj = (v: unknown): Obj => (v && typeof v === "object" && !Array.isArray(v) ? (v as Obj) : {});
const str = (v: unknown): string | null => (typeof v === "string" && v.trim() ? v : null);
const int = (v: unknown): number | null => (typeof v === "number" && Number.isInteger(v) ? v : null);
const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);

const NOT_SET = "Not set yet";

function row(label: string, value: string | null, source: RowSource): SummaryRow {
  return value === null ? { label, value: NOT_SET, source, missing: true } : { label, value, source };
}

const UNIT_LABEL: Record<string, string> = { pcs: "pcs", kg: "kg", L: "L" };

export function summarizeCandidate(candidate: Record<string, unknown>): CandidateSummary {
  const c = obj(candidate);
  const goalType =
    c.goal_type === "product_purchase" || c.goal_type === "recurring_procurement" ? c.goal_type : null;
  const criteria = obj(c.criteria);
  const monitoring = obj(c.monitoring);
  const actions = obj(c.actions);
  const sections: SummarySection[] = [];
  const items: CandidateSummary["items"] = [];

  const money = (v: unknown) => {
    const cents = int(v);
    return cents === null ? null : formatEurCents(cents);
  };
  const delivery =
    criteria.include_shipping_and_tax === true && str(criteria.delivery_country)
      ? `Delivered to ${criteria.delivery_country === "FI" ? "Finland" : String(criteria.delivery_country)}, shipping and tax included`
      : null;

  if (goalType === "product_purchase") {
    const product = obj(criteria.product);
    const condition = product.condition === "new" ? "New only" : product.condition === "any" ? "New or used" : null;
    const match =
      product.match_policy === "exact_model"
        ? "Exactly this model"
        : product.match_policy === "equivalent_allowed"
          ? "Equivalent models OK"
          : null;
    const qty = int(criteria.quantity);
    sections.push({
      title: "What you want",
      rows: [
        row("Product", str(product.query), "you"),
        row("Condition", condition, "you"),
        row("Match", match, "you"),
        row("Quantity", qty === null ? null : String(qty), "you"),
        row("Budget (total)", money(criteria.max_total_eur_cents), "you"),
        row("Delivery", delivery, "you"),
      ],
    });
  } else if (goalType === "recurring_procurement") {
    for (const raw of Array.isArray(criteria.items) ? criteria.items : []) {
      const it = obj(raw);
      const q = num(it.quantity);
      const unit = str(it.unit);
      const reqs = Array.isArray(it.requirements) ? it.requirements.filter((r): r is string => typeof r === "string") : null;
      items.push({
        name: str(it.name) ?? "Unnamed item",
        amount: q !== null && unit ? `${q} ${UNIT_LABEL[unit] ?? unit}` : NOT_SET,
        requirements: reqs === null ? NOT_SET : reqs.length ? reqs.join(", ") : "None",
      });
    }
    const sellers = int(criteria.max_distinct_sellers);
    sections.push({
      title: "Every month",
      rows: [
        row("You spend now", money(criteria.current_total_eur_cents), "you"),
        row("Target (max per month)", money(criteria.max_total_eur_cents), "you"),
        row("Delivery", delivery, "you"),
        row("Max sellers", sellers === null ? null : String(sellers), "proposed"),
      ],
    });
  }

  if (goalType) {
    const freq = int(monitoring.frequency_minutes);
    const age = int(monitoring.max_evidence_age_minutes);
    sections.push({
      title: "How GoalPulse watches",
      rows: [
        row("Check offers", freq === null ? null : `Every ${formatDuration(freq, true)}`, "proposed"),
        row("Trust evidence up to", age === null ? null : `${formatDuration(age)} old`, "proposed"),
        row("Notify you", actions.notify_user === false ? "No" : "In the app", "proposed"),
        // These two are fixed by the contract and shown so nobody assumes otherwise.
        row("Buying", "Never automatic — GoalPulse prepares the details, you buy", "fixed"),
      ],
    });
  }

  return { title: str(c.title), goalType, sections, items };
}

const FIELD_LABELS: Record<string, string> = {
  supported_goal_type: "a goal type GoalPulse supports",
  product_query: "product",
  max_total_eur_cents: "budget",
  include_shipping_and_tax: "shipping & tax",
  delivery_country: "delivery country",
  product_condition: "condition",
  match_policy: "exact or equivalent",
  quantity: "quantity",
  items: "items",
  "items.quantity_unit": "item amounts",
  "items.requirements": "item requirements",
  current_total_eur_cents: "current spend",
  summary_approval: "your OK on the summary",
};

export function labelForField(field: string): string {
  return FIELD_LABELS[field] ?? field.replace(/[._]/g, " ");
}

export const isUnsupported = (missing: string[]) => missing.includes("supported_goal_type");

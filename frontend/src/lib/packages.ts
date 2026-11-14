// Helpers for showing a prepare-only action package. Wording never implies an
// order was placed, and missing cost components stay "unknown" rather than 0.

import { formatEurCents } from "./format.ts";
import type { ActionPackage, SellerOffer } from "./proposed.ts";

/** Only plain http(s) links from external evidence become clickable. */
export function safeHref(url: string): string | null {
  try {
    const u = new URL(url);
    return u.protocol === "https:" || u.protocol === "http:" ? u.toString() : null;
  } catch {
    return null;
  }
}

export function isExpired(pkg: Pick<ActionPackage, "expires_at">, now: Date = new Date()): boolean {
  const t = new Date(pkg.expires_at).getTime();
  return Number.isNaN(t) || t <= now.getTime();
}

export function money(cents: number | undefined, locale?: string): string {
  return typeof cents === "number" && Number.isInteger(cents) ? formatEurCents(cents, locale) : "Unknown";
}

export interface OfferRow {
  item: string;
  amount: string;
  seller: string;
  href: string | null;
  sourceText: string;
  itemTotal: string;
  shipping: string;
  tax: string;
}

export function offerRows(offers: SellerOffer[], locale?: string): OfferRow[] {
  return offers.map((o) => ({
    item: o.item_name,
    amount: `${o.quantity} ${o.unit}`,
    seller: o.seller_domain,
    href: safeHref(o.source_url),
    sourceText: o.source_url,
    itemTotal: money(o.total_item_eur_cents, locale),
    shipping: o.shipping_eur_cents === undefined ? "Not stated" : money(o.shipping_eur_cents, locale),
    tax: o.additional_tax_eur_cents === undefined ? "Not stated" : money(o.additional_tax_eur_cents, locale),
  }));
}

export const NOT_ORDERED =
  "Nothing has been ordered. GoalPulse never buys, pays or checks out; you decide whether to buy.";
export const RECHECK =
  "Prices and stock were verified when checked and can change. Re-check everything on the seller's site before you buy.";

/** Plain-text copy for the clipboard. Contains no account or payment data. */
export function packageText(pkg: ActionPackage, goalTitle?: string, locale?: string): string {
  const lines = [
    `GoalPulse prepared purchase${goalTitle ? `: ${goalTitle}` : ""}`,
    NOT_ORDERED,
    RECHECK,
    `Prepared: ${pkg.created_at} · valid until: ${pkg.expires_at} (UTC)`,
    "",
  ];
  for (const r of offerRows(pkg.seller_offers, locale)) {
    lines.push(
      `- ${r.amount} ${r.item} from ${r.seller}: ${r.itemTotal} (shipping: ${r.shipping}, extra tax: ${r.tax})`,
      `  ${r.sourceText}`,
    );
  }
  if (pkg.total_eur_cents !== undefined) lines.push("", `Total: ${money(pkg.total_eur_cents, locale)}`);
  lines.push("", `Package ${pkg.package_id} · goal ${pkg.goal_id} v${pkg.goal_version}`);
  return lines.join("\n");
}

"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";

import { Icp02Pending } from "@/components/Icp02Pending";
import { ApiError, adviceFor } from "@/lib/api/errors";
import { getProposedApi } from "@/lib/api/proposed";
import { formatTimestamp } from "@/lib/format";
import { NOT_ORDERED, RECHECK, isExpired, money, offerRows, packageText } from "@/lib/packages";
import type { ActionPackage } from "@/lib/proposed";

type Load =
  | { status: "loading" }
  | { status: "expired" }
  | { status: "error"; message: string }
  | { status: "ok"; pkg: ActionPackage };

async function copy(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}

export function PackageView() {
  const { packageId } = useParams<{ packageId: string }>();
  const api = getProposedApi();
  const [state, setState] = useState<Load>({ status: "loading" });
  const [copied, setCopied] = useState<string | null>(null);

  useEffect(() => {
    if (!api) return;
    let cancelled = false;
    api.getActionPackage(packageId).then(
      (pkg) => !cancelled && setState({ status: "ok", pkg }),
      (err) => {
        if (cancelled) return;
        if (err instanceof ApiError && err.kind === "gone") setState({ status: "expired" });
        else setState({ status: "error", message: adviceFor(err, "load").message });
      },
    );
    return () => {
      cancelled = true;
    };
  }, [api, packageId]);

  if (!api) return <Icp02Pending what="Prepared purchases" />;
  if (state.status === "loading") return <p className="hint">Loading prepared purchase…</p>;
  if (state.status === "expired" || (state.status === "ok" && isExpired(state.pkg))) {
    return (
      <div className="card">
        <h1>This prepared purchase expired</h1>
        <p>{adviceFor(new ApiError(410, "gone", "expired"), "load").message}</p>
        <Link href="/notifications">Back to notifications</Link>
      </div>
    );
  }
  if (state.status === "error") {
    return (
      <div className="card">
        <p role="alert" className="notice notice-error">
          {state.message}
        </p>
        <Link href="/notifications">Back to notifications</Link>
      </div>
    );
  }

  const { pkg } = state;
  const rows = offerRows(pkg.seller_offers);

  async function doCopy(kind: "text" | "json") {
    const ok = await copy(kind === "text" ? packageText(pkg) : JSON.stringify(pkg, null, 2));
    setCopied(ok ? (kind === "text" ? "Details copied." : "JSON copied.") : "Couldn't copy; select the text instead.");
  }

  return (
    <article className="package">
      <p>
        <Link href="/notifications">← Notifications</Link>
      </p>
      <h1>Prepared purchase</h1>
      <p className="notice notice-info">
        <strong>Not ordered.</strong> {NOT_ORDERED.replace("Nothing has been ordered. ", "")}
      </p>
      <p className="notice notice-warning">{RECHECK}</p>
      <p className="hint">
        Prepared <time dateTime={pkg.created_at} title={`${pkg.created_at} (UTC)`}>{formatTimestamp(pkg.created_at)}</time> ·
        valid until <time dateTime={pkg.expires_at} title={`${pkg.expires_at} (UTC)`}>{formatTimestamp(pkg.expires_at)}</time>
      </p>

      <div className="card">
        <table className="items">
          <caption>Offers</caption>
          <thead>
            <tr>
              <th scope="col">Item</th>
              <th scope="col">Seller</th>
              <th scope="col">Item total</th>
              <th scope="col">Shipping</th>
              <th scope="col">Extra tax</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={`${r.seller}-${r.item}`}>
                <td>
                  {r.amount} {r.item}
                </td>
                <td>
                  {r.href ? (
                    <a href={r.href} target="_blank" rel="noopener noreferrer nofollow">
                      {r.seller} ↗
                    </a>
                  ) : (
                    <span title={r.sourceText}>{r.seller} (link unavailable)</span>
                  )}
                </td>
                <td>{r.itemTotal}</td>
                <td>{r.shipping}</td>
                <td>{r.tax}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {pkg.total_eur_cents !== undefined && (
          <p className="total">
            Total when checked: <strong>{money(pkg.total_eur_cents)}</strong>
          </p>
        )}
      </div>

      <div className="actions actions-start">
        <button type="button" onClick={() => void doCopy("text")}>
          Copy details
        </button>
        <button type="button" onClick={() => void doCopy("json")}>
          Copy as JSON
        </button>
        <Link href={`/goals/${pkg.goal_id}`}>View goal</Link>
      </div>
      {copied && (
        <p className="hint" role="status">
          {copied}
        </p>
      )}
      <p className="hint">
        Package {pkg.package_id} · goal version {pkg.goal_version} · read-only
      </p>
    </article>
  );
}

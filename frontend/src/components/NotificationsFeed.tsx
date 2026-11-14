"use client";

import { BadgeCheck, Package } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";

import { Icon, Notice } from "@/components/Icons";
import { getApi } from "@/lib/api";
import { ApiError, adviceFor } from "@/lib/api/errors";
import { hasMockControls } from "@/lib/api/types";
import type { Notification } from "@/lib/extension";
import { formatTimestamp } from "@/lib/format";

type Load = { status: "loading" } | { status: "error"; message: string } | { status: "ok"; items: Notification[] };

/**
 * Owner-only in-app notifications (GET /v1/notifications, R2-D2-04). GoalPulse only
 * notifies about offers Goal Evaluation verified, so every item is a verified offer;
 * candidates and unknowns never appear here (they're on the goal page).
 */
export function NotificationsFeed() {
  const api = getApi();
  const [state, setState] = useState<Load>({ status: "loading" });
  const [attempt, setAttempt] = useState(0);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    api.listNotifications(50).then(
      (items) => !cancelled && setState({ status: "ok", items }),
      (err) => !cancelled && setState({ status: "error", message: adviceFor(err, "load").message }),
    );
    return () => {
      cancelled = true;
    };
  }, [api, attempt]);

  async function markRead(id: string) {
    setError(null);
    try {
      await api.markNotificationRead(id);
      setState((s) =>
        s.status === "ok" ? { ...s, items: s.items.map((n) => (n.notification_id === id ? { ...n, is_read: true } : n)) } : s,
      );
    } catch (err) {
      const gone = err instanceof ApiError && err.kind === "not_found";
      setError(gone ? "That notification doesn't exist or isn't yours. We reloaded your notifications." : adviceFor(err, "load").message);
      if (gone) setAttempt((n) => n + 1);
    }
  }

  if (state.status === "loading") return <p className="hint">Loading notifications…</p>;
  if (state.status === "error") {
    return (
      <Notice tone="error">
        {state.message}{" "}
        <button
          type="button"
          className="link-button"
          onClick={() => {
            setState({ status: "loading" });
            setAttempt((n) => n + 1);
          }}
        >
          Try again
        </button>
      </Notice>
    );
  }
  if (state.items.length === 0) {
    return (
      <div className="card empty">
        <p>No notifications yet. You&apos;ll get one when an offer meets every condition of a goal.</p>
        <Link href="/goals">See my goals</Link>
      </div>
    );
  }

  const unread = state.items.filter((n) => !n.is_read).length;

  return (
    <>
      <p className="hint" aria-live="polite">
        {unread === 0 ? "All read." : `${unread} unread.`}
      </p>
      {error && <Notice tone="error">{error}</Notice>}
      <ul className="feed">
        {state.items.map((n) => {
          const packageId = hasMockControls(api) ? api.packageIdFor(n.notification_id) : null;
          return (
            <li key={n.notification_id} className={`card notification${n.is_read ? "" : " unread"}`}>
              <div className="goal-card-head">
                <h2>{n.title}</h2>
                {!n.is_read && <span className="badge badge-unread">New</span>}
              </div>
              <p className="with-icon">
                <Icon icon={BadgeCheck} />
                Verified offer · not ordered
              </p>
              <p>{n.summary}</p>
              <p className="hint">
                <time dateTime={n.created_at} title={`${n.created_at} (UTC)`}>
                  {formatTimestamp(n.created_at)}
                </time>{" "}
                · goal version {n.goal_version} · Offers can change before checkout; re-check on the seller&apos;s site.
              </p>
              <div className="actions actions-start">
                {/* Mock only: the public Notification has no package link (see README). */}
                {packageId && (
                  <Link
                    className="with-icon"
                    href={`/packages/${packageId}`}
                    onClick={() => !n.is_read && void markRead(n.notification_id)}
                  >
                    <Icon icon={Package} />
                    View prepared purchase
                  </Link>
                )}
                <Link href={`/goals/${n.goal_id}`}>View goal and evidence</Link>
                {!n.is_read && (
                  <button type="button" className="link-button" onClick={() => void markRead(n.notification_id)}>
                    Mark as read
                  </button>
                )}
              </div>
            </li>
          );
        })}
      </ul>
    </>
  );
}

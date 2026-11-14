"use client";

import { Package } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";

import { Icon, Notice } from "@/components/Icons";
import { Icp02Pending } from "@/components/Icp02Pending";
import { adviceFor } from "@/lib/api/errors";
import { getProposedApi } from "@/lib/api/proposed";
import { formatTimestamp } from "@/lib/format";
import type { Notification } from "@/lib/proposed";

type Load = { status: "loading" } | { status: "error"; message: string } | { status: "ok"; items: Notification[] };

export function NotificationsFeed() {
  const api = getProposedApi();
  const [state, setState] = useState<Load>({ status: "loading" });
  const [attempt, setAttempt] = useState(0);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!api) return;
    let cancelled = false;
    api.listNotifications(50).then(
      (items) => !cancelled && setState({ status: "ok", items }),
      (err) => !cancelled && setState({ status: "error", message: adviceFor(err, "load").message }),
    );
    return () => {
      cancelled = true;
    };
  }, [api, attempt]);

  if (!api) return <Icp02Pending what="Notifications" />;

  async function markRead(id: string) {
    setError(null);
    try {
      await api!.markNotificationRead(id);
      setState((s) =>
        s.status === "ok" ? { ...s, items: s.items.map((n) => (n.notification_id === id ? { ...n, is_read: true } : n)) } : s,
      );
    } catch (err) {
      setError(adviceFor(err, "load").message);
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

  return (
    <>
      {error && (
        <Notice tone="error">
          {error}
        </Notice>
      )}
      <ul className="feed">
        {state.items.map((n) => (
          <li key={n.notification_id} className={`card notification${n.is_read ? "" : " unread"}`}>
            <div className="goal-card-head">
              <h2>{n.title}</h2>
              {!n.is_read && <span className="badge badge-unread">New</span>}
            </div>
            <p>{n.summary}</p>
            <p className="hint">
              <time dateTime={n.created_at} title={`${n.created_at} (UTC)`}>
                {formatTimestamp(n.created_at)}
              </time>{" "}
              · goal version {n.goal_version} · Offers can change before checkout.
            </p>
            <div className="actions actions-start">
              {n.action_package_id && (
                <Link
                  className="with-icon"
                  href={`/packages/${n.action_package_id}`}
                  onClick={() => !n.is_read && void markRead(n.notification_id)}
                >
                  <Icon icon={Package} />
                  View prepared purchase
                </Link>
              )}
              <Link href={`/goals/${n.goal_id}`}>View goal</Link>
              {!n.is_read && (
                <button type="button" className="link-button" onClick={() => void markRead(n.notification_id)}>
                  Mark as read
                </button>
              )}
            </div>
          </li>
        ))}
      </ul>
    </>
  );
}

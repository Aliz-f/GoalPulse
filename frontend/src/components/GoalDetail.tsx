"use client";

import { ArrowLeft, Ban, Check, Pause, Play, type LucideIcon } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";

import { Icon, Notice } from "@/components/Icons";
import { DraftSummary } from "@/components/DraftSummary";
import { EvaluationPanel } from "@/components/EvaluationPanel";
import { LifecycleBadge, OpportunityStatus } from "@/components/GoalStatus";
import { getApi } from "@/lib/api";
import { adviceFor } from "@/lib/api/errors";
import type { GoalSpec, GoalSummary, LifecycleAction } from "@/lib/contracts";
import { formatTimestamp } from "@/lib/format";
import { ACTION_LABEL, NEEDS_CONFIRMATION, allowedActions } from "@/lib/goals";
import { summarizeCandidate } from "@/lib/summary";

const ACTION_ICON: Record<LifecycleAction, LucideIcon> = { pause: Pause, resume: Play, complete: Check, cancel: Ban };

type Load =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ok"; spec: GoalSpec; summary: GoalSummary | null };

export function GoalDetail() {
  const { goalId } = useParams<{ goalId: string }>();
  const api = getApi();
  const [state, setState] = useState<Load>({ status: "loading" });
  const [pending, setPending] = useState<LifecycleAction | null>(null);
  const [armed, setArmed] = useState<LifecycleAction | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let cancelled = false;
    // The frozen API has no single-goal status read: GoalSpec comes from GET /v1/goals/{id},
    // lifecycle + opportunity status from the GoalSummary list.
    Promise.all([api.getGoal(goalId), api.listGoals()]).then(
      ([spec, list]) =>
        !cancelled && setState({ status: "ok", spec, summary: list.find((g) => g.goal_id === goalId) ?? null }),
      (err) => !cancelled && setState({ status: "error", message: adviceFor(err, "load").message }),
    );
    return () => {
      cancelled = true;
    };
  }, [api, goalId, attempt]);

  async function run(action: LifecycleAction) {
    if (NEEDS_CONFIRMATION.has(action) && armed !== action) {
      setArmed(action);
      return;
    }
    setArmed(null);
    setPending(action);
    setNotice(null);
    try {
      await api.updateLifecycle(goalId, action);
    } catch (err) {
      const advice = adviceFor(err, "lifecycle");
      setNotice(advice.message);
    } finally {
      setPending(null);
      setAttempt((n) => n + 1); // always re-read: the server is the source of truth
    }
  }

  if (state.status === "loading") return <p className="hint">Loading goal…</p>;
  if (state.status === "error") {
    return (
      <div className="card">
        <Notice tone="error">
          {state.message}
        </Notice>
        <Link href="/goals">Back to my goals</Link>
      </div>
    );
  }

  const { spec, summary } = state;
  const actions = summary ? allowedActions(summary.lifecycle) : [];

  return (
    <div className="session">
      <section aria-label="Status">
        <p>
          <Link href="/goals" className="with-icon">
            <Icon icon={ArrowLeft} />
            My goals
          </Link>
        </p>
        <div className="goal-card-head">
          <h1>{spec.title}</h1>
          {summary && <LifecycleBadge lifecycle={summary.lifecycle} />}
        </div>
        <p className="hint">
          Version {spec.version} · approved {formatTimestamp(spec.approved_at)}
        </p>

        {summary ? (
          <div className="card">
            <OpportunityStatus summary={summary} showDetail />
          </div>
        ) : (
          <Notice tone="info">Status isn&apos;t available for this goal right now.</Notice>
        )}

        {notice && (
          <Notice tone="error">
            {notice}
          </Notice>
        )}

        {actions.length > 0 && (
          <div className="actions actions-start" aria-label="Goal actions">
            {actions.map((a) => (
              <button
                key={a}
                type="button"
                className={armed === a ? "danger with-icon" : "with-icon"}
                disabled={pending !== null}
                onClick={() => void run(a)}
              >
                <Icon icon={ACTION_ICON[a]} />
                {pending === a ? "Saving…" : armed === a ? `Yes, ${ACTION_LABEL[a].toLowerCase()}` : ACTION_LABEL[a]}
              </button>
            ))}
            {armed && (
              <button type="button" className="link-button" onClick={() => setArmed(null)}>
                Keep it
              </button>
            )}
          </div>
        )}
        {armed && (
          <p className="hint">
            {armed === "cancel"
              ? "Cancelling stops monitoring for good. You can create a new goal later."
              : "Marking as done stops monitoring. It doesn't buy anything."}
          </p>
        )}

        <EvaluationPanel spec={spec} />
      </section>

      <DraftSummary summary={summarizeCandidate(spec as unknown as Record<string, unknown>)} badge={`Version ${spec.version}`} />
    </div>
  );
}

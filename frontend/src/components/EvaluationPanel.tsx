"use client";

import { CircleCheck, CircleQuestionMark, CircleX, type LucideIcon } from "lucide-react";
import { useEffect, useState } from "react";

import { Icon, Notice } from "@/components/Icons";
import { getApi } from "@/lib/api";
import { ApiError, adviceFor } from "@/lib/api/errors";
import type { GoalSpec } from "@/lib/contracts";
import { headlineFor, summarizeEvaluations, type EvaluationSummary } from "@/lib/evaluations";
import type { CheckStatus } from "@/lib/extension";
import { formatDuration, formatTimestamp } from "@/lib/format";
import { freshness } from "@/lib/goals";

const STATUS_ICON: Record<CheckStatus, LucideIcon> = { satisfied: CircleCheck, unsatisfied: CircleX, unknown: CircleQuestionMark };

type Load = { status: "loading" } | { status: "error"; message: string } | { status: "ok"; summary: EvaluationSummary };

/**
 * What Goal Evaluation actually found for the goal's current version
 * (GET /v1/goals/{id}/evaluations, R2-D2-04). Loaded separately from the goal so an
 * unavailable evaluation service never hides the goal itself.
 */
export function EvaluationPanel({ spec }: { spec: GoalSpec }) {
  const api = getApi();
  const [state, setState] = useState<Load>({ status: "loading" });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let cancelled = false;
    api.listEvaluations(spec.goal_id, 20).then(
      (list) => !cancelled && setState({ status: "ok", summary: summarizeEvaluations(list, spec.goal_id, spec.version) }),
      (err) =>
        !cancelled &&
        setState({
          status: "error",
          message:
            err instanceof ApiError && (err.kind === "unavailable" || err.kind === "server")
              ? "Check results are unavailable right now. Your goal is unchanged and still being watched."
              : adviceFor(err, "load").message,
        }),
    );
    return () => {
      cancelled = true;
    };
  }, [api, spec.goal_id, spec.version, attempt]);

  return (
    <section className="card evaluation" aria-labelledby="evaluation-heading">
      <h2 id="evaluation-heading">What the last check found</h2>
      <p className="hint">
        GoalPulse checks about every {formatDuration(spec.monitoring.frequency_minutes, true)} and ignores evidence older
        than {formatDuration(spec.monitoring.max_evidence_age_minutes)}. Checks use a limited search budget and only the
        sources it looked at, so an offer elsewhere can be missed.
      </p>

      {state.status === "loading" && <p className="hint">Loading check results…</p>}

      {state.status === "error" && (
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
      )}

      {state.status === "ok" && <Result summary={state.summary} version={spec.version} />}
    </section>
  );
}

function Result({ summary, version }: { summary: EvaluationSummary; version: number }) {
  const older =
    summary.olderVersionCount > 0 ? (
      <p className="hint">
        {summary.olderVersionCount} check{summary.olderVersionCount === 1 ? "" : "s"} of earlier versions of this goal{" "}
        {summary.olderVersionCount === 1 ? "is" : "are"} not shown: they don&apos;t apply to version {version}.
      </p>
    ) : null;

  if (summary.kind === "none") {
    return (
      <>
        <p>
          <strong>Waiting for the first check of version {version}.</strong>
        </p>
        {older}
      </>
    );
  }

  const { latest, headline, conditions, progress, history } = summary;
  return (
    <>
      <div className={`status tone-${headline.tone}`}>
        <strong>{headline.label}</strong>
        <p>{headline.detail}</p>
        <span className="hint">
          Last completed check:{" "}
          <time dateTime={latest.evaluated_at} title={`${latest.evaluated_at} (UTC)`}>
            {formatTimestamp(latest.evaluated_at)}
          </time>{" "}
          ({freshness(latest.evaluated_at).replace(/^Checked /, "")})
        </span>
      </div>
      {progress && <p>{progress}</p>}

      <h3>Your conditions</h3>
      <ul className="conditions">
        {conditions.map((c) => (
          <li key={c.code} className={`condition condition-${c.status}`}>
            <span className="condition-head">
              <span className="with-icon">
                <Icon icon={STATUS_ICON[c.status]} />
                {c.label}
              </span>
              <span className={`badge condition-badge-${c.status}`}>{c.statusLabel}</span>
            </span>
            <span className="hint">
              {c.reason} ·{" "}
              {c.evidenceCount === 0 ? "no evidence found" : `based on ${c.evidenceCount} observation${c.evidenceCount === 1 ? "" : "s"}`}
            </span>
          </li>
        ))}
      </ul>
      <p className="hint">
        Source links and observation times aren&apos;t part of the check record yet, so they can&apos;t be shown here. A
        failed check attempt isn&apos;t recorded either: if the last completed check looks old, recent attempts may have
        failed.
      </p>

      {history.length > 0 && (
        <details>
          <summary>Earlier checks of this version ({history.length})</summary>
          <ul className="history">
            {history.map((e) => (
              <li key={e.evaluation_id}>
                <time dateTime={e.evaluated_at} title={`${e.evaluated_at} (UTC)`}>
                  {formatTimestamp(e.evaluated_at)}
                </time>{" "}
                · {headlineFor(e).label}
              </li>
            ))}
          </ul>
        </details>
      )}
      {older}
    </>
  );
}


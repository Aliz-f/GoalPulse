"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { LifecycleBadge, OpportunityStatus } from "@/components/GoalStatus";
import { getApi } from "@/lib/api";
import { adviceFor } from "@/lib/api/errors";
import type { GoalSummary, Lifecycle } from "@/lib/contracts";
import { lifecycleLabel } from "@/lib/goals";

const FILTERS: (Lifecycle | "all")[] = ["all", "active", "paused", "completed", "cancelled"];

type Load = { status: "loading" } | { status: "error"; message: string } | { status: "ok"; goals: GoalSummary[] };

export function GoalsList() {
  const api = getApi();
  const [filter, setFilter] = useState<Lifecycle | "all">("all");
  const [state, setState] = useState<Load>({ status: "loading" });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let cancelled = false;
    api.listGoals(filter === "all" ? undefined : filter).then(
      (goals) => !cancelled && setState({ status: "ok", goals }),
      (err) => !cancelled && setState({ status: "error", message: adviceFor(err, "load").message }),
    );
    return () => {
      cancelled = true;
    };
  }, [api, filter, attempt]);

  function choose(next: Lifecycle | "all") {
    setFilter(next);
    setState({ status: "loading" });
  }

  return (
    <>
      <div className="filters" role="group" aria-label="Filter goals">
        {FILTERS.map((f) => (
          <button key={f} type="button" className="chip" aria-pressed={filter === f} onClick={() => choose(f)}>
            {f === "all" ? "All" : lifecycleLabel(f)}
          </button>
        ))}
      </div>

      {state.status === "loading" && <p className="hint">Loading your goals…</p>}

      {state.status === "error" && (
        <div role="alert" className="notice notice-error">
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
        </div>
      )}

      {state.status === "ok" && state.goals.length === 0 && (
        <div className="card empty">
          <p>{filter === "all" ? "You have no goals yet." : `No ${lifecycleLabel(filter).toLowerCase()} goals.`}</p>
          <Link href="/">Describe your first goal</Link>
        </div>
      )}

      {state.status === "ok" && state.goals.length > 0 && (
        <ul className="goal-grid">
          {state.goals.map((g) => (
            <li key={g.goal_id} className="card goal-card">
              <div className="goal-card-head">
                <h2>
                  <Link href={`/goals/${g.goal_id}`}>{g.title}</Link>
                </h2>
                <LifecycleBadge lifecycle={g.lifecycle} />
              </div>
              <OpportunityStatus summary={g} />
              <p className="hint">Version {g.version}</p>
            </li>
          ))}
        </ul>
      )}
    </>
  );
}

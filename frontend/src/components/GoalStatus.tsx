import type { GoalSummary } from "@/lib/contracts";
import { freshness, lifecycleLabel, opportunityView } from "@/lib/goals";
import { formatTimestamp } from "@/lib/format";

export function LifecycleBadge({ lifecycle }: { lifecycle: GoalSummary["lifecycle"] }) {
  return <span className={`badge lifecycle-${lifecycle}`}>{lifecycleLabel(lifecycle)}</span>;
}

export function OpportunityStatus({ summary, showDetail = false }: { summary: GoalSummary; showDetail?: boolean }) {
  const view = opportunityView(summary.opportunity_status, summary.last_evaluated_at);
  return (
    <div className={`status tone-${view.tone}`}>
      <strong>{view.label}</strong>
      {showDetail && view.detail && <p>{view.detail}</p>}
      <span
        className="hint"
        title={summary.last_evaluated_at ? formatTimestamp(summary.last_evaluated_at) : undefined}
      >
        {freshness(summary.last_evaluated_at)}
      </span>
    </div>
  );
}

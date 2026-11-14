import { Icon, LIFECYCLE_ICON, OPPORTUNITY_ICON } from "@/components/Icons";
import type { GoalSummary } from "@/lib/contracts";
import { formatTimestamp } from "@/lib/format";
import { freshness, lifecycleLabel, opportunityView } from "@/lib/goals";

export function LifecycleBadge({ lifecycle }: { lifecycle: GoalSummary["lifecycle"] }) {
  return (
    <span className={`badge lifecycle-${lifecycle}`}>
      <Icon icon={LIFECYCLE_ICON[lifecycle]} size={12} />
      {lifecycleLabel(lifecycle)}
    </span>
  );
}

export function OpportunityStatus({ summary, showDetail = false }: { summary: GoalSummary; showDetail?: boolean }) {
  const view = opportunityView(summary.opportunity_status, summary.last_evaluated_at);
  const waiting = !summary.last_evaluated_at && summary.opportunity_status !== "verified";
  return (
    <div className={`status tone-${view.tone}`}>
      <strong className="with-icon">
        <Icon icon={OPPORTUNITY_ICON[waiting ? "waiting" : summary.opportunity_status]} />
        {view.label}
      </strong>
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

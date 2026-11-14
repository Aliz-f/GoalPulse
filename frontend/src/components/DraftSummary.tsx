import type { CandidateSummary } from "@/lib/summary";

const SOURCE_LABEL = { you: "From you", configured: "In this draft", fixed: "Always" } as const;

export function DraftSummary({ summary, badge }: { summary: CandidateSummary; badge: string }) {
  if (!summary.goalType) {
    return (
      <section className="card summary" aria-label="Draft summary">
        <h2>Draft</h2>
        <p className="hint">Nothing to summarise yet.</p>
      </section>
    );
  }
  return (
    <section className="card summary" aria-label={`Summary, ${badge}`}>
      <div className="summary-head">
        <h2>{summary.title ?? "Draft"}</h2>
        <span className="badge">{badge}</span>
      </div>

      {summary.items.length > 0 && (
        <table className="items">
          <caption>Items</caption>
          <thead>
            <tr>
              <th scope="col">Item</th>
              <th scope="col">Per month</th>
              <th scope="col">Requirements</th>
            </tr>
          </thead>
          <tbody>
            {summary.items.map((it) => (
              <tr key={it.name}>
                <td>{it.name}</td>
                <td>{it.amount}</td>
                <td>{it.requirements}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {summary.sections.map((section) => (
        <div key={section.title} className="section">
          <h3>{section.title}</h3>
          <dl>
            {section.rows.map((r) => (
              <div key={r.label} className={r.missing ? "row missing" : "row"}>
                <dt>{r.label}</dt>
                <dd>
                  {r.value}
                  {!r.missing && r.source !== "you" && <span className="source">{SOURCE_LABEL[r.source]}</span>}
                </dd>
              </div>
            ))}
          </dl>
        </div>
      ))}
    </section>
  );
}

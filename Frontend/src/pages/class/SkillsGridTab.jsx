import { useMemo, useState } from "react";
import { Legend } from "../../components/charts.jsx";
import { EmptyState, Segmented, Status, StudentLink } from "../../components/ui.jsx";
import { strandsOf, summarizeConcepts } from "./summarize.js";

const MAX_COLUMNS = 24;
const SHORT = ["M", "A", "D", "B"]; // Meets, Approaching, Developing, Beginning
const LEGEND = {
  meets: "Meets (M)",
  approaching: "Approaching (A)",
  developing: "Developing (D)",
  beginning: "Beginning (B)",
  too_little_evidence: "Too little evidence",
};

// Students (rows) x skills (columns); each cell is the student's current level on that skill.
export default function SkillsGridTab({ ctx }) {
  const { classroomId, concepts } = ctx;
  const [strand, setStrand] = useState("all");
  const [onlyGaps, setOnlyGaps] = useState(true);
  const [showAll, setShowAll] = useState(false);

  const summary = useMemo(
    () => (concepts.byStudent ? summarizeConcepts(concepts.byStudent) : null),
    [concepts.byStudent]
  );

  const columns = useMemo(() => {
    if (!summary) return [];
    return summary.concepts
      .filter((c) => strand === "all" || c.strand === strand)
      .filter((c) => !onlyGaps || c.counts.needs_attention > 0)
      .sort(
        (a, b) =>
          b.counts.needs_attention - a.counts.needs_attention ||
          b.counts.emerging - a.counts.emerging ||
          a.concept_code.localeCompare(b.concept_code)
      );
  }, [summary, strand, onlyGaps]);

  const shown = showAll ? columns : columns.slice(0, MAX_COLUMNS);

  const rows = useMemo(() => {
    if (!concepts.byStudent) return [];
    return Object.entries(concepts.byStudent)
      .filter(([, list]) => list.length > 0)
      .map(([id, list]) => ({
        id,
        byCode: Object.fromEntries(list.map((c) => [c.concept_code, c])),
        gaps: list.filter((c) => c.status === "needs_attention").length,
      }))
      .sort((a, b) => b.gaps - a.gaps || a.id.localeCompare(b.id));
  }, [concepts.byStudent]);

  return (
    <Status loading={!summary && !concepts.error} error={concepts.error}>
      {summary && rows.length === 0 && (
        <EmptyState title="No reviewed work yet">The grid fills in once the class analysis has run.</EmptyState>
      )}
      {summary && rows.length > 0 && (
        <section className="card">
          <div className="toolbar">
            <label className="field-inline">
              Strand
              <select value={strand} onChange={(e) => setStrand(e.target.value)}>
                <option value="all">All strands</option>
                {strandsOf(summary.concepts).map((s) => (
                  <option key={s} value={s}>
                    {s}
                  </option>
                ))}
              </select>
            </label>
            <Segmented
              label="Which skills"
              value={onlyGaps ? "gaps" : "all"}
              onChange={(v) => setOnlyGaps(v === "gaps")}
              options={[
                { value: "gaps", label: "Skills with gaps" },
                { value: "all", label: "Every skill" },
              ]}
            />
            <span className="small muted">
              {rows.length} students · {columns.length} skills · sorted by most gaps
            </span>
          </div>

          {columns.length === 0 ? (
            <p className="notice">No skills match. Try "Every skill" or another strand.</p>
          ) : (
            <div className="grid-scroll">
              <table className="skill-grid">
                <thead>
                  <tr>
                    <th className="sticky-col">Student</th>
                    {shown.map((c) => (
                      <th key={c.concept_code} className="rot" title={`${c.concept_code} ${c.concept_name || ""}`}>
                        <span>{c.concept_code}</span>
                      </th>
                    ))}
                    <th className="num">Gaps</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r) => (
                    <tr key={r.id}>
                      <th className="sticky-col" scope="row">
                        <StudentLink classroomId={classroomId} studentId={r.id} />
                      </th>
                      {shown.map((c) => {
                        const cell = r.byCode[c.concept_code];
                        if (!cell || cell.status === "too_little_evidence") {
                          return (
                            <td key={c.concept_code} className="cell lvl-bg-none" title="Too little evidence">
                              <span className="visually-hidden">Too little evidence</span>
                            </td>
                          );
                        }
                        const lvl = cell.current_level;
                        return (
                          <td
                            key={c.concept_code}
                            className={`cell lvl-bg-${lvl} ${cell.status === "needs_attention" ? "cell-attn" : ""}`}
                            title={`${c.concept_code}: ${cell.level_label} · ${cell.status_label} (${cell.basis})`}
                          >
                            {SHORT[lvl] ?? lvl}
                          </td>
                        );
                      })}
                      <td className="num strong">{r.gaps}</td>
                    </tr>
                  ))}
                </tbody>
                <tfoot>
                  <tr>
                    <th className="sticky-col small">Need attention</th>
                    {shown.map((c) => (
                      <td key={c.concept_code} className="num small strong">
                        {c.counts.needs_attention || ""}
                      </td>
                    ))}
                    <td />
                  </tr>
                </tfoot>
              </table>
            </div>
          )}

          {columns.length > MAX_COLUMNS && (
            <button type="button" className="btn btn-sm" onClick={() => setShowAll((s) => !s)}>
              {showAll ? `Show the first ${MAX_COLUMNS} skills` : `Show all ${columns.length} skills`}
            </button>
          )}
          <Legend legend={LEGEND} />
          <p className="small muted">A thick border means the student needs attention on that skill. Hover a cell for details.</p>
        </section>
      )}
    </Status>
  );
}
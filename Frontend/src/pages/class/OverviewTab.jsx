import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { CodeTag, EmptyState, StatCard, Status, StudentLink, classPath } from "../../components/ui.jsx";
import { summarizeConcepts } from "./summarize.js";

const TOP_GAPS = 5;
const SKILLS_SHOWN = 6; // per student, before "+N more"

// The class landing page: "what do I reteach, and to whom?"
export default function OverviewTab({ ctx }) {
  const { classroomId, classroom, concepts } = ctx;
  const [expanded, setExpanded] = useState(() => new Set()); // students whose full skill list is open
  const toggleExpanded = (id) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  const summary = useMemo(
    () => (concepts.byStudent ? summarizeConcepts(concepts.byStudent) : null),
    [concepts.byStudent]
  );

  return (
    <Status loading={!summary && !concepts.error} error={concepts.error}>
      {summary && (
        <div className="stack-lg">
          <section className="stat-row" aria-label="Class totals">
            <StatCard
              label="Students with reviewed work"
              value={`${summary.studentsWithEvidence} of ${classroom.student_count}`}
            />
            <StatCard
              label="Need attention on a skill"
              value={`${summary.studentsNeedingAttention.length} of ${summary.studentsWithEvidence}`}
              tone={summary.studentsNeedingAttention.length ? "warn" : undefined}
            />
          </section>

          {summary.studentsWithEvidence === 0 ? (
            <EmptyState title="No reviewed work yet">
              Start a review from the <Link to={classPath(classroomId, "students")}>Students &amp; reviews</Link> tab.
              Once essays are reviewed and the class analysis has run, the gaps show up here.
            </EmptyState>
          ) : (
            <>
              <section className="card" aria-labelledby="gaps-title">
                <div className="row-between wrap">
                  <h2 id="gaps-title" className="section-title">Most common gaps</h2>
                  <Link to={classPath(classroomId, "skills")} className="small">
                    See every skill →
                  </Link>
                </div>
                <p className="small muted">
                  Skills where the most students need attention (Developing or Beginning on 2 of their last 3 pieces of work).
                </p>
                {summary.gaps.length === 0 ? (
                  <p className="notice">No student needs attention on any skill right now.</p>
                ) : (
                  <ol className="gap-list">
                    {summary.gaps.slice(0, TOP_GAPS).map((g) => (
                      <li key={g.concept_code} className="gap-item">
                        <div className="gap-head">
                          <CodeTag>{g.concept_code}</CodeTag>
                          <strong>{g.concept_name || g.skill_code}</strong>
                          <span className="muted small">{g.strand}</span>
                        </div>
                        <p className="gap-count">
                          <strong>
                            {g.counts.needs_attention} of {summary.studentsWithEvidence}
                          </strong>{" "}
                          students need attention
                          {g.trends.declining > 0 && (
                            <span className="trend trend-declining"> · {g.trends.declining} declining</span>
                          )}
                          {g.trends.improving > 0 && (
                            <span className="trend trend-improving"> · {g.trends.improving} improving</span>
                          )}
                        </p>
                        <div className="student-chips">
                          {g.students.needs_attention.map((id) => (
                            <StudentLink key={id} classroomId={classroomId} studentId={id} />
                          ))}
                        </div>
                      </li>
                    ))}
                  </ol>
                )}
              </section>

              <section className="card" aria-labelledby="strengths-title">
                <h2 id="strengths-title" className="section-title">Class strengths</h2>
                <p className="small muted">Skills the most students are secure on.</p>
                {summary.strengths.length === 0 ? (
                  <p className="small muted">No skill is secure for any student yet.</p>
                ) : (
                  <ul className="plain-list">
                    {summary.strengths.slice(0, 5).map((s) => (
                      <li key={s.concept_code}>
                        <CodeTag>{s.concept_code}</CodeTag> {s.concept_name}{" "}
                        <span className="muted small">
                          · {s.counts.secure} of {summary.studentsWithEvidence} secure
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
              </section>

              <section className="card" aria-labelledby="alerts-title">
                <h2 id="alerts-title" className="section-title">Students needing attention</h2>
                {summary.studentsNeedingAttention.length === 0 ? (
                  <p className="small muted">Nobody right now.</p>
                ) : (
                  <div className="table-box">
                    <table>
                      <thead>
                        <tr>
                          <th>Student</th>
                          <th className="num">Skills needing attention</th>
                          <th>Skills</th>
                        </tr>
                      </thead>
                      <tbody>
                        {summary.studentsNeedingAttention.map((s) => {
                          const open = expanded.has(s.student_id);
                          const extra = s.concepts.length - SKILLS_SHOWN;
                          return (
                            <tr key={s.student_id}>
                              <td>
                                <StudentLink classroomId={classroomId} studentId={s.student_id} />
                              </td>
                              <td className="num">{s.concepts.length}</td>
                              <td>
                                <div className="code-row">
                                  {(open ? s.concepts : s.concepts.slice(0, SKILLS_SHOWN)).map((c) => (
                                    <CodeTag key={c.concept_code} title={c.concept_name}>
                                      {c.concept_code}
                                    </CodeTag>
                                  ))}
                                  {extra > 0 && (
                                    <button
                                      type="button"
                                      className="btn-link-inline small"
                                      aria-expanded={open}
                                      onClick={() => toggleExpanded(s.student_id)}
                                    >
                                      {open ? "Show less" : `+${extra} more`}
                                    </button>
                                  )}
                                </div>
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                )}
              </section>
            </>
          )}
        </div>
      )}
    </Status>
  );
}
import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, shortId } from "../api.js";
import { studentPath } from "./StudentsPage.jsx";

const REVIEW_LABEL = {
  succeeded: "Reviewed",
  in_progress: "Reviewing",
  failed: "Review failed",
  not_reviewed: "Not reviewed yet",
};

function formatDate(value) {
  if (!value) return null;

  const date = new Date(value);

  return Number.isNaN(date.getTime())
    ? null
    : date.toLocaleDateString(undefined, {
        month: "short",
        day: "numeric",
        year: "numeric",
      });
}

function SummaryCard({ label, value, description }) {
  return (
    <div
      className="card"
      style={{
        padding: "14px 17px",
        gap: 5,
        minWidth: 0,
      }}
    >
      <span className="small muted">{label}</span>

      <strong
        style={{
          fontSize: 25,
          lineHeight: 1.2,
          color: "var(--accent)",
          fontVariantNumeric: "tabular-nums",
        }}
      >
        {value ?? 0}
      </strong>

      <span className="small muted">{description}</span>
    </div>
  );
}

function PatternCard({ pattern, type }) {
  const [expanded, setExpanded] = useState(false);

  const standards = pattern.standard_codes
    ? pattern.standard_codes.split(", ").filter(Boolean)
    : [];

  return (
    <article
      className="card"
      style={{
        padding: "14px 16px",
        gap: 9,
        minWidth: 0,
      }}
    >
      <div className="row-between wrap" style={{ gap: 7 }}>
        <span className="small muted">
          {pattern.strand || "Learning concept"}
        </span>

        <span className="pill pill-accent">
          {type === "recurring" ? "Recurring" : "New"}
        </span>
      </div>

      <h3
        style={{
          fontSize: 17,
          lineHeight: 1.3,
          overflowWrap: "anywhere",
        }}
      >
        {pattern.concept}
      </h3>

      <div className="row wrap small muted">
        <span>
          {pattern.finding_count} findings
        </span>
        <span>·</span>
        <span>
          {pattern.submission_count}{" "}
          {pattern.submission_count === 1
            ? "submission"
            : "submissions"}
        </span>
      </div>

      {standards.length > 0 && (
        <div>
          <button
            type="button"
            className="btn-link-inline"
            onClick={() => setExpanded((value) => !value)}
            aria-expanded={expanded}
          >
            {expanded
              ? "Hide learning standards"
              : `View ${standards.length} learning standards`}
          </button>

          {expanded && (
            <div className="row wrap" style={{ marginTop: 9 }}>
              {standards.map((code) => (
                <span className="code-tag" key={code}>
                  {code}
                </span>
              ))}
            </div>
          )}
        </div>
      )}
    </article>
  );
}

function StrandChart({ rows }) {
  const maxCount = Math.max(
    ...rows.map((row) => Number(row.finding_count) || 0),
    1
  );

  if (!rows.length) {
    return (
      <p className="notice">
        No strand data is available from the recorded reviews yet.
      </p>
    );
  }

  return (
    <div
      className="card"
      style={{
        padding: "18px 20px",
        gap: 17,
      }}
    >
      {rows.map((row) => {
        const count = Number(row.finding_count) || 0;
        const width = (count / maxCount) * 100;

        return (
          <div key={row.strand} style={{ display: "grid", gap: 7 }}>
            <div className="row-between wrap">
              <strong>{row.strand}</strong>

              <span className="small muted">
                {count} findings
              </span>
            </div>

            <div
              style={{
                height: 10,
                background: "var(--neutral-soft)",
                borderRadius: 20,
                overflow: "hidden",
              }}
            >
              <div
                style={{
                  height: "100%",
                  width: `${width}%`,
                  background: "var(--accent)",
                  borderRadius: 20,
                  transition: "width 0.3s ease",
                }}
              />
            </div>

            <span className="small muted">
              Across {row.submission_count}{" "}
              {row.submission_count === 1
                ? "submission"
                : "submissions"}
            </span>
          </div>
        );
      })}
    </div>
  );
}

function FindingCard({ finding }) {
  const [expanded, setExpanded] = useState(false);

  const evidence = Array.isArray(finding.evidence)
    ? finding.evidence
    : [];

  return (
    <article className="card finding">
      <div className="row wrap">
        <span className="code-tag">
          {finding.standard_code || "Standard unavailable"}
        </span>

        <span className="small muted">
          {finding.strand}
          {finding.concept ? ` · ${finding.concept}` : ""}
        </span>
      </div>

      <h3 className="finding-title">
        {finding.summary}
      </h3>

      <p className="small muted">
        Submission: {finding.submission_id || "Unavailable"}
        {finding.attempt != null
          ? ` · Attempt ${finding.attempt}`
          : ""}
      </p>

      {evidence.length > 0 && (
        <div className="evidence">
          <span className="small strong muted">
            Paragraph evidence
          </span>

          {evidence.map((item, index) => (
            <div
              className="evidence-row"
              key={`${item.paragraph}-${index}`}
            >
              <span className="para-tag">
                ¶ {item.paragraph}
              </span>

              <span>{item.reason}</span>
            </div>
          ))}
        </div>
      )}

      {finding.standard_description && (
        <div>
          <button
            type="button"
            className="btn-link-inline"
            onClick={() => setExpanded((value) => !value)}
            aria-expanded={expanded}
          >
            {expanded
              ? "Hide learning standard"
              : "View learning standard"}
          </button>

          {expanded && (
            <p className="standard">
              <strong>{finding.standard_code}:</strong>{" "}
              {finding.standard_description}
            </p>
          )}
        </div>
      )}
    </article>
  );
}

export default function FindingsPage() {
  const { classroomId, studentId } = useParams();
  const navigate = useNavigate();

  const [student, setStudent] = useState(null);
  const [students, setStudents] = useState([]);
  const [findings, setFindings] = useState([]);
  const [insights, setInsights] = useState(null);

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [findingsError, setFindingsError] = useState(null);
  const [insightsError, setInsightsError] = useState(null);

  useEffect(() => {
    let active = true;

    setLoading(true);
    setError(null);
    setFindingsError(null);
    setInsightsError(null);
    setStudent(null);
    setStudents([]);
    setFindings([]);
    setInsights(null);

    Promise.allSettled([
      api.students(classroomId),
      api.findings(studentId),
      api.studentInsights(studentId),
    ])
      .then(([studentsResult, findingsResult, insightsResult]) => {
        if (!active) return;

        if (studentsResult.status === "fulfilled") {
          const rows = Array.isArray(studentsResult.value)
            ? studentsResult.value
            : [];

          setStudents(rows);

          setStudent(
            rows.find(
              (row) => row.student_id === studentId
            ) || null
          );
        } else {
          setError(
            studentsResult.reason?.message ||
              "Unable to load student information."
          );
        }

        if (findingsResult.status === "fulfilled") {
          setFindings(
            Array.isArray(findingsResult.value)
              ? findingsResult.value
              : []
          );
        } else {
          setFindingsError(
            findingsResult.reason?.message ||
              "Unable to load detailed findings."
          );
        }

        if (insightsResult.status === "fulfilled") {
          setInsights(insightsResult.value);
        } else {
          setInsightsError(
            insightsResult.reason?.message ||
              "Unable to load learning insights."
          );
        }
      })
      .finally(() => {
        if (active) setLoading(false);
      });

    return () => {
      active = false;
    };
  }, [classroomId, studentId]);

  // Navigate through all students, not only reviewed students.
  const orderedStudents = useMemo(
    () =>
      [...students].sort((a, b) =>
        a.student_id.localeCompare(b.student_id)
      ),
    [students]
  );

  const studentIndex = orderedStudents.findIndex(
    (row) => row.student_id === studentId
  );

  const previous =
    studentIndex > 0
      ? orderedStudents[studentIndex - 1]
      : null;

  const next =
    studentIndex >= 0 &&
    studentIndex < orderedStudents.length - 1
      ? orderedStudents[studentIndex + 1]
      : null;

  useEffect(() => {
    const onKeyDown = (event) => {
      if (
        /^(INPUT|TEXTAREA|SELECT|BUTTON)$/.test(
          event.target.tagName
        ) ||
        event.metaKey ||
        event.ctrlKey ||
        event.altKey
      ) {
        return;
      }

      if (event.key === "ArrowLeft" && previous) {
        navigate(
          studentPath(classroomId, previous.student_id)
        );
      }

      if (event.key === "ArrowRight" && next) {
        navigate(
          studentPath(classroomId, next.student_id)
        );
      }
    };

    window.addEventListener("keydown", onKeyDown);

    return () =>
      window.removeEventListener("keydown", onKeyDown);
  }, [previous, next, classroomId, navigate]);

  const summary = insights?.summary || {};
  const recurring = insights?.recurring_patterns || [];
  const newlyObserved = insights?.new_patterns || [];
  const strandChart = insights?.strand_chart || [];

  return (
    <>
      {/* Top navigation */}
      <div className="row-between wrap">
        <Link
          to={`/classrooms/${encodeURIComponent(classroomId)}`}
          className="back"
        >
          ← Back to the class
        </Link>

        {orderedStudents.length > 1 && studentIndex >= 0 && (
          <nav className="pager" aria-label="Other students">
            <button
              type="button"
              className="btn btn-sm"
              disabled={!previous}
              onClick={() =>
                navigate(
                  studentPath(classroomId, previous.student_id)
                )
              }
            >
              ← Previous
            </button>

            <span className="small muted">
              {studentIndex + 1} of {orderedStudents.length}
            </span>

            <button
              type="button"
              className="btn btn-sm"
              disabled={!next}
              onClick={() =>
                navigate(
                  studentPath(classroomId, next.student_id)
                )
              }
            >
              Next →
            </button>
          </nav>
        )}
      </div>

      {loading ? (
        <p className="notice">Loading student insights…</p>
      ) : error ? (
        <p className="notice error" role="alert">
          {error}
        </p>
      ) : (
        <>
          {/* Student information */}
          <div className="heading">
            <h1>
              Student{" "}
              <span
                className="mono h1-mono"
                title={studentId}
              >
                {shortId(studentId)}
              </span>
            </h1>

            <p className="muted">
              Student learning insights from recorded AI reviews.
            </p>
          </div>

          {student && (
            <section className="card">
              <div className="row-between wrap">
                <div>
                  <span className="small strong muted">
                    LATEST ASSIGNMENT
                  </span>

                  <h2 className="card-title">
                    {student.assignment_title ||
                      (student.submission_id
                        ? "Assignment title unavailable"
                        : "No submission recorded")}
                  </h2>
                </div>

                <span
                  className={`pill pill-${
                    student.review_status || "not_reviewed"
                  }`}
                >
                  {REVIEW_LABEL[student.review_status] ||
                    "Not reviewed yet"}
                </span>
              </div>

              <div className="row wrap small muted">
                {student.submission_id && (
                  <span>
                    Submission {student.submission_id}
                  </span>
                )}

                {student.submitted_at && (
                  <span>
                    Submitted {formatDate(student.submitted_at)}
                  </span>
                )}
              </div>
            </section>
          )}

          {/* Compact summary cards */}
          {insightsError ? (
            <p className="notice error" role="alert">
              Learning insights unavailable: {insightsError}
            </p>
          ) : (
            <>
              <section className="stack">
                <div className="heading">
                  <h2>Learning Overview</h2>
                </div>

                <div
                  style={{
                    display: "grid",
                    gridTemplateColumns:
                      "repeat(auto-fit, minmax(145px, 1fr))",
                    gap: 12,
                  }}
                >
                  <SummaryCard
                    label="Findings"
                    value={summary.finding_count}
                    description="Recorded AI findings"
                  />

                  <SummaryCard
                    label="Submissions"
                    value={summary.submissions_with_findings}
                    description="With recorded findings"
                  />

                  <SummaryCard
                    label="Standards"
                    value={summary.standard_count}
                    description="Identified standards"
                  />

                  <SummaryCard
                    label="Concepts"
                    value={summary.concept_count}
                    description="Identified concepts"
                  />
                </div>
              </section>

              {/* Recurring patterns */}
              <section className="stack">
                <div className="row-between wrap">
                  <div className="heading">
                    <h2>Recurring Patterns</h2>
                    <p className="small muted">
                      Concepts appearing across multiple submissions.
                    </p>
                  </div>

                  <span className="pill pill-accent">
                    {recurring.length} patterns
                  </span>
                </div>

                {recurring.length === 0 ? (
                  <p className="notice">
                    No recurring patterns identified yet.
                  </p>
                ) : (
                  <div
                    style={{
                      display: "grid",
                      gridTemplateColumns:
                        "repeat(auto-fit, minmax(230px, 1fr))",
                      gap: 12,
                    }}
                  >
                    {recurring.map((pattern) => (
                      <PatternCard
                        key={`${pattern.strand}-${pattern.concept}`}
                        pattern={pattern}
                        type="recurring"
                      />
                    ))}
                  </div>
                )}
              </section>

              {/* Newly observed patterns */}
              <section className="stack">
                <div className="row-between wrap">
                  <div className="heading">
                    <h2>Newly Observed Patterns</h2>
                    <p className="small muted">
                      Concepts appearing in the latest reviewed submission
                      but not earlier ones.
                    </p>
                  </div>

                  <span className="pill pill-accent">
                    {newlyObserved.length} new
                  </span>
                </div>

                {newlyObserved.length === 0 ? (
                  <p className="notice">
                    No newly observed patterns in the latest review.
                  </p>
                ) : (
                  <div
                    style={{
                      display: "grid",
                      gridTemplateColumns:
                        "repeat(auto-fit, minmax(230px, 1fr))",
                      gap: 12,
                    }}
                  >
                    {newlyObserved.map((pattern) => (
                      <PatternCard
                        key={`${pattern.strand}-${pattern.concept}`}
                        pattern={pattern}
                        type="new"
                      />
                    ))}
                  </div>
                )}
              </section>

              {/* Real database-driven graph */}
              <section className="stack">
                <div className="heading">
                  <h2>Findings by Learning Strand</h2>
                  <p className="small muted">
                    Distribution of recorded findings across strands.
                  </p>
                </div>

                <StrandChart rows={strandChart} />
              </section>
            </>
          )}

          {/* Detailed findings - collapsed by default */}
          <details className="card" style={{ padding: 18 }}>
            <summary
              style={{
                cursor: "pointer",
                color: "var(--accent)",
                fontWeight: 600,
                fontSize: 16,
              }}
            >
              Detailed AI Findings ({findings.length})
            </summary>

            <div className="stack" style={{ marginTop: 16 }}>
              {findingsError ? (
                <p className="notice error">
                  {findingsError}
                </p>
              ) : findings.length === 0 ? (
                <p className="notice">
                  No detailed findings are available yet.
                </p>
              ) : (
                findings.map((finding, index) => (
                  <FindingCard
                    key={`${finding.id}-${index}`}
                    finding={finding}
                  />
                ))
              )}
            </div>
          </details>

          <p className="small muted">
            Insights reflect recorded AI review patterns. They are not
            grades, mastery percentages or a final evaluation.
          </p>
        </>
      )}
    </>
  );
}
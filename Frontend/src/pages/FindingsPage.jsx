import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, shortId } from "../api.js";
import { Status } from "../components/Layout.jsx";
import { studentPath } from "./StudentsPage.jsx";

function FindingCard({ f }) {
  const [open, setOpen] = useState(false);
  return (
    <article className="card finding">
      <div className="row wrap">
        <span className="code-tag">{f.standard_code}</span>
        <span className="small muted">
          {f.strand} · {f.concept}
        </span>
      </div>
      <h2 className="finding-title">{f.summary}</h2>
      <div className="evidence">
        <span className="small strong muted">Evidence</span>
        {f.evidence.map((e, i) => (
          <div key={i} className="evidence-row">
            <span className="para-tag">¶ {e.paragraph}</span>
            <span>{e.reason}</span>
          </div>
        ))}
      </div>
      <div>
        <button type="button" className="btn-link-inline" aria-expanded={open} onClick={() => setOpen(!open)}>
          {open ? "Hide the standard" : `Show the standard ${f.standard_code}`}
        </button>
        {open && (
          <p className="standard">
            <strong>{f.standard_code}:</strong> {f.standard_description}
          </p>
        )}
      </div>
    </article>
  );
}

// Screen 3: GET /students/{id}/findings, with Previous / Next through the reviewed students
export default function FindingsPage() {
  const { classroomId, studentId } = useParams();
  const navigate = useNavigate();
  const [findings, setFindings] = useState(null);
  const [students, setStudents] = useState([]);
  const [error, setError] = useState(null);

  useEffect(() => {
    setFindings(null);
    setError(null);
    api.findings(studentId).then(setFindings).catch((e) => setError(e.message));
  }, [studentId]);

  useEffect(() => {
    api.students(classroomId).then(setStudents).catch(() => setStudents([]));
  }, [classroomId]);

  const reviewed = useMemo(
    () =>
      students
        .filter((s) => s.review_status === "succeeded")
        .sort((a, b) => a.student_id.localeCompare(b.student_id)),
    [students]
  );
  const index = reviewed.findIndex((s) => s.student_id === studentId);
  const prev = index > 0 ? reviewed[index - 1] : null;
  const next = index >= 0 && index < reviewed.length - 1 ? reviewed[index + 1] : null;

  // ← and → move between students (ignored while typing in a field)
  useEffect(() => {
    const onKey = (e) => {
      if (/^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName) || e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.key === "ArrowLeft" && prev) navigate(studentPath(classroomId, prev.student_id));
      if (e.key === "ArrowRight" && next) navigate(studentPath(classroomId, next.student_id));
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [prev, next, classroomId, navigate]);

  const first = findings && findings[0];

  return (
    <>
      <div className="row-between wrap">
        <Link to={`/classrooms/${encodeURIComponent(classroomId)}`} className="back">
          ← Back to the class
        </Link>
        {reviewed.length > 1 && index >= 0 && (
          <nav className="pager" aria-label="Other students">
            <button
              type="button"
              className="btn btn-sm"
              disabled={!prev}
              onClick={() => navigate(studentPath(classroomId, prev.student_id))}
            >
              ← Previous
            </button>
            <span className="small muted">
              {index + 1} of {reviewed.length}
            </span>
            <button
              type="button"
              className="btn btn-sm"
              disabled={!next}
              onClick={() => navigate(studentPath(classroomId, next.student_id))}
            >
              Next →
            </button>
          </nav>
        )}
      </div>
      <div className="heading">
        <h1>
          Student <span className="mono h1-mono" title={studentId}>{shortId(studentId)}</span>
        </h1>
        {first && (
          <p className="muted">
            Submission {first.submission_id} · attempt {first.attempt} · {findings.length}{" "}
            {findings.length === 1 ? "finding" : "findings"}
          </p>
        )}
      </div>
      <Status loading={!findings} error={error}>
        {findings && findings.length === 0 && (
          <p className="notice">No findings for this student. Their essay may not be reviewed yet.</p>
        )}
        <div className="stack">
          {(findings || []).map((f) => (
            <FindingCard key={f.id} f={f} />
          ))}
        </div>
        {reviewed.length > 1 && <p className="small muted">Tip: use the ← and → keys to move between students.</p>}
      </Status>
    </>
  );
}

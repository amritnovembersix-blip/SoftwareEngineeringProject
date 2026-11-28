import { useEffect, useMemo, useState } from "react";
import { Link, Navigate, Route, Routes, useNavigate, useParams } from "react-router-dom";
import { api, formatDate, formatWeek, shortId } from "../api.js";
import { Sparkline } from "../components/charts.jsx";
import FindingCard from "../components/FindingCard.jsx";
import {
  CodeTag,
  ConceptStatus,
  ConfidenceBadge,
  EmptyState,
  LevelChip,
  Segmented,
  StatCard,
  Status,
  TabNav,
  TrendArrow,
  classPath,
  studentPath,
} from "../components/ui.jsx";

const basisText = (basis) => {
  const m = /^(\d+)\/(\d+)$/.exec(basis || "");
  return m ? `${m[1]} of ${m[2]}` : basis || "—";
};

// ---------- Skills tab: GET /students/{id}/concepts + /concept-history ----------

function SkillsTab({ studentId, classroomId, concepts }) {
  const [history, setHistory] = useState(null);
  // Open on what needs attention first, when there is any (a student can have ~90 skills).
  const [status, setStatus] = useState(() => (concepts?.summary?.needs_attention ? "needs_attention" : "all"));
  const [strand, setStrand] = useState("");
  const [open, setOpen] = useState(null);

  useEffect(() => {
    let active = true;
    api
      .conceptHistory(studentId, { classroomId })
      .then((h) => active && setHistory(h))
      .catch(() => active && setHistory([]));
    return () => {
      active = false;
    };
  }, [studentId, classroomId]);

  const points = useMemo(
    () => Object.fromEntries((history || []).map((h) => [h.concept_code, h.points])),
    [history]
  );
  const list = concepts?.concepts || [];
  const strands = [...new Set(list.map((c) => c.strand).filter(Boolean))].sort();
  const shown = list.filter((c) => (status === "all" || c.status === status) && (!strand || c.strand === strand));
  const sum = concepts?.summary || {};

  if (!list.length) {
    return <EmptyState title="No skill data yet">This student's work hasn't been analysed in this class yet.</EmptyState>;
  }

  return (
    <section className="card">
      <div className="toolbar">
        <Segmented
          label="Filter by status"
          value={status}
          onChange={setStatus}
          options={[
            { value: "all", label: "All", count: list.length },
            { value: "needs_attention", label: "Needs attention", count: sum.needs_attention },
            { value: "emerging", label: "Emerging", count: sum.emerging },
            { value: "secure", label: "Secure", count: sum.secure },
            { value: "too_little_evidence", label: "Too little evidence", count: sum.too_little_evidence },
          ]}
        />
        <label className="field-inline">
          Strand
          <select value={strand} onChange={(e) => setStrand(e.target.value)}>
            <option value="">All strands</option>
            {strands.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </label>
      </div>

      <div className="table-box">
        <table>
          <thead>
            <tr>
              <th>Skill</th>
              <th>Status</th>
              <th>Level now</th>
              <th>Basis</th>
              <th>Trend</th>
              <th>Over the term</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((c) => {
              const p = points[c.concept_code];
              const isOpen = open === c.concept_code;
              return (
                <FragmentRow key={c.concept_code}>
                  <tr className="row-link" onClick={() => setOpen(isOpen ? null : c.concept_code)}>
                    <td>
                      <button type="button" className="btn-link-inline" aria-expanded={isOpen}>
                        <CodeTag>{c.concept_code}</CodeTag>
                      </button>{" "}
                      <span>{c.concept_name}</span> <span className="muted small">{c.strand}</span>
                    </td>
                    <td>
                      <ConceptStatus status={c.status} label={c.status_label} />
                    </td>
                    <td>
                      <LevelChip level={c.current_level} label={c.level_label} />
                    </td>
                    <td className="small nowrap" title="Growth area on this many of the recent pieces of work">
                      {basisText(c.basis)}
                    </td>
                    <td>
                      <TrendArrow trend={c.trend} />
                    </td>
                    <td>{history ? <Sparkline points={p} /> : <span className="muted small">…</span>}</td>
                  </tr>
                  {isOpen && (
                    <tr className="detail-row">
                      <td colSpan={6}>
                        {p && p.length ? (
                          <ol className="points-list">
                            {p.map((pt) => (
                              <li key={pt.submission_id}>
                                <span className="small muted">Week of {formatWeek(pt.week_start)}</span>
                                <span>{pt.assignment_title}</span>
                                <LevelChip level={pt.level} label={pt.level_label} />
                              </li>
                            ))}
                          </ol>
                        ) : (
                          <p className="small muted">No reviewed work shows this skill yet.</p>
                        )}
                      </td>
                    </tr>
                  )}
                </FragmentRow>
              );
            })}
            {shown.length === 0 && (
              <tr>
                <td colSpan={6} className="empty-row">
                  No skills match.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      <p className="small muted">
        Needs attention = Developing or Beginning on at least 2 of the last 3 pieces of work. Click a skill to see each
        piece of work.
      </p>
    </section>
  );
}

function FragmentRow({ children }) {
  return <>{children}</>;
}

// ---------- Findings tab: GET /students/{id}/findings (read-only) ----------

function FindingsTab({ findings }) {
  const assignments = useMemo(() => {
    const seen = new Map();
    for (const f of findings || []) {
      if (!seen.has(f.assignment_id)) {
        seen.set(f.assignment_id, { id: f.assignment_id, title: f.assignment_title, submitted_at: f.submitted_at, n: 0 });
      }
      seen.get(f.assignment_id).n += 1;
    }
    return [...seen.values()]; // newest first (backend order)
  }, [findings]);

  const [assignment, setAssignment] = useState("");
  const [confidence, setConfidence] = useState("");

  useEffect(() => {
    if (!assignment && assignments.length) setAssignment(assignments[0].id);
  }, [assignments, assignment]);

  if (!findings?.length) {
    return <EmptyState title="No findings">No growth areas were found in this student's reviewed work.</EmptyState>;
  }

  const inAssignment = findings.filter((f) => !assignment || assignment === "all" || f.assignment_id === assignment);
  const shown = inAssignment.filter((f) => !confidence || f.confidence_label === confidence);

  return (
    <div className="stack">
      <div className="toolbar card">
        <label className="field-inline">
          Assignment
          <select value={assignment} onChange={(e) => setAssignment(e.target.value)}>
            <option value="all">All assignments ({findings.length})</option>
            {assignments.map((a) => (
              <option key={a.id} value={a.id}>
                {a.title} ({a.n})
              </option>
            ))}
          </select>
        </label>
        <label className="field-inline">
          Confidence
          <select value={confidence} onChange={(e) => setConfidence(e.target.value)}>
            <option value="">Any</option>
            <option value="High">High</option>
            <option value="Medium">Medium</option>
            <option value="Low">Low</option>
          </select>
        </label>
        <span className="small muted">
          Showing {shown.length} of {inAssignment.length}
        </span>
      </div>
      <p className="small muted">
        What the AI reviewer flagged as growth areas. These are AI drafts: check them against the student's work before
        acting on them.
      </p>
      {shown.length === 0 && <p className="notice">No findings match these filters.</p>}
      {shown.map((f) => (
        <FindingCard key={f.id} finding={f} />
      ))}
    </div>
  );
}

// ---------- Strengths tab: GET /students/{id}/strengths ----------

function StrengthsTab({ strengths }) {
  if (!strengths?.length) {
    return <EmptyState title="No notable strengths recorded yet">Strengths appear as the student's work is reviewed.</EmptyState>;
  }
  const byAssignment = new Map();
  for (const s of strengths) {
    if (!byAssignment.has(s.assignment_id)) byAssignment.set(s.assignment_id, { title: s.assignment_title, date: s.submitted_at, items: [] });
    byAssignment.get(s.assignment_id).items.push(s);
  }
  return (
    <div className="stack">
      {[...byAssignment.entries()].map(([id, a]) => (
        <section key={id} className="card">
          <h3 className="section-title">
            {a.title} <span className="muted small">· {formatDate(a.date)}</span>
          </h3>
          <ul className="strength-list">
            {a.items.map((s) => (
              <li key={`${id}-${s.standard_code}`}>
                <div className="row wrap">
                  <CodeTag title={s.standard_description}>{s.standard_code}</CodeTag>
                  <span className="small muted">
                    {s.strand} · {s.concept}
                  </span>
                  <ConfidenceBadge label={s.confidence_label} />
                </div>
                {s.strength_note && <p>{s.strength_note}</p>}
              </li>
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
}

// ---------- page ----------

export default function StudentPage() {
  const { classroomId, studentId } = useParams();
  const navigate = useNavigate();
  const [concepts, setConcepts] = useState(null);
  const [findings, setFindings] = useState(null);
  const [strengths, setStrengths] = useState(null);
  const [classroom, setClassroom] = useState(null);
  const [classmates, setClassmates] = useState([]);
  const [error, setError] = useState(null);

  useEffect(() => {
    let active = true;
    setConcepts(null);
    setFindings(null);
    setStrengths(null);
    setError(null);
    Promise.all([api.concepts(studentId, classroomId), api.findings(studentId), api.strengths(studentId)])
      .then(([c, f, s]) => {
        if (!active) return;
        setConcepts(c);
        // Note: findings/strengths have no classroom filter yet, so a student in two of this
        // teacher's classes shows both classes' work here (docs/BACKEND_GAPS.md).
        setFindings(f);
        setStrengths(s);
      })
      .catch((e) => active && setError(e));
    return () => {
      active = false;
    };
  }, [studentId, classroomId]);

  useEffect(() => {
    let active = true;
    Promise.all([api.classrooms(), api.students(classroomId)])
      .then(([rows, studs]) => {
        if (!active) return;
        setClassroom(rows.find((r) => r.id === classroomId) || null);
        setClassmates(studs.filter((s) => s.review_status === "succeeded").map((s) => s.student_id).sort());
      })
      .catch(() => {});
    return () => {
      active = false;
    };
  }, [classroomId]);

  const index = classmates.indexOf(studentId);
  const prev = index > 0 ? classmates[index - 1] : null;
  const next = index >= 0 && index < classmates.length - 1 ? classmates[index + 1] : null;
  const base = studentPath(classroomId, studentId);
  const sum = concepts?.summary || {};
  const lastReviewed = findings?.length ? findings[0].submitted_at : null;

  return (
    <div className="page-wrap">
      <nav className="crumbs" aria-label="Breadcrumb">
        <Link to="/">Home</Link> <span aria-hidden="true">/</span>{" "}
        <Link to={classPath(classroomId)}>{classroom?.name || "Class"}</Link> <span aria-hidden="true">/</span>{" "}
        <span>Student {shortId(studentId)}</span>
      </nav>

      <header className="page-head row-between wrap">
        <div>
          <h1>
            Student <span className="mono" title={studentId}>{shortId(studentId)}</span>
          </h1>
          <p className="muted small">
            {concepts?.as_of ? `Analysis as of ${formatDate(concepts.as_of)}` : ""}
            {lastReviewed ? ` · last work submitted ${formatDate(lastReviewed)}` : ""}
          </p>
        </div>
        {classmates.length > 1 && index >= 0 && (
          <nav className="pager" aria-label="Other students">
            <button type="button" className="btn btn-sm" disabled={!prev} onClick={() => navigate(studentPath(classroomId, prev))}>
              ← Previous
            </button>
            <span className="small muted">
              {index + 1} of {classmates.length}
            </span>
            <button type="button" className="btn btn-sm" disabled={!next} onClick={() => navigate(studentPath(classroomId, next))}>
              Next →
            </button>
          </nav>
        )}
      </header>

      <Status loading={!concepts && !error} error={error}>
        {concepts && (
          <>
            <section className="stat-row" aria-label="Student summary">
              <StatCard label="Needs attention" value={sum.needs_attention ?? 0} tone={sum.needs_attention ? "warn" : undefined} />
              <StatCard label="Emerging" value={sum.emerging ?? 0} />
              <StatCard label="Secure" value={sum.secure ?? 0} tone="ok" />
              <StatCard label="Notable strengths" value={strengths?.length ?? 0} tone="ok" />
              <StatCard label="Growth areas found" value={findings?.length ?? 0} tone="accent" />
            </section>

            <TabNav
              label="Student views"
              tabs={[
                { to: base, label: "Skills", end: true },
                { to: `${base}/findings`, label: "Findings", count: findings?.length },
                { to: `${base}/strengths`, label: "Strengths", count: strengths?.length },
              ]}
            />

            <Routes>
              <Route index element={<SkillsTab studentId={studentId} classroomId={classroomId} concepts={concepts} />} />
              <Route path="findings" element={<FindingsTab findings={findings} />} />
              <Route path="strengths" element={<StrengthsTab strengths={strengths} />} />
              <Route path="*" element={<Navigate to={base} replace />} />
            </Routes>
          </>
        )}
      </Status>
    </div>
  );
}
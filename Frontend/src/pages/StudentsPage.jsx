import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, shortId } from "../api.js";
import { Status } from "../components/Layout.jsx";

const POLL_MS = 2000;
const FLASH_MS = 1600;

// Remember a running review per class, so reloading the page keeps showing its progress.
const runKey = (classroomId) => `sass-run-${classroomId}`;
const saveRun = (classroomId, runId) => {
  try {
    if (runId) sessionStorage.setItem(runKey(classroomId), runId);
    else sessionStorage.removeItem(runKey(classroomId));
  } catch {
    /* storage unavailable: progress still works until the page reloads */
  }
};
const loadRun = (classroomId) => {
  try {
    return sessionStorage.getItem(runKey(classroomId));
  } catch {
    return null;
  }
};

const STATUS_LABEL = {
  not_reviewed: "Not reviewed yet",
  in_progress: "Reviewing",
  succeeded: "Reviewed",
  failed: "Review failed",
};
const STATUS_ORDER = { failed: 0, in_progress: 1, not_reviewed: 2, succeeded: 3 };

const FILTERS = [
  { key: "all", label: "All", test: () => true },
  { key: "not_reviewed", label: "Not reviewed", test: (s) => s.review_status !== "succeeded" },
  { key: "succeeded", label: "Reviewed", test: (s) => s.review_status === "succeeded" },
];

const SORTS = {
  student: (a, b) => a.student_id.localeCompare(b.student_id),
  status: (a, b) => (STATUS_ORDER[a.review_status] ?? 9) - (STATUS_ORDER[b.review_status] ?? 9),
  findings: (a, b) => a.finding_count - b.finding_count,
};

export const studentPath = (classroomId, studentId) =>
  `/classrooms/${encodeURIComponent(classroomId)}/students/${encodeURIComponent(studentId)}`;

// Screen 2: GET /classrooms/{id}/students, plus the "Review class" button
export default function StudentsPage() {
  const { classroomId } = useParams();
  const navigate = useNavigate();
  const [classroom, setClassroom] = useState(null);
  const [students, setStudents] = useState(null);
  const [error, setError] = useState(null);
  const [run, setRun] = useState(null); // {run_id, status, done, total, error}
  const [reviewError, setReviewError] = useState(null);
  const [showBanner, setShowBanner] = useState(false);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState("all");
  const [sort, setSort] = useState({ key: "student", dir: 1 });
  const [flash, setFlash] = useState(() => new Set());
  const timer = useRef(null);
  const lastStatus = useRef(new Map());

  // Load the class and its students. Rows whose review just finished flash briefly.
  const loadStudents = useCallback(() => {
    return Promise.all([api.classrooms(), api.students(classroomId)])
      .then(([classrooms, rows]) => {
        setClassroom(classrooms.find((c) => c.id === classroomId) || { id: classroomId, name: classroomId });
        const fresh = new Set();
        for (const s of rows) {
          const before = lastStatus.current.get(s.student_id);
          if (before && before !== "succeeded" && s.review_status === "succeeded") fresh.add(s.student_id);
          lastStatus.current.set(s.student_id, s.review_status);
        }
        if (fresh.size) {
          setFlash(fresh);
          setTimeout(() => setFlash(new Set()), FLASH_MS);
        }
        setStudents(rows);
      })
      .catch((e) => setError(e.message));
  }, [classroomId]);

  const poll = useCallback(
    (runId) => {
      api
        .reviewProgress(runId)
        .then((p) => {
          setRun(p);
          loadStudents(); // rows flip to "Reviewed" one by one while the review runs
          if (p.status === "succeeded" || p.status === "failed") {
            saveRun(classroomId, null);
            setShowBanner(true);
          } else {
            timer.current = setTimeout(() => poll(runId), POLL_MS);
          }
        })
        .catch((e) => {
          saveRun(classroomId, null);
          setRun(null);
          setReviewError(e.message);
        });
    },
    [classroomId, loadStudents]
  );

  useEffect(() => {
    lastStatus.current = new Map();
    loadStudents();
    const saved = loadRun(classroomId);
    if (saved) poll(saved);
    return () => clearTimeout(timer.current);
  }, [classroomId, loadStudents, poll]);

  const startReview = () => {
    setReviewError(null);
    setShowBanner(false);
    setRun({ status: "queued", done: 0, total: students ? students.length : 0 });
    api
      .startReview(classroomId)
      .then(({ run_id }) => {
        saveRun(classroomId, run_id);
        poll(run_id);
      })
      .catch((e) => {
        setRun(null);
        setReviewError(e.message);
      });
  };

  const counts = useMemo(() => {
    const all = students || [];
    return Object.fromEntries(FILTERS.map((f) => [f.key, all.filter(f.test).length]));
  }, [students]);

  const visible = useMemo(() => {
    const q = query.trim().toLowerCase();
    const test = FILTERS.find((f) => f.key === filter).test;
    return (students || [])
      .filter((s) => test(s) && (!q || s.student_id.toLowerCase().includes(q)))
      .sort((a, b) => SORTS[sort.key](a, b) * sort.dir || SORTS.student(a, b));
  }, [students, query, filter, sort]);

  const toggleSort = (key) =>
    setSort((s) => (s.key === key ? { key, dir: -s.dir } : { key, dir: key === "findings" ? -1 : 1 }));
  const sortMark = (key) => (sort.key === key ? (sort.dir === 1 ? " ↑" : " ↓") : "");
  const ariaSort = (key) => (sort.key === key ? (sort.dir === 1 ? "ascending" : "descending") : "none");

  const running = run && (run.status === "queued" || run.status === "running");
  const reviewed = counts.succeeded || 0;
  // The table can refresh a moment before the progress count, so show whichever is further along.
  const done = run ? Math.min(run.total || 0, Math.max(run.done || 0, running ? reviewed : 0)) : 0;
  const pct = run && run.total ? Math.round((done / run.total) * 100) : 0;
  const firstReviewed = (students || []).filter((s) => s.review_status === "succeeded").sort(SORTS.student)[0];

  return (
    <>
      <Link to="/" className="back">← All classrooms</Link>
      <Status loading={!students} error={error}>
        <div className="heading row-between wrap">
          <div>
            <h1>{classroom && classroom.name}</h1>
            <p className="muted">
              Grade {classroom && classroom.grade_level} · {students && students.length} students ·{" "}
              <span className="strong">{reviewed} reviewed</span>
            </p>
          </div>
          <button type="button" className="btn btn-primary" onClick={startReview} disabled={running}>
            {running ? (
              <>
                <span className="spinner" aria-hidden="true" /> Reviewing…
              </>
            ) : (
              "Review class"
            )}
          </button>
        </div>

        {run && (running || !showBanner) && (
          <div className={`progress-panel ${run.status}`} role="status" aria-live="polite">
            <div className="row-between">
              <strong>
                {running && `Reviewing… ${done} of ${run.total}`}
                {run.status === "succeeded" && `Review finished: ${run.done} of ${run.total}`}
                {run.status === "failed" && "Review finished with errors"}
              </strong>
              <span className="small muted">{pct}%</span>
            </div>
            <div className="bar" aria-hidden="true">
              <div className={`bar-fill ${running ? "moving" : ""}`} style={{ width: `${Math.max(pct, running ? 4 : 0)}%` }} />
            </div>
            {running && (
              <p className="small muted">
                Each essay is checked by the AI and its findings are saved. Rows update as they finish.
              </p>
            )}
          </div>
        )}

        {showBanner && run && (
          <div className={`banner ${run.status}`} role="status">
            <div>
              <strong>
                {run.status === "succeeded"
                  ? `All ${run.total} essays reviewed.`
                  : "The review finished with errors."}
              </strong>{" "}
              <span className="muted">
                {run.status === "succeeded"
                  ? "Open any student to see what the AI found."
                  : `${run.error || "Some reviews failed"}. The rest were saved.`}
              </span>
            </div>
            <div className="row">
              {firstReviewed && (
                <button
                  type="button"
                  className="btn btn-primary btn-sm"
                  onClick={() => navigate(studentPath(classroomId, firstReviewed.student_id))}
                >
                  See findings →
                </button>
              )}
              <button type="button" className="btn btn-sm" onClick={() => setShowBanner(false)}>
                Dismiss
              </button>
            </div>
          </div>
        )}
        {reviewError && <p className="notice error" role="alert">Couldn't start the review: {reviewError}</p>}

        {students && students.length === 0 && (
          <p className="notice">No students are enrolled in this class. Run seed-demo to add the demo students.</p>
        )}

        {students && students.length > 0 && (
          <>
            <div className="toolbar">
              <label className="search">
                <span className="visually-hidden">Search students</span>
                <input
                  id="student-search"
                  type="search"
                  placeholder="Search by student ID"
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                />
              </label>
              <div className="chips" role="group" aria-label="Filter by review status">
                {FILTERS.map((f) => (
                  <button
                    key={f.key}
                    type="button"
                    className="chip"
                    aria-pressed={filter === f.key}
                    onClick={() => setFilter(f.key)}
                  >
                    {f.label} <span className="chip-count">{counts[f.key]}</span>
                  </button>
                ))}
              </div>
            </div>

            <div className="table-box">
              <table>
                <thead>
                  <tr>
                    <th aria-sort={ariaSort("student")}>
                      <button type="button" className="th-btn" onClick={() => toggleSort("student")}>
                        Student{sortMark("student")}
                      </button>
                    </th>
                    <th>Assignment</th>
                    <th aria-sort={ariaSort("status")}>
                      <button type="button" className="th-btn" onClick={() => toggleSort("status")}>
                        Review{sortMark("status")}
                      </button>
                    </th>
                    <th className="num" aria-sort={ariaSort("findings")}>
                      <button type="button" className="th-btn" onClick={() => toggleSort("findings")}>
                        Findings{sortMark("findings")}
                      </button>
                    </th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {visible.map((s) => {
                    const canOpen = s.review_status === "succeeded";
                    const to = studentPath(classroomId, s.student_id);
                    return (
                      <tr
                        key={s.student_id}
                        className={`${canOpen ? "row-link" : ""} ${flash.has(s.student_id) ? "row-flash" : ""}`}
                        onClick={canOpen ? () => navigate(to) : undefined}
                      >
                        <td className="mono" title={s.student_id}>
                          {canOpen ? (
                            <Link to={to} onClick={(e) => e.stopPropagation()}>{shortId(s.student_id)}</Link>
                          ) : (
                            shortId(s.student_id)
                          )}
                        </td>
                        <td className="muted">{s.assignment_title || "—"}</td>
                        <td>
                          <span className={`pill pill-${s.review_status}`}>
                            {STATUS_LABEL[s.review_status] || s.review_status}
                          </span>
                        </td>
                        <td className="num">{s.finding_count}</td>
                        <td className="right link-cell">{canOpen && <span className="link-text">View findings →</span>}</td>
                      </tr>
                    );
                  })}
                  {visible.length === 0 && (
                    <tr>
                      <td colSpan={5} className="empty-row">
                        No students match.{" "}
                        <button type="button" className="btn-link-inline" onClick={() => { setQuery(""); setFilter("all"); }}>
                          Clear filters
                        </button>
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </>
        )}
        <p className="small muted">Student IDs are anonymous. Essay text is never stored or shown.</p>
      </Status>
    </>
  );
}

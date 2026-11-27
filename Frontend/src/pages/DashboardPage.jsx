import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, formatDate, plural } from "../api.js";
import { notificationTarget } from "../components/Layout.jsx";
import { EmptyState, StatCard, Status, classPath } from "../components/ui.jsx";
import { useAuth } from "../context/AuthContext.jsx";

// Home: GET /dashboard (one card per class) and GET /notifications (what needs attention now).
export default function DashboardPage() {
  const { user, isAdmin } = useAuth();
  const [data, setData] = useState(null);
  const [notes, setNotes] = useState([]);
  const [error, setError] = useState(null);

  useEffect(() => {
    let active = true;
    api
      .dashboard()
      .then((d) => active && setData(d))
      .catch((e) => active && setError(e));
    api
      .notifications()
      .then((n) => active && setNotes(n || []))
      .catch(() => active && setNotes([]));
    return () => {
      active = false;
    };
  }, []);

  const name = data?.user?.display_name || user?.name;
  const t = data?.totals;

  return (
    <div className="page-wrap">
      <header className="page-head">
        <h1>{name ? `Welcome, ${name}` : "Welcome"}</h1>
        <p className="muted">
          {isAdmin
            ? "Every class in the school. As an admin you can open any class or student."
            : "What to reteach, and to whom, across your classes."}
        </p>
      </header>

      <Status loading={!data && !error} error={error}>
        {data && (
          <>
            <section className="stat-row" aria-label="Totals">
              <StatCard label="Classes" value={t.classrooms} />
              <StatCard label="Students" value={t.students} />
              <StatCard
                label="Students needing attention"
                value={t.students_needing_attention}
                hint="Developing or Beginning on 2 of their last 3 pieces of work"
                tone={t.students_needing_attention ? "warn" : undefined}
              />
              <StatCard label="Failed reviews" value={t.failed_reviews} tone={t.failed_reviews ? "bad" : undefined} />
            </section>

            {notes.length > 0 && (
              <section className="card attention-list" aria-labelledby="attn-title">
                <h2 id="attn-title" className="section-title">Needs your attention</h2>
                <ul>
                  {notes.map((n) => (
                    <li key={n.id} className={`kind-${n.kind}`}>
                      <span className="bell-dot" aria-hidden="true" />
                      <span>{n.message}</span>
                      <Link to={notificationTarget(n)} className="btn btn-sm">
                        Open
                      </Link>
                    </li>
                  ))}
                </ul>
              </section>
            )}

            <section aria-labelledby="classes-title">
              <h2 id="classes-title" className="section-title">Classes</h2>
              {data.classrooms.length === 0 ? (
                <EmptyState title="No classes yet">
                  {isAdmin ? "No classrooms have been synced from the LMS." : "No classes are assigned to your account."}
                </EmptyState>
              ) : (
                <div className="class-grid">
                  {data.classrooms.map((c) => (
                    <Link key={c.classroom_id} to={classPath(c.classroom_id)} className="class-card">
                      <div className="class-card-top">
                        <h3>{c.name}</h3>
                        <span className="muted small">Grade {c.grade_level}</span>
                      </div>
                      <dl className="class-card-stats">
                        <div>
                          <dt>Students</dt>
                          <dd>{c.student_count}</dd>
                        </div>
                        <div className={c.students_needing_attention ? "warn" : ""}>
                          <dt>Need attention</dt>
                          <dd>{c.students_needing_attention}</dd>
                        </div>
                        <div className={c.failed_reviews ? "bad" : ""}>
                          <dt>Failed reviews</dt>
                          <dd>{c.failed_reviews}</dd>
                        </div>
                      </dl>
                      <p className="small muted class-card-foot">
                        {c.last_reviewed_at ? `Last reviewed ${formatDate(c.last_reviewed_at)}` : "Not reviewed yet"}
                        {c.analysis_as_of && ` · analysis as of ${formatDate(c.analysis_as_of)}`}
                      </p>
                      <span className="class-card-open">
                        {c.students_needing_attention
                          ? `See who needs help (${plural(c.students_needing_attention, "student")}) →`
                          : "Open class →"}
                      </span>
                    </Link>
                  ))}
                </div>
              )}
            </section>
          </>
        )}
      </Status>
    </div>
  );
}
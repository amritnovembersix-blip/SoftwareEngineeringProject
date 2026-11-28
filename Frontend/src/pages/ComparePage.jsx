import { useEffect, useState } from "react";
import { api } from "../api.js";
import { PairedWeeklyBars } from "../components/charts.jsx";
import { EmptyState, Status } from "../components/ui.jsx";

const STATUS_ROWS = [
  ["needs_attention", "Needs attention"],
  ["emerging", "Emerging"],
  ["secure", "Secure"],
  ["too_little_evidence", "Too little evidence"],
];
const TREND_ROWS = [
  ["improving", "Improving"],
  ["steady", "Steady"],
  ["declining", "Declining"],
  ["unclear", "Unclear"],
];

// GET /comparison?classrooms=a,b: two classes side by side (e.g. Period 1 vs Period 2).
// Counts only; each class reports its size so the reader can judge them fairly.
export default function ComparePage() {
  const [classes, setClasses] = useState(null);
  const [a, setA] = useState("");
  const [b, setB] = useState("");
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    api
      .classrooms()
      .then((rows) => {
        setClasses(rows);
        if (rows.length >= 2) {
          setA(rows[0].id);
          setB(rows[1].id);
        }
      })
      .catch(setError);
  }, []);

  useEffect(() => {
    if (!a || !b || a === b) {
      setData(null);
      return undefined;
    }
    let active = true;
    setLoading(true);
    setError(null);
    api
      .comparison(a, b)
      .then((d) => active && setData(d))
      .catch((e) => active && setError(e))
      .finally(() => active && setLoading(false));
    return () => {
      active = false;
    };
  }, [a, b]);

  const [left, right] = data?.classrooms || [];
  const weeks = data
    ? [...new Set(data.classrooms.flatMap((c) => c.weekly.map((w) => w.week_start)))].sort()
    : [];
  const series = (key) =>
    (data?.classrooms || []).map((c) => ({
      name: c.name,
      values: Object.fromEntries(c.weekly.map((w) => [w.week_start, w[key]])),
    }));

  return (
    <div className="page-wrap">
      <header className="page-head">
        <h1>Compare classes</h1>
        <p className="muted">Same course, different periods or teachers. Counts of students and skill results, never scores.</p>
      </header>

      <Status loading={!classes && !error} error={!classes ? error : null}>
        {classes && classes.length < 2 && (
          <EmptyState title="You need two classes to compare">Only one class is available on your account.</EmptyState>
        )}
        {classes && classes.length >= 2 && (
          <>
            <div className="toolbar card">
              <label className="field-inline">
                Class A
                <select value={a} onChange={(e) => setA(e.target.value)}>
                  {classes.map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.name}
                    </option>
                  ))}
                </select>
              </label>
              <span className="muted">vs</span>
              <label className="field-inline">
                Class B
                <select value={b} onChange={(e) => setB(e.target.value)}>
                  {classes.map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.name}
                    </option>
                  ))}
                </select>
              </label>
            </div>

            {a === b && <p className="notice">Pick two different classes.</p>}
            {loading && <p className="notice">Loading…</p>}
            {error && classes && (
              <p className="notice error" role="alert">
                {error.message}
              </p>
            )}

            {left && right && (
              <div className="stack-lg">
                <section className="card">
                  <div className="table-box">
                    <table className="compare-table">
                      <thead>
                        <tr>
                          <th />
                          <th className="num">{left.name}</th>
                          <th className="num">{right.name}</th>
                        </tr>
                      </thead>
                      <tbody>
                        <tr>
                          <th scope="row">Students</th>
                          <td className="num">{left.student_count}</td>
                          <td className="num">{right.student_count}</td>
                        </tr>
                        <tr>
                          <th scope="row">Students needing attention</th>
                          <td className="num strong">{left.students_needing_attention}</td>
                          <td className="num strong">{right.students_needing_attention}</td>
                        </tr>
                        <tr className="group-row">
                          <th colSpan={3}>Student-skill statuses</th>
                        </tr>
                        {STATUS_ROWS.map(([k, label]) => (
                          <tr key={k}>
                            <th scope="row">{label}</th>
                            <td className="num">{left.skill_statuses[k]}</td>
                            <td className="num">{right.skill_statuses[k]}</td>
                          </tr>
                        ))}
                        <tr className="group-row">
                          <th colSpan={3}>Student-skill trends</th>
                        </tr>
                        {TREND_ROWS.map(([k, label]) => (
                          <tr key={k}>
                            <th scope="row">{label}</th>
                            <td className="num">{left.skill_trends[k]}</td>
                            <td className="num">{right.skill_trends[k]}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </section>

                <section className="card">
                  <h2 className="section-title">Student-skill results needing support, by week</h2>
                  <PairedWeeklyBars weeks={weeks} series={series("needing_support")} valueLabel="Needing support" />
                </section>
                <section className="card">
                  <h2 className="section-title">Student-skill results meeting the standard, by week</h2>
                  <PairedWeeklyBars weeks={weeks} series={series("meets")} valueLabel="Meets" />
                </section>
              </div>
            )}
          </>
        )}
      </Status>
    </div>
  );
}
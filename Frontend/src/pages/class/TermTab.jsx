import { useEffect, useMemo, useState } from "react";
import { api, formatWeek } from "../../api.js";
import { WeeklyStackedBars } from "../../components/charts.jsx";
import { CodeTag, EmptyState, Status } from "../../components/ui.jsx";

const MAX_ROWS = 30;

/** Darker = more students needing support that week, out of those with evidence. */
function shade(cell) {
  if (!cell) return "none";
  const assessed = cell.meets + cell.approaching + cell.developing + cell.beginning;
  if (!assessed) return "none";
  const share = cell.needing_support / assessed;
  if (share === 0) return "0";
  if (share < 0.25) return "1";
  if (share < 0.5) return "2";
  return "3";
}

// GET /classrooms/{id}/concept-weeks: skills x weeks heatmap, and for one skill, stacked bars
// of how many students were at each level each week ("concept over the term").
export default function TermTab({ ctx }) {
  const { classroomId } = ctx;
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [strand, setStrand] = useState("");
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState(null);
  const [showAll, setShowAll] = useState(false);

  useEffect(() => {
    let active = true;
    setData(null);
    api
      .conceptWeeks(classroomId)
      .then((d) => {
        if (!active) return;
        setData(d);
        setSelected(d.skills[0]?.concept_code || null);
      })
      .catch((e) => active && setError(e));
    return () => {
      active = false;
    };
  }, [classroomId]);

  const strands = useMemo(
    () => (data ? [...new Set(data.skills.map((s) => s.strand).filter(Boolean))].sort() : []),
    [data]
  );

  const rows = useMemo(() => {
    if (!data) return [];
    const q = query.trim().toLowerCase();
    return data.skills.filter(
      (s) =>
        (!strand || s.strand === strand) &&
        (!q || s.concept_code.toLowerCase().includes(q) || (s.concept_name || "").toLowerCase().includes(q))
    );
  }, [data, strand, query]);

  const shown = showAll ? rows : rows.slice(0, MAX_ROWS);
  const current = data?.skills.find((s) => s.concept_code === selected);

  return (
    <Status loading={!data && !error} error={error}>
      {data && data.skills.length === 0 && (
        <EmptyState title="No weekly data yet">This fills in once the class analysis has run.</EmptyState>
      )}
      {data && data.skills.length > 0 && (
        <div className="stack-lg">
          {current && (
            <section className="card" aria-labelledby="term-title">
              <h2 id="term-title" className="section-title">
                <CodeTag>{current.concept_code}</CodeTag> {current.concept_name}{" "}
                <span className="muted small">over the term</span>
              </h2>
              <p className="small muted">How many students were at each level each week. Pick another skill in the table below.</p>
              <WeeklyStackedBars weeks={data.weeks} cells={current.cells} legend={data.legend} />
            </section>
          )}

          <section className="card" aria-labelledby="heat-title">
            <h2 id="heat-title" className="section-title">Skills by week</h2>
            <div className="toolbar">
              <label className="search">
                <span className="visually-hidden">Search skills</span>
                <input
                  type="search"
                  placeholder="Search by code or name"
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                />
              </label>
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
              <span className="small muted">Each cell: students needing support that week (Developing or Beginning)</span>
            </div>

            <div className="grid-scroll">
              <table className="heat">
                <thead>
                  <tr>
                    <th>Skill</th>
                    {data.weeks.map((w) => (
                      <th key={w} className="num">
                        {formatWeek(w)}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {shown.map((s) => (
                    <tr
                      key={s.concept_code}
                      className={s.concept_code === selected ? "selected" : ""}
                      onClick={() => setSelected(s.concept_code)}
                    >
                      <th scope="row">
                        <button
                          type="button"
                          className="btn-link-inline"
                          aria-pressed={s.concept_code === selected}
                          onClick={() => setSelected(s.concept_code)}
                        >
                          <CodeTag>{s.concept_code}</CodeTag>
                        </button>{" "}
                        <span className="small">{s.concept_name}</span>
                      </th>
                      {s.cells.map((c, i) => (
                        <td
                          key={data.weeks[i]}
                          className={`heat-cell heat-${shade(c)}`}
                          title={
                            c
                              ? `${formatWeek(data.weeks[i])}: ${c.needing_support} needing support, ${c.meets} meets, ${c.approaching} approaching, ${c.too_little_evidence} too little evidence`
                              : "No data"
                          }
                        >
                          {c ? c.needing_support : ""}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {rows.length > MAX_ROWS && (
              <button type="button" className="btn btn-sm" onClick={() => setShowAll((v) => !v)}>
                {showAll ? `Show the first ${MAX_ROWS}` : `Show all ${rows.length} skills`}
              </button>
            )}
            <p className="small muted">
              Darker cells mean a larger share of the students with evidence needed support. Skills needing the most
              support in the latest week come first.
            </p>
          </section>
        </div>
      )}
    </Status>
  );
}
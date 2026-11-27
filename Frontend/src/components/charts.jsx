/**
 * Small SVG charts with no chart library: a sparkline of levels, stacked bars of how many
 * students are at each level per week, and paired bars for comparing two classes.
 *
 * Levels: 0 Meets, 1 Approaching, 2 Developing, 3 Beginning. Higher on the chart = better,
 * so Meets is at the top. Every chart also has a text description for screen readers.
 */
import { formatWeek } from "../api.js";

export const LEVEL_KEYS = ["meets", "approaching", "developing", "beginning"];
export const LEVEL_COLORS = {
  meets: "var(--lvl0)",
  approaching: "var(--lvl1)",
  developing: "var(--lvl2)",
  beginning: "var(--lvl3)",
  too_little_evidence: "var(--lvl-none)",
};

/** points = [{level, level_label, assignment_title}], oldest first. */
export function Sparkline({ points, width = 120, height = 32 }) {
  if (!points || points.length === 0) return <span className="muted small">No data</span>;
  const pad = 4;
  const step = points.length > 1 ? (width - pad * 2) / (points.length - 1) : 0;
  const y = (level) => pad + (level / 3) * (height - pad * 2);
  const xy = points.map((p, i) => [pad + i * step, y(p.level)]);
  const path = xy.map(([x, yy], i) => `${i ? "L" : "M"}${x.toFixed(1)},${yy.toFixed(1)}`).join(" ");
  const summary = points.map((p) => p.level_label || p.level).join(", ");
  return (
    <svg
      className="sparkline"
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      role="img"
      aria-label={`Levels, oldest first: ${summary}`}
    >
      <line x1={pad} x2={width - pad} y1={y(0)} y2={y(0)} className="spark-meets" />
      {points.length > 1 && <path d={path} className="spark-line" />}
      {xy.map(([x, yy], i) => (
        <circle key={i} cx={x} cy={yy} r={2.6} className={`spark-dot lvl-fill-${points[i].level}`}>
          <title>
            {points[i].assignment_title ? `${points[i].assignment_title}: ` : ""}
            {points[i].level_label || points[i].level}
          </title>
        </circle>
      ))}
    </svg>
  );
}

/**
 * One stacked bar per week: how many students were at each level, plus too little evidence.
 * cells = [{week_start, meets, approaching, developing, beginning, too_little_evidence} | null]
 */
export function WeeklyStackedBars({ weeks, cells, legend, height = 180 }) {
  const keys = [...LEVEL_KEYS, "too_little_evidence"];
  const totals = cells.map((c) => (c ? keys.reduce((s, k) => s + (c[k] || 0), 0) : 0));
  const max = Math.max(1, ...totals);
  const barW = 34;
  const gap = 18;
  const left = 28;
  const top = 8;
  const bottom = 34;
  const width = left + weeks.length * (barW + gap);
  const plotH = height - top - bottom;
  const desc = weeks
    .map((w, i) => {
      const c = cells[i];
      if (!c) return `${formatWeek(w)}: no data`;
      return `${formatWeek(w)}: ${keys.map((k) => `${c[k] || 0} ${legend?.[k] || k}`).join(", ")}`;
    })
    .join("; ");

  return (
    <figure className="chart">
      <svg
        width="100%"
        viewBox={`0 0 ${width} ${height}`}
        role="img"
        aria-label={`Students at each level by week. ${desc}`}
        preserveAspectRatio="xMinYMin meet"
        style={{ maxWidth: width * 1.4 }}
      >
        {[0, 0.5, 1].map((f) => (
          <g key={f}>
            <line x1={left - 4} x2={width} y1={top + plotH * (1 - f)} y2={top + plotH * (1 - f)} className="grid" />
            <text x={left - 8} y={top + plotH * (1 - f) + 4} className="axis" textAnchor="end">
              {Math.round(max * f)}
            </text>
          </g>
        ))}
        {weeks.map((w, i) => {
          const c = cells[i];
          const x = left + i * (barW + gap) + gap / 2;
          let yCursor = top + plotH;
          return (
            <g key={w}>
              {c &&
                keys.map((k) => {
                  const n = c[k] || 0;
                  if (!n) return null;
                  const h = (n / max) * plotH;
                  yCursor -= h;
                  return (
                    <rect key={k} x={x} y={yCursor} width={barW} height={Math.max(h - 1, 0.5)} fill={LEVEL_COLORS[k]} rx={2}>
                      <title>{`${formatWeek(w)}: ${n} ${legend?.[k] || k}`}</title>
                    </rect>
                  );
                })}
              <text x={x + barW / 2} y={height - 14} className="axis" textAnchor="middle">
                {formatWeek(w)}
              </text>
            </g>
          );
        })}
      </svg>
      <Legend legend={legend} keys={keys} />
    </figure>
  );
}

export function Legend({ legend, keys = [...LEVEL_KEYS, "too_little_evidence"] }) {
  return (
    <figcaption className="legend">
      {keys.map((k) => (
        <span key={k} className="legend-item">
          <span className="legend-swatch" style={{ background: LEVEL_COLORS[k] }} aria-hidden="true" />
          {legend?.[k] || k}
        </span>
      ))}
    </figcaption>
  );
}

/**
 * Two series over the same weeks as side-by-side bars (e.g. Period 1 vs Period 2).
 * series = [{name, values: {week: number}}]
 */
export function PairedWeeklyBars({ weeks, series, height = 170, valueLabel }) {
  const max = Math.max(1, ...series.flatMap((s) => weeks.map((w) => s.values[w] || 0)));
  const barW = 14;
  const groupW = barW * 2 + 22;
  const left = 30;
  const top = 8;
  const bottom = 30;
  const width = left + weeks.length * groupW;
  const plotH = height - top - bottom;
  return (
    <figure className="chart">
      <svg
        width="100%"
        viewBox={`0 0 ${width} ${height}`}
        role="img"
        aria-label={`${valueLabel} per week. ${series
          .map((s) => `${s.name}: ${weeks.map((w) => s.values[w] || 0).join(", ")}`)
          .join(". ")}`}
        style={{ maxWidth: width * 1.5 }}
      >
        {[0, 0.5, 1].map((f) => (
          <g key={f}>
            <line x1={left - 4} x2={width} y1={top + plotH * (1 - f)} y2={top + plotH * (1 - f)} className="grid" />
            <text x={left - 8} y={top + plotH * (1 - f) + 4} className="axis" textAnchor="end">
              {Math.round(max * f)}
            </text>
          </g>
        ))}
        {weeks.map((w, i) => (
          <g key={w}>
            {series.map((s, j) => {
              const n = s.values[w] || 0;
              const h = (n / max) * plotH;
              const x = left + i * groupW + 8 + j * barW;
              return (
                <rect key={s.name} x={x} y={top + plotH - h} width={barW - 2} height={Math.max(h, 0.5)} className={`series-${j}`} rx={2}>
                  <title>{`${s.name}, ${formatWeek(w)}: ${n}`}</title>
                </rect>
              );
            })}
            <text x={left + i * groupW + 8 + barW} y={height - 10} className="axis" textAnchor="middle">
              {formatWeek(w)}
            </text>
          </g>
        ))}
      </svg>
      <figcaption className="legend">
        {series.map((s, j) => (
          <span key={s.name} className="legend-item">
            <span className={`legend-swatch series-${j}`} aria-hidden="true" />
            {s.name}
          </span>
        ))}
      </figcaption>
    </figure>
  );
}
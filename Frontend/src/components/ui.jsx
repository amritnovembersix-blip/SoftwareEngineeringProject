/**
 * Small pieces used on many pages: loading/error states, status and level chips,
 * confidence badges, trend arrows, tabs and stat cards.
 *
 * Rules from the handoff: no scores or percentages for students; show levels
 * (Meets, Approaching, Developing, Beginning) and counts. Words come from the backend's
 * *_label fields (which read Justin's display_label table); the fallbacks here are only
 * used when a label is missing.
 */
import { Link, NavLink } from "react-router-dom";
import { shortId } from "../api.js";

// ---------- loading / errors ----------

export function Status({ loading, error, children }) {
  if (error) {
    if (error.status === 404 && error.pendingEndpoint) {
      return <PendingBackend endpoint={error.pendingEndpoint} />;
    }
    return (
      <p className="notice error" role="alert">
        {error.message || String(error)}
      </p>
    );
  }
  if (loading) {
    return (
      <p className="notice" role="status">
        <span className="spinner" aria-hidden="true" /> Loading…
      </p>
    );
  }
  return children;
}

/** Shown when a page needs an endpoint the backend doesn't have yet. */
export function PendingBackend({ endpoint, children }) {
  return (
    <div className="pending-backend" role="status">
      <strong>Waiting on the backend.</strong>{" "}
      This screen is built, but the backend doesn't have <code>{endpoint}</code> yet.
      {children && <div className="small muted">{children}</div>}
    </div>
  );
}

export function EmptyState({ title, children }) {
  return (
    <div className="empty-state">
      <strong>{title}</strong>
      {children && <p className="muted">{children}</p>}
    </div>
  );
}

// ---------- chips and badges ----------

const LEVEL_FALLBACK = ["Meets", "Approaching", "Developing", "Beginning"];

/** Level 0-3 (0 Meets ... 3 Beginning). */
export function LevelChip({ level, label }) {
  if (level === null || level === undefined) {
    return <span className="lvl lvl-none">—</span>;
  }
  return <span className={`lvl lvl-${level}`}>{label || LEVEL_FALLBACK[level] || level}</span>;
}

const SUPPORT_TO_LEVEL = { approaching: 1, developing: 2, beginning: 3 };

export function SupportChip({ supportLevel, label }) {
  if (!supportLevel) return null;
  return <LevelChip level={SUPPORT_TO_LEVEL[supportLevel]} label={label} />;
}

const STATUS_FALLBACK = {
  needs_attention: "Needs attention",
  emerging: "Emerging",
  too_little_evidence: "Too little evidence",
  secure: "Secure",
};

export function ConceptStatus({ status, label }) {
  if (!status) return <span className="muted">—</span>;
  return <span className={`cstatus cstatus-${status}`}>{label || STATUS_FALLBACK[status] || status}</span>;
}

/** Confidence is shown as a word (High / Medium / Low), never a percentage. */
export function ConfidenceBadge({ label }) {
  if (!label) return null;
  return (
    <span className={`conf conf-${label.toLowerCase()}`} title="How sure the AI reviewer is about this">
      {label} confidence
    </span>
  );
}

const TREND = {
  improving: { icon: "↗", text: "Improving" },
  steady: { icon: "→", text: "Steady" },
  declining: { icon: "↘", text: "Declining" },
  unclear: { icon: "·", text: "Unclear" },
};

export function TrendArrow({ trend, showText = true }) {
  const t = TREND[trend];
  if (!t) return <span className="muted">—</span>;
  return (
    <span className={`trend trend-${trend}`} title={t.text}>
      <span aria-hidden="true">{t.icon}</span>
      {showText ? ` ${t.text}` : <span className="visually-hidden">{t.text}</span>}
    </span>
  );
}

export function CodeTag({ children, title }) {
  return (
    <span className="code-tag" title={title}>
      {children}
    </span>
  );
}

// ---------- layout pieces ----------

export function StatCard({ label, value, hint, tone }) {
  return (
    <div className={`stat-card ${tone ? `stat-${tone}` : ""}`}>
      <span className="stat-label">{label}</span>
      <strong className="stat-value">{value ?? "—"}</strong>
      {hint && <span className="stat-hint">{hint}</span>}
    </div>
  );
}

/** Tabs that are links, so each tab has its own URL. tabs = [{to, label, count, end}] */
export function TabNav({ tabs, label }) {
  return (
    <nav className="tabnav" aria-label={label}>
      {tabs.map((t) => (
        <NavLink
          key={t.to}
          to={t.to}
          end={t.end}
          className={({ isActive }) => `tab ${isActive ? "active" : ""}`}
        >
          {t.label}
          {t.count ? <span className="tab-count">{t.count}</span> : null}
        </NavLink>
      ))}
    </nav>
  );
}

/** Buttons that act like tabs within a page (no URL change). */
export function Segmented({ options, value, onChange, label }) {
  return (
    <div className="segmented" role="group" aria-label={label}>
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          aria-pressed={value === o.value}
          className={value === o.value ? "active" : ""}
          onClick={() => onChange(o.value)}
        >
          {o.label}
          {o.count !== undefined && <span className="chip-count">{o.count}</span>}
        </button>
      ))}
    </div>
  );
}

export const studentPath = (classroomId, studentId) =>
  `/classrooms/${encodeURIComponent(classroomId)}/students/${encodeURIComponent(studentId)}`;

export const classPath = (classroomId, tab = "") =>
  `/classrooms/${encodeURIComponent(classroomId)}${tab ? `/${tab}` : ""}`;

export function StudentLink({ classroomId, studentId }) {
  return (
    <Link className="mono student-link" to={studentPath(classroomId, studentId)} title={studentId}>
      {shortId(studentId)}
    </Link>
  );
}
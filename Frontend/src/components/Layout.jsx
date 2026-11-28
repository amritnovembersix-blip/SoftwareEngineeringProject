import { useEffect, useRef, useState } from "react";
import { Link, NavLink, useLocation, useNavigate } from "react-router-dom";
import { api } from "../api.js";
import { useAuth } from "../context/AuthContext.jsx";
import { classPath } from "./ui.jsx";

export { Status } from "./ui.jsx";

const SEEN_KEY = "sass-seen-notifications";

function loadSeen() {
  try {
    return new Set(JSON.parse(localStorage.getItem(SEEN_KEY) || "[]"));
  } catch {
    return new Set();
  }
}

function saveSeen(ids) {
  try {
    localStorage.setItem(SEEN_KEY, JSON.stringify([...ids]));
  } catch {
    /* storage unavailable: the badge just shows again next visit */
  }
}

/** Where a notification takes the teacher. */
export function notificationTarget(n) {
  if (n.kind === "failed_reviews") return classPath(n.classroom_id, "students");
  return classPath(n.classroom_id);
}

/**
 * The bell. The server has no read/unread state yet, so "seen" is remembered in this
 * browser by notification id. A seen notification whose count changes gets a new key.
 */
export function NotificationBell() {
  const [items, setItems] = useState(null);
  const [open, setOpen] = useState(false);
  const [seen, setSeen] = useState(loadSeen);
  const box = useRef(null);
  const location = useLocation();

  useEffect(() => {
    let active = true;
    api
      .notifications()
      .then((rows) => active && setItems(rows))
      .catch(() => active && setItems([]));
    return () => {
      active = false;
    };
  }, [location.pathname]);

  useEffect(() => {
    if (!open) return undefined;
    const onDown = (e) => {
      if (box.current && !box.current.contains(e.target)) setOpen(false);
    };
    const onKey = (e) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const key = (n) => `${n.id}:${n.count}`;
  const unseen = (items || []).filter((n) => !seen.has(key(n))).length;

  function toggle() {
    const next = !open;
    setOpen(next);
    if (next && items?.length) {
      const all = new Set([...seen, ...items.map(key)]);
      setSeen(all);
      saveSeen(all);
    }
  }

  return (
    <div className="bell" ref={box}>
      <button
        type="button"
        className="bell-button"
        aria-expanded={open}
        aria-label={unseen ? `Notifications, ${unseen} new` : "Notifications"}
        onClick={toggle}
      >
        <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true">
          <path
            d="M12 3a6 6 0 0 0-6 6v3.6L4.3 16a1 1 0 0 0 .9 1.5h13.6a1 1 0 0 0 .9-1.5L18 12.6V9a6 6 0 0 0-6-6Zm-2.5 16a2.5 2.5 0 0 0 5 0"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.7"
            strokeLinejoin="round"
          />
        </svg>
        {unseen > 0 && <span className="bell-badge">{unseen}</span>}
      </button>
      {open && (
        <div className="bell-panel" role="dialog" aria-label="Notifications">
          <div className="bell-head">Needs your attention</div>
          {!items && <p className="small muted bell-empty">Loading…</p>}
          {items && items.length === 0 && <p className="small muted bell-empty">Nothing needs attention right now.</p>}
          {items &&
            items.map((n) => (
              <Link key={n.id} to={notificationTarget(n)} className={`bell-item kind-${n.kind}`} onClick={() => setOpen(false)}>
                <span className="bell-dot" aria-hidden="true" />
                <span>{n.message}</span>
              </Link>
            ))}
        </div>
      )}
    </div>
  );
}

function SideLink({ to, children, end }) {
  return (
    <NavLink to={to} end={end} className={({ isActive }) => `side-link ${isActive ? "active" : ""}`}>
      {children}
    </NavLink>
  );
}

export default function Layout({ children }) {
  const navigate = useNavigate();
  const { user, isAdmin, logout } = useAuth();
  const [classes, setClasses] = useState([]);
  const [menuOpen, setMenuOpen] = useState(false);
  const location = useLocation();

  useEffect(() => {
    let active = true;
    api
      .classrooms()
      .then((rows) => active && setClasses(rows || []))
      .catch(() => active && setClasses([]));
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => setMenuOpen(false), [location.pathname]);

  const displayName = user?.name || user?.email || "Signed in";
  const roleLabel = isAdmin ? "Admin" : "Teacher";

  function logOut() {
    logout();
    navigate("/login", { replace: true });
  }

  return (
    <div className={`shell ${menuOpen ? "menu-open" : ""}`}>
      <aside className="side" aria-label="Main navigation">
        <Link to="/" className="side-brand">
          SASS
          <span>Student Academic Support</span>
        </Link>

        <nav className="side-nav">
          <SideLink to="/" end>
            Home
          </SideLink>

          <div className="side-group">{isAdmin ? "All classes" : "My classes"}</div>
          {classes.length === 0 && <span className="side-empty">No classes yet</span>}
          {classes.map((c) => (
            <SideLink key={c.id} to={classPath(c.id)}>
              {c.name}
            </SideLink>
          ))}

          {classes.length >= 2 && (
            <>
              <div className="side-group">Analysis</div>
              <SideLink to="/compare">Compare classes</SideLink>
            </>
          )}
        </nav>

        <p className="side-foot">Student IDs are anonymous. Essay text is never stored.</p>
      </aside>

      <div className="main">
        <header className="topbar2">
          <button type="button" className="menu-toggle" aria-label="Open menu" onClick={() => setMenuOpen((o) => !o)}>
            ☰
          </button>
          <span className="topbar-spacer" />
          <NotificationBell />
          <div className="user-chip">
            <span className="avatar" aria-hidden="true">
              {displayName.trim().charAt(0).toUpperCase()}
            </span>
            <span className="user-text">
              <strong>{displayName}</strong>
              <span>{roleLabel}</span>
            </span>
          </div>
          <button type="button" className="btn btn-sm" onClick={logOut}>
            Log out
          </button>
        </header>
        <main className="content">{children}</main>
      </div>
    </div>
  );
}

import { useEffect, useState } from "react";
import {
  Link,
  useLocation,
  useMatch,
  useNavigate,
} from "react-router-dom";
import { useAuth } from "../context/AuthContext.jsx";
import { api } from "../api.js";

export default function Layout({ children }) {
  const navigate = useNavigate();
  const location = useLocation();
  const { user, logout } = useAuth();

  const findingsMatch = useMatch(
    "/classrooms/:classroomId/students/:studentId"
  );

  const classroomMatch = useMatch("/classrooms/:classroomId");

  const classroomId =
    findingsMatch?.params.classroomId ||
    classroomMatch?.params.classroomId ||
    null;

  const studentId = findingsMatch?.params.studentId || null;

  const [firstClassroomId, setFirstClassroomId] = useState(null);

  useEffect(() => {
    if (classroomId) return undefined;

    let cancelled = false;

    api.classrooms()
      .then((classrooms) => {
        if (!cancelled) {
          setFirstClassroomId(classrooms?.[0]?.id ?? null);
        }
      })
      .catch(() => {
        if (!cancelled) setFirstClassroomId(null);
      });

    return () => {
      cancelled = true;
    };
  }, [classroomId]);

  const displayName =
    user?.name ||
    user?.display_name ||
    user?.email ||
    "Teacher";

  const role = (user?.role || "teacher").toLowerCase();

  const roleLabel =
    role.charAt(0).toUpperCase() + role.slice(1);

  const avatarLetter =
    displayName.trim().charAt(0).toUpperCase() || "T";

  const targetClassroomId = classroomId || firstClassroomId;

  const studentsHref = targetClassroomId
    ? `/classrooms/${encodeURIComponent(targetClassroomId)}`
    : "/";

  const findingsHref = studentId
    ? `/classrooms/${encodeURIComponent(classroomId)}/students/${encodeURIComponent(studentId)}`
    : studentsHref;

  const isClassrooms =
    location.pathname === "/" ||
    location.pathname === "/dev/classrooms";

  const isStudents = Boolean(classroomId && !studentId);
  const isFindings = Boolean(studentId);

  function navClass(active) {
    return `sidebar-link${active ? " active" : ""}`;
  }

  function logOut() {
    logout();
    navigate("/login", { replace: true });
  }

  return (
    <div className="app">
      <header className="topbar">
        <div className="topbar-inner">
          <Link to="/" className="brand">
            SASS
          </Link>

          <span className="tagline">
            Essay review against the Missouri Learning Standards
          </span>

          {user && (
            <div className="topbar-user">
              <span>
                {displayName}
                <span className="topbar-role">({role})</span>
              </span>

              
            </div>
          )}
        </div>
      </header>

      <div className="workspace-shell">
        <aside className="teacher-sidebar">
          <div className="sidebar-user">
            <div className="sidebar-avatar">
              {avatarLetter}
            </div>

            <div className="sidebar-user-info">
              <span className="sidebar-eyebrow">
                TEACHER PORTAL
              </span>

              <strong>{displayName}</strong>

              <span className="sidebar-role">
                {roleLabel}
              </span>
            </div>
          </div>

          <p className="sidebar-nav-label">WORKSPACE</p>

          <nav
            className="teacher-sidebar-nav"
            aria-label="Main navigation"
          >
            <Link
              to="/"
              className={navClass(isClassrooms)}
            >
              <span className="sidebar-icon">▦</span>
              <span>Classrooms</span>
            </Link>

            <Link
              to={studentsHref}
              className={navClass(isStudents)}
            >
              <span className="sidebar-icon">♙</span>
              <span>Students</span>
            </Link>

            <Link
              to={findingsHref}
              className={navClass(isFindings)}
              title={
                studentId
                  ? "View this student's findings"
                  : "Select a student to view findings"
              }
            >
              <span className="sidebar-icon">✧</span>
              <span>Findings</span>
            </Link>
          </nav>
          <button
            type="button"
            className="sidebar-logout"
            onClick={logOut}
          >
            <span className="sidebar-icon">↪</span>
            <span>Log out</span>
            
          </button>
          

          <div className="sidebar-footer">
            Student Academic Support System
          </div>
        </aside>

        <main className="page workspace-page">
          {children}
        </main>
      </div>
    </div>
  );
}

export function Status({ loading, error, children }) {
  if (error) {
    return (
      <p className="notice error" role="alert">
        {error}
      </p>
    );
  }

  if (loading) {
    return <p className="notice">Loading…</p>;
  }

  return children;
}

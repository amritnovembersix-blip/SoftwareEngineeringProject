
import { Link, useNavigate } from "react-router-dom";
import { useAuth } from "../context/AuthContext.jsx";

export default function Layout({ children }) {
  const navigate = useNavigate();
  const { user, logout } = useAuth();

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
                {user.name || user.email}
                <span className="topbar-role">
                  ({user.role})
                </span>
              </span>

              <button
                type="button"
                className="logout-button"
                onClick={logOut}
              >
                Log out
              </button>
            </div>
          )}
        </div>
      </header>

      <main className="page">
        {children}
      </main>
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

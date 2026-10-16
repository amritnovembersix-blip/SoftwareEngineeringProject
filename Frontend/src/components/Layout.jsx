import { Link } from "react-router-dom";

export default function Layout({ children }) {
  return (
    <div className="app">
      <header className="topbar">
        <div className="topbar-inner">
          <Link to="/" className="brand">
            SASS
          </Link>
          <span className="tagline">Essay review against the Missouri Learning Standards</span>
        </div>
      </header>
      <main className="page">{children}</main>
    </div>
  );
}

export function Status({ loading, error, children }) {
  if (error) return <p className="notice error" role="alert">{error}</p>;
  if (loading) return <p className="notice">Loading…</p>;
  return children;
}

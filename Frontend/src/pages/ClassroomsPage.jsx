import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api.js";
import { Status } from "../components/Layout.jsx";

// Screen 1: GET /classrooms
export default function ClassroomsPage() {
  const [classrooms, setClassrooms] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    api.classrooms().then(setClassrooms).catch((e) => setError(e.message));
  }, []);

  return (
    <>
      <div className="heading">
        <h1>Your classrooms</h1>
        <p className="muted">Pick a class to see its students and what the reviewer found in their essays.</p>
      </div>
      <Status loading={!classrooms} error={error}>
        {classrooms && classrooms.length === 0 && (
          <p className="notice">No classrooms yet. Fill the database with Backend/scripts/fill_demo_data.sh.</p>
        )}
        <div className="card-grid">
          {(classrooms || []).map((c) => (
            <Link key={c.id} to={`/classrooms/${encodeURIComponent(c.id)}`} className="card card-link">
              <div className="row-between">
                <span className="pill pill-accent">
                  Grade {c.grade_level} · {c.subject.toUpperCase()}
                </span>
                <span className="mono small muted">{c.id}</span>
              </div>
              <h2 className="card-title">{c.name}</h2>
              <div className="stats">
                <div>
                  <span className="stat-num">{c.student_count}</span>
                  <span className="small muted">students</span>
                </div>
                <div>
                  <span className="stat-num">{c.reviewed_count}</span>
                  <span className="small muted">reviewed</span>
                </div>
              </div>
              <span className="link-text">Open class →</span>
            </Link>
          ))}
        </div>
      </Status>
    </>
  );
}

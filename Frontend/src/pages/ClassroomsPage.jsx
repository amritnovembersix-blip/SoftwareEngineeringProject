
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api.js";
import { Status } from "../components/Layout.jsx";

function isValidNumber(value) {
  return (
    value !== null &&
    value !== undefined &&
    value !== "" &&
    Number.isFinite(Number(value))
  );
}

function displayCount(value) {
  return isValidNumber(value)
    ? Number(value).toLocaleString()
    : "—";
}

function getReviewPercentage(reviewed, total) {
  if (!isValidNumber(reviewed) || !isValidNumber(total)) {
    return null;
  }

  const totalCount = Number(total);

  if (totalCount <= 0) return null;

  const percentage =
    (Number(reviewed) / totalCount) * 100;

  return Math.min(100, Math.round(percentage));
}

export default function ClassroomsPage() {
  const [classrooms, setClassrooms] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    let active = true;

    api.classrooms()
      .then((data) => {
        if (active) setClassrooms(data);
      })
      .catch((e) => {
        if (active) {
          setError(e.message || "Unable to load classrooms.");
        }
      });

    return () => {
      active = false;
    };
  }, []);

  const totalStudents = classrooms?.every(
    (classroom) => isValidNumber(classroom.student_count)
  )
    ? classrooms.reduce(
        (sum, classroom) =>
          sum + Number(classroom.student_count),
        0
      )
    : null;

  return (
    <div className="teacher-dashboard">
      <div className="dashboard-heading">
        <h1>Welcome!</h1>
        <p>
          Here's an overview of your classrooms and students.
        </p>
      </div>

      <Status loading={!classrooms && !error} error={error}>
        {classrooms && (
          <>
            <section className="dashboard-overview">
              <div className="overview-card">
                <div className="overview-icon" aria-hidden="true">
                  <span>♧</span>
                </div>

                <div>
                  <p>My Classrooms</p>
                  <h2>{classrooms.length}</h2>
                </div>
              </div>

              <div className="overview-card">
                <div className="overview-icon" aria-hidden="true">
                  <span>♙</span>
                </div>

                <div>
                  <p>Total Students</p>
                  <h2>{displayCount(totalStudents)}</h2>
                </div>
              </div>
            </section>

            <section
              className="classroom-section"
              id="classroom-overview"
            >
              <div className="section-heading">
                <h2>Classroom Overview</h2>
                <p>
                  Your enrolled classrooms and review progress.
                </p>
              </div>

              {classrooms.length === 0 ? (
                <p className="notice">
                  No classrooms are currently available.
                </p>
              ) : (
                <div className="classroom-dashboard-grid">
                  {classrooms.map((classroom) => {
                    const percentage = getReviewPercentage(
                      classroom.reviewed_count,
                      classroom.student_count
                    );

                    return (
                      <Link
                        key={classroom.id}
                        to={`/classrooms/${encodeURIComponent(
                          classroom.id
                        )}`}
                        className="classroom-dashboard-card"
                      >
                        <div className="classroom-card-header">
                          <div
                            className="classroom-icon"
                            aria-hidden="true"
                          >
                            <span>▤</span>
                          </div>

                          <div className="classroom-title">
                            <h3>{classroom.name}</h3>

                            <span className="classroom-grade">
                              Grade {classroom.grade_level}
                              {classroom.subject
                                ? ` · ${classroom.subject}`
                                : ""}
                            </span>
                          </div>

                          <span
                            className="classroom-arrow"
                            aria-hidden="true"
                          >
                            →
                          </span>
                        </div>

                        <div className="classroom-card-stats">
                          <div className="classroom-stat">
                            <span
                              className="stat-icon"
                              aria-hidden="true"
                            >
                              ♙
                            </span>

                            <div>
                              <strong>
                                {displayCount(
                                  classroom.student_count
                                )}
                              </strong>
                              <span>Students</span>
                            </div>
                          </div>

                          <div className="classroom-stat">
                            <span
                              className="stat-icon"
                              aria-hidden="true"
                            >
                              ✓
                            </span>

                            <div>
                              <strong>
                                {displayCount(
                                  classroom.reviewed_count
                                )}
                              </strong>
                              <span>Reviewed</span>
                            </div>
                          </div>
                        </div>

                        <div className="classroom-progress">
                          <div
                            className="progress-track"
                            role="progressbar"
                            aria-label={`Review progress for ${classroom.name}`}
                            aria-valuemin="0"
                            aria-valuemax="100"
                            aria-valuenow={percentage ?? undefined}
                          >
                            {percentage !== null && (
                              <div
                                className="progress-fill"
                                style={{
                                  width: `${percentage}%`,
                                }}
                              />
                            )}
                          </div>

                          <span className="progress-label">
                            {percentage === null
                              ? "Progress unavailable"
                              : `${percentage}% Reviewed`}
                          </span>
                        </div>

                        <span className="classroom-open-link">
                          View classroom →
                        </span>
                      </Link>
                    );
                  })}
                </div>
              )}
            </section>
          </>
        )}
      </Status>
    </div>
  );
}

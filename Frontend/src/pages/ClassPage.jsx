import { useEffect, useState } from "react";
import { Link, Navigate, Route, Routes, useParams } from "react-router-dom";
import { api } from "../api.js";
import { Status, TabNav, classPath } from "../components/ui.jsx";
import StudentsPage from "./StudentsPage.jsx";
import OverviewTab from "./class/OverviewTab.jsx";
import SkillsGridTab from "./class/SkillsGridTab.jsx";
import TermTab from "./class/TermTab.jsx";

/**
 * Every student's latest status on every skill in this class: one
 * GET /students/{id}/concepts?classroom_id= per enrolled student, in parallel.
 * Used by the class summary and the students x skills grid.
 * (A single class-wide endpoint would be faster; listed in docs/BACKEND_GAPS.md.)
 */
export function useClassConcepts(classroomId, students) {
  const [byStudent, setByStudent] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!students) return undefined;
    let active = true;
    setByStudent(null);
    Promise.allSettled(students.map((s) => api.concepts(s.student_id, classroomId))).then((results) => {
      if (!active) return;
      const failed = results.find((r) => r.status === "rejected");
      if (failed && results.every((r) => r.status === "rejected")) {
        setError(failed.reason);
        return;
      }
      const map = {};
      results.forEach((r, i) => {
        map[students[i].student_id] = r.status === "fulfilled" ? r.value.concepts || [] : [];
      });
      setByStudent(map);
    });
    return () => {
      active = false;
    };
  }, [classroomId, students]);

  return { byStudent, error };
}

export default function ClassPage() {
  const { classroomId } = useParams();
  const [classroom, setClassroom] = useState(null);
  const [students, setStudents] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    let active = true;
    setClassroom(null);
    setStudents(null);
    setError(null);
    Promise.all([api.classrooms(), api.students(classroomId)])
      .then(([rows, studs]) => {
        if (!active) return;
        const c = rows.find((r) => r.id === classroomId);
        if (!c) {
          setError(new Error("This class isn't in your classes, or it doesn't exist."));
          return;
        }
        setClassroom(c);
        setStudents(studs);
      })
      .catch((e) => active && setError(e));
    return () => {
      active = false;
    };
  }, [classroomId]);

  const concepts = useClassConcepts(classroomId, students);
  const ctx = { classroomId, classroom, students, concepts };
  const base = classPath(classroomId);

  return (
    <div className="page-wrap">
      <nav className="crumbs" aria-label="Breadcrumb">
        <Link to="/">Home</Link> <span aria-hidden="true">/</span> <span>{classroom?.name || "Class"}</span>
      </nav>

      <Status loading={!classroom && !error} error={error}>
        {classroom && (
          <>
            <header className="page-head">
              <h1>{classroom.name}</h1>
              <p className="muted">
                Grade {classroom.grade_level}
                {classroom.subject ? ` · ${classroom.subject.toUpperCase()}` : ""} · {classroom.student_count} students ·{" "}
                {classroom.reviewed_count} with reviewed work
              </p>
            </header>

            <TabNav
              label="Class views"
              tabs={[
                { to: base, label: "Summary", end: true },
                { to: `${base}/students`, label: "Students & reviews" },
                { to: `${base}/skills`, label: "Students × skills" },
                { to: `${base}/term`, label: "Over the term" },
              ]}
            />

            <Routes>
              <Route index element={<OverviewTab ctx={ctx} />} />
              <Route path="students" element={<StudentsPage />} />
              <Route path="skills" element={<SkillsGridTab ctx={ctx} />} />
              <Route path="term" element={<TermTab ctx={ctx} />} />
              <Route path="*" element={<Navigate to={base} replace />} />
            </Routes>
          </>
        )}
      </Status>
    </div>
  );
}
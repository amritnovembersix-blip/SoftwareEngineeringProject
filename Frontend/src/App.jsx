import { Navigate, Route, Routes } from "react-router-dom";
import Layout from "./components/Layout.jsx";
import ClassroomsPage from "./pages/ClassroomsPage.jsx";
import StudentsPage from "./pages/StudentsPage.jsx";
import FindingsPage from "./pages/FindingsPage.jsx";

// Classrooms → Students (with the Review class button) → one student's Findings
export default function App() {
  return (
    <Layout>
      <Routes>
        <Route path="/" element={<ClassroomsPage />} />
        <Route path="/classrooms/:classroomId" element={<StudentsPage />} />
        <Route path="/classrooms/:classroomId/students/:studentId" element={<FindingsPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Layout>
  );
}

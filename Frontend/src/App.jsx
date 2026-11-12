
import { Navigate, Route, Routes } from "react-router-dom";
import Layout from "./components/Layout.jsx";
import LoginPage from "./pages/LoginPage.jsx";
import ClassroomsPage from "./pages/ClassroomsPage.jsx";
import StudentsPage from "./pages/StudentsPage.jsx";
import FindingsPage from "./pages/FindingsPage.jsx";

function isSignedIn() {
  return (
    sessionStorage.getItem("sassDemoSignedIn") === "true" ||
    localStorage.getItem("sassDemoSignedIn") === "true"
  );
}

function MainApp() {
  if (!isSignedIn()) {
    return <Navigate to="/login" replace />;
  }

  return (
    <Layout>
      <Routes>
        <Route path="/" element={<ClassroomsPage />} />
        <Route
          path="/classrooms/:classroomId"
          element={<StudentsPage />}
        />
        <Route
          path="/classrooms/:classroomId/students/:studentId"
          element={<FindingsPage />}
        />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Layout>
  );
}

export default function App() {
  return (
    <Routes>
      <Route
        path="/login"
        element={
          isSignedIn() ? <Navigate to="/" replace /> : <LoginPage />
        }
      />
      <Route path="/*" element={<MainApp />} />
    </Routes>
  );
}

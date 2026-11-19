
import { Navigate, Route, Routes } from "react-router-dom";
import Layout from "./components/Layout.jsx";
import LoginPage from "./pages/LoginPage.jsx";
import ClassroomsPage from "./pages/ClassroomsPage.jsx";
import StudentsPage from "./pages/StudentsPage.jsx";
import FindingsPage from "./pages/FindingsPage.jsx";
import { AuthProvider, useAuth } from "./context/AuthContext.jsx";

function MainApp() {
  const { isAuthenticated } = useAuth();

  if (!isAuthenticated) {
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

function AppRoutes() {
  const { isAuthenticated } = useAuth();

  return (
    <Routes>
      {/* Temporary local development preview */}
      <Route
        path="/dev/classrooms"
        element={
          <Layout>
            <ClassroomsPage />
          </Layout>
        }
      />

      <Route
        path="/login"
        element={
          isAuthenticated
            ? <Navigate to="/" replace />
            : <LoginPage />
        }
      />

      <Route path="/*" element={<MainApp />} />
    </Routes>
  );
}

export default function App() {
  return (
    <AuthProvider>
      <AppRoutes />
    </AuthProvider>
  );
}

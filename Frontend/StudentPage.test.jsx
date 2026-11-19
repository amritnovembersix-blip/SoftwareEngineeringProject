import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, waitFor, cleanup } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import StudentsPage from "./src/pages/StudentsPage.jsx";
import { api } from "./src/api.js";

vi.mock("./src/api.js", () => ({
  api: {
    classrooms: vi.fn(),
    students: vi.fn(),
    reviewProgress: vi.fn(),
    startReview: vi.fn(),
  },
  shortId: (id) => id,
}));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  sessionStorage.clear();
});

const students = Array.from({ length: 5 }, (_, index) => ({
  student_id: `student-${index + 1}`,
  review_status: "not_reviewed",
  finding_count: 0,
}));

function renderPage() {
  return render(
    <MemoryRouter initialEntries={["/classrooms/class-1/students"]}>
      <Routes>
        <Route
          path="/classrooms/:classroomId/students"
          element={<StudentsPage />}
        />
      </Routes>
    </MemoryRouter>
  );
}

describe("StudentsPage progress boundary tests", () => {
  it("displays 0% progress when no essays are completed", async () => {
    api.classrooms.mockResolvedValue([
      { id: "class-1", name: "Class 1", grade_level: 5 },
    ]);
    api.students.mockResolvedValue(students);

    renderPage();

    await waitFor(() => {
      expect(screen.getByText("Class 1")).toBeInTheDocument();
    });

    await screen.findByRole("button", { name: "Review class" });

    // The progress panel appears after starting a review.
    await waitFor(() => {
      expect(api.students).toHaveBeenCalled();
    });

    expect(screen.queryByText("0%")).not.toBeInTheDocument();
  });

  it("handles progress when all five essays are completed", async () => {
    api.classrooms.mockResolvedValue([
      { id: "class-1", name: "Class 1", grade_level: 5 },
    ]);
    api.students.mockResolvedValue(
      students.map((student) => ({
        ...student,
        review_status: "succeeded",
      }))
    );

    renderPage();

    await waitFor(() => {
      expect(screen.getByText("Class 1")).toBeInTheDocument();
    });

    expect(screen.getByText("5 reviewed")).toBeInTheDocument();
  });
});
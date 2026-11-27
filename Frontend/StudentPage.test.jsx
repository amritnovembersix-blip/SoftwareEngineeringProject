import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, waitFor, cleanup } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
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
        <Route path="/classrooms/:classroomId/students" element={<StudentsPage />} />
      </Routes>
    </MemoryRouter>
  );
}

describe("StudentsPage progress boundary tests", () => {
  it("displays no progress before a review starts", async () => {
    api.classrooms.mockResolvedValue([{ id: "class-1", name: "Class 1", grade_level: 5 }]);
    api.students.mockResolvedValue(students);

    renderPage();

    await waitFor(() => {
      expect(screen.getByText("Class 1")).toBeInTheDocument();
    });
    await screen.findByRole("button", { name: "Review class" });
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("handles progress when all five essays are completed", async () => {
    api.classrooms.mockResolvedValue([{ id: "class-1", name: "Class 1", grade_level: 5 }]);
    api.students.mockResolvedValue(students.map((s) => ({ ...s, review_status: "succeeded" })));

    renderPage();

    await waitFor(() => {
      expect(screen.getByText("Class 1")).toBeInTheDocument();
    });
    expect(screen.getByText("5 reviewed")).toBeInTheDocument();
  });

  it("shows the backend's message when a review finishes with failures", async () => {
    const user = userEvent.setup();
    api.classrooms.mockResolvedValue([{ id: "class-1", name: "Class 1", grade_level: 5 }]);
    api.students.mockResolvedValue(students);
    api.startReview.mockResolvedValue({ run_id: "run-1", already_running: false });
    api.reviewProgress.mockResolvedValue({
      run_id: "run-1",
      status: "succeeded",
      done: 4,
      total: 5,
      failed: 1,
      not_started: 0,
      message: "4 of 5 done, 1 failed. Click Review class again to retry; reviewed essays are kept.",
    });

    renderPage();
    await user.click(await screen.findByRole("button", { name: "Review class" }));

    expect(await screen.findByText(/4 of 5 done, 1 failed/)).toBeInTheDocument();
    expect(api.startReview).toHaveBeenCalledWith("class-1");
    expect(api.reviewProgress).toHaveBeenCalledWith("run-1");
  });

  it("shows why a review couldn't start", async () => {
    const user = userEvent.setup();
    api.classrooms.mockResolvedValue([{ id: "class-1", name: "Class 1", grade_level: 5 }]);
    api.students.mockResolvedValue(students);
    api.startReview.mockRejectedValue(new Error("the agentic service is not reachable"));

    renderPage();
    await user.click(await screen.findByRole("button", { name: "Review class" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("the agentic service is not reachable");
  });
});
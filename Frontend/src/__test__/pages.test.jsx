import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { api } from "../api.js";
import FindingCard from "../components/FindingCard.jsx";
import { AuthProvider } from "../context/AuthContext.jsx";
import DashboardPage from "../pages/DashboardPage.jsx";

// Keep the real helpers (shortId, formatDate, ApiError ...) and replace only the network calls.
vi.mock("../api.js", async (importOriginal) => {
  const real = await importOriginal();
  const calls = ["me", "dashboard", "notifications", "classrooms"];
  return { ...real, api: Object.fromEntries(calls.map((k) => [k, vi.fn()])) };
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  sessionStorage.clear();
  localStorage.clear();
});

function inRouter(ui) {
  return render(<MemoryRouter>{ui}</MemoryRouter>);
}

// ---------- findings ----------

const finding = {
  id: "f-1",
  summary: "Grammar: growth area in S3.",
  support_level: "developing",
  support_level_label: "Developing",
  confidence: 0.97,
  confidence_label: "High",
  pattern: "consistent",
  skill_state: "misapplied",
  standard_code: "3.L.1.A.d",
  strand: "Language",
  concept: "Grammar",
  standard_description: "Apply standard English grammar",
  evidence: [{ paragraph: 3, reason: "Combines most with a superlative." }],
  is_draft: true,
  teacher_response: null,
};

describe("FindingCard (read-only)", () => {
  it("shows level and confidence as words, never a percentage", () => {
    inRouter(<FindingCard finding={finding} />);
    expect(screen.getByText("Developing")).toBeInTheDocument();
    expect(screen.getByText("High confidence")).toBeInTheDocument();
    expect(screen.queryByText(/%/)).not.toBeInTheDocument();
    expect(screen.getByText("Consistent")).toBeInTheDocument();
    expect(screen.getByText("Skill misapplied")).toBeInTheDocument();
    expect(screen.getByText("Combines most with a superlative.")).toBeInTheDocument();
  });

  it("has no agree / disagree / dismiss controls in this release", () => {
    inRouter(<FindingCard finding={finding} />);
    expect(screen.queryByRole("radio")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Save answer/ })).not.toBeInTheDocument();
  });

  it("shows the standard on request", async () => {
    const user = userEvent.setup();
    inRouter(<FindingCard finding={finding} />);
    await user.click(screen.getByRole("button", { name: "Show standard 3.L.1.A.d" }));
    expect(screen.getByText("Apply standard English grammar")).toBeInTheDocument();
  });
});

// ---------- dashboard ----------

describe("DashboardPage", () => {
  it("shows totals, class cards and what needs attention", async () => {
    api.me.mockResolvedValue({ id: 1, email: "t@x", role: "teacher", display_name: "Demo Teacher" });
    api.dashboard.mockResolvedValue({
      user: { display_name: "Demo Teacher", role: "teacher" },
      totals: { classrooms: 1, students: 18, students_needing_attention: 4, pending_pairs: 20, failed_reviews: 0 },
      classrooms: [
        {
          classroom_id: "c1",
          name: "English 9, Period 1",
          grade_level: 9,
          student_count: 18,
          students_needing_attention: 4,
          pending_pairs: 20,
          failed_reviews: 0,
          last_reviewed_at: "2026-11-12T13:23:34-06:00",
          analysis_as_of: null,
        },
      ],
    });
    api.notifications.mockResolvedValue([
      { id: "needs_attention:c1", kind: "needs_attention", classroom_id: "c1", count: 4, message: "4 students need attention in English 9, Period 1." },
    ]);

    render(
      <MemoryRouter>
        <AuthProvider>
          <DashboardPage />
        </AuthProvider>
      </MemoryRouter>
    );

    expect(await screen.findByText("Welcome, Demo Teacher")).toBeInTheDocument();
    expect(screen.getByText("English 9, Period 1")).toBeInTheDocument();
    expect(await screen.findByText("4 students need attention in English 9, Period 1.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open" })).toHaveAttribute("href", "/classrooms/c1");
    expect(screen.getByText(/See who needs help \(4 students\)/)).toBeInTheDocument();
    // Peer pairs are left out of this release
    expect(screen.queryByText(/Pairs to decide|Peer pairs/)).not.toBeInTheDocument();
  });

  it("shows the backend's error", async () => {
    api.dashboard.mockRejectedValue(new Error("the database is not reachable"));
    api.notifications.mockResolvedValue([]);
    render(
      <MemoryRouter>
        <AuthProvider>
          <DashboardPage />
        </AuthProvider>
      </MemoryRouter>
    );
    expect(await screen.findByRole("alert")).toHaveTextContent("the database is not reachable");
  });
});

// Routes used by notification links
describe("notification links", () => {
  it("send failures to the students tab and the rest to the class summary", async () => {
    const { notificationTarget } = await import("../components/Layout.jsx");
    expect(notificationTarget({ kind: "failed_reviews", classroom_id: "c 1" })).toBe("/classrooms/c%201/students");
    expect(notificationTarget({ kind: "needs_attention", classroom_id: "c1" })).toBe("/classrooms/c1");
  });
});
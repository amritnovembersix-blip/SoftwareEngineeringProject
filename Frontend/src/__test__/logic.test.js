import { describe, expect, it } from "vitest";
import { summarizeConcepts } from "../pages/class/summarize.js";

const c = (code, status, extra = {}) => ({
  concept_code: code,
  skill_code: code.replace(/^\d+\./, ""),
  strand: "Writing",
  concept_name: `Name ${code}`,
  status,
  current_level: status === "needs_attention" ? 2 : 0,
  trend: "steady",
  ...extra,
});

describe("class summary from each student's concepts", () => {
  const byStudent = {
    s1: [c("9.W.1.A", "needs_attention", { trend: "declining" }), c("9.W.2.A", "secure")],
    s2: [c("9.W.1.A", "needs_attention"), c("9.W.2.A", "secure")],
    s3: [c("9.W.1.A", "secure"), c("9.W.2.A", "too_little_evidence")],
    s4: [], // enrolled, nothing reviewed
  };
  const sum = summarizeConcepts(byStudent);

  it("counts students with evidence, not every enrolled student", () => {
    expect(sum.studentsWithEvidence).toBe(3);
  });

  it("ranks gaps by how many students need attention and names them", () => {
    expect(sum.gaps.map((g) => g.concept_code)).toEqual(["9.W.1.A"]);
    expect(sum.gaps[0].counts.needs_attention).toBe(2);
    expect(sum.gaps[0].students.needs_attention).toEqual(["s1", "s2"]);
    expect(sum.gaps[0].trends.declining).toBe(1);
  });

  it("ranks strengths by students secure", () => {
    expect(sum.strengths.map((s) => [s.concept_code, s.counts.secure])).toEqual([
      ["9.W.2.A", 2],
      ["9.W.1.A", 1],
    ]);
  });

  it("lists students needing attention, most gaps first", () => {
    expect(sum.studentsNeedingAttention.map((s) => s.student_id)).toEqual(["s1", "s2"]);
  });

  it("handles an empty class", () => {
    const empty = summarizeConcepts({});
    expect(empty.studentsWithEvidence).toBe(0);
    expect(empty.gaps).toEqual([]);
  });
});
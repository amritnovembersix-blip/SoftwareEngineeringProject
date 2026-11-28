/**
 * Turn every student's concept list into class-level counts, without percentages:
 * per concept, how many students are in each status, and which students.
 *
 * byStudent = { studentId: [ {concept_code, skill_code, strand, concept_name, status,
 *                             current_level, level_label, basis, trend}, ... ] }
 */
export const STATUSES = ["needs_attention", "emerging", "too_little_evidence", "secure"];

export function summarizeConcepts(byStudent) {
  const concepts = new Map();
  const withEvidence = new Set();

  for (const [studentId, list] of Object.entries(byStudent || {})) {
    if (list.length) withEvidence.add(studentId);
    for (const c of list) {
      let s = concepts.get(c.concept_code);
      if (!s) {
        s = {
          concept_code: c.concept_code,
          skill_code: c.skill_code,
          strand: c.strand,
          concept_name: c.concept_name,
          counts: Object.fromEntries(STATUSES.map((k) => [k, 0])),
          students: Object.fromEntries(STATUSES.map((k) => [k, []])),
          trends: { improving: 0, steady: 0, declining: 0, unclear: 0 },
          assessed: 0,
        };
        concepts.set(c.concept_code, s);
      }
      if (s.counts[c.status] !== undefined) {
        s.counts[c.status] += 1;
        s.students[c.status].push(studentId);
      }
      if (c.status !== "too_little_evidence") s.assessed += 1;
      if (s.trends[c.trend] !== undefined) s.trends[c.trend] += 1;
    }
  }

  const list = [...concepts.values()];
  return {
    concepts: list,
    studentsWithEvidence: withEvidence.size,
    /** Most students needing attention first. */
    gaps: list
      .filter((c) => c.counts.needs_attention > 0)
      .sort((a, b) => b.counts.needs_attention - a.counts.needs_attention || a.concept_code.localeCompare(b.concept_code)),
    /** Most students secure first. */
    strengths: list
      .filter((c) => c.counts.secure > 0)
      .sort((a, b) => b.counts.secure - a.counts.secure || a.concept_code.localeCompare(b.concept_code)),
    /** Students with at least one concept needing attention. */
    studentsNeedingAttention: Object.entries(byStudent || {})
      .filter(([, l]) => l.some((c) => c.status === "needs_attention"))
      .map(([id, l]) => ({ student_id: id, concepts: l.filter((c) => c.status === "needs_attention") }))
      .sort((a, b) => b.concepts.length - a.concepts.length || a.student_id.localeCompare(b.student_id)),
  };
}

export function strandsOf(concepts) {
  return [...new Set(concepts.map((c) => c.strand).filter(Boolean))].sort();
}
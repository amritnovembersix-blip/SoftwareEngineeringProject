"""Read-only findings query for the Simulation tab: codes, counts, support levels and dates, never free text."""
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime

BANDS = ("3–5", "6–8", "9–10", "11–12")
SUPPORT_LEVELS = ("approaching", "developing", "beginning")  # lowest to highest need
_SEP = " · "


@dataclass(frozen=True)
class FindingCell:
    """One (student, skill area, submission): its findings with a standard in the area, and its strengths there."""
    student_hash: str
    area: str
    due_at: datetime
    count: int  # distinct findings with at least one standard in the area
    level: str | None  # the highest support level among them (None if none is set)
    strengths: int  # verdicts with notable_strength in the area


def band(grade_min: int) -> str:
    if grade_min <= 5:
        return BANDS[0]
    if grade_min <= 8:
        return BANDS[1]
    return BANDS[2] if grade_min <= 10 else BANDS[3]


def area_of(grade_min: int, strand_code: str, concept: str | None) -> str:
    """A skill area: grade band, strand and catalog concept, e.g. "3–5 · L · Grammar"."""
    return _SEP.join((band(grade_min), strand_code, concept or "—"))


def area_sort_key(area: str) -> tuple:
    """Band order, then strand, then concept."""
    b, _, rest = area.partition(_SEP)
    return (BANDS.index(b) if b in BANDS else len(BANDS), rest)


def highest_level(levels) -> str | None:
    known = [lv for lv in levels if lv in SUPPORT_LEVELS]
    return max(known, key=SUPPORT_LEVELS.index) if known else None


# Reviewed (latest-attempt) submissions of one classroom, due by the cut-off.
_SUBMISSIONS = """
  FROM submission s
  JOIN assignment a ON a.id = s.assignment_id
  JOIN classroom c ON c.id = a.classroom_id
  JOIN student st ON st.id = s.student_id
"""
_WHERE = """
 WHERE c.lms_course_id = %(course)s AND a.due_at <= %(through)s
   AND s.review_status = 'succeeded' AND s.superseded_at IS NULL
"""

_FINDINGS = ("SELECT DISTINCT st.lms_user_id, s.id, a.due_at, f.id, f.support_level, "
             "std.grade_min, std.strand_code, std.concept" + _SUBMISSIONS + """
  JOIN finding f ON f.submission_id = s.id
  JOIN finding_standard fs ON fs.finding_id = f.id
  JOIN standard std ON std.id = fs.standard_id""" + _WHERE)

_STRENGTHS = ("SELECT st.lms_user_id, s.id, a.due_at, std.grade_min, std.strand_code, std.concept, count(*)"
              + _SUBMISSIONS + """
  JOIN verdict v ON v.submission_id = s.id AND v.notable_strength
  JOIN standard std ON std.id = v.standard_id""" + _WHERE + """
 GROUP BY st.lms_user_id, s.id, a.due_at, std.grade_min, std.strand_code, std.concept""")


def findings_by_area(conn, lms_course_id: str, through: datetime) -> list[FindingCell]:
    """Reviewed submissions of the classroom due by `through`, per (student, area, submission). SELECT only."""
    params = {"course": lms_course_id, "through": through}
    findings: dict[tuple, dict] = defaultdict(dict)  # key -> {finding id: support level}
    strengths: dict[tuple, int] = defaultdict(int)
    for student, sub, due, fid, level, grade_min, strand, concept in conn.execute(_FINDINGS, params):
        findings[(student, area_of(grade_min, strand, concept), sub, due)][fid] = level
    for student, sub, due, grade_min, strand, concept, n in conn.execute(_STRENGTHS, params):
        strengths[(student, area_of(grade_min, strand, concept), sub, due)] += n
    cells = []
    for key in sorted(set(findings) | set(strengths), key=lambda k: (k[0], k[3], str(k[2]), area_sort_key(k[1]))):
        student, area, _, due = key
        levels = findings.get(key, {})
        cells.append(FindingCell(student_hash=student, area=area, due_at=due, count=len(levels),
                                 level=highest_level(levels.values()), strengths=strengths.get(key, 0)))
    return cells

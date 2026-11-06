"""Pure helpers for the Streamlit Simulation tab (no UI, no Adapters): pipeline rows, the classroom grid,
twin pairs and intervention marks. Every value is an id, hash, code, count or date; never student text."""
import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from agentic.coursedata import CourseDir, lms_assignment_id

from agentic_lab.sim.findings_query import FindingCell, area_of, area_sort_key, highest_level
from agentic_lab.sim.schedule import schedule
from agentic_lab.sim.simulator import AssignmentStatus

_CENTRAL = ZoneInfo("America/Chicago")
_RUN_COUNTS = ("queued", "running", "succeeded", "failed", "unknown")


def week_end(start_date: date, week: int) -> datetime:
    """Sunday 23:59:59 America/Chicago of the course week (week 1 starts on start_date, a Monday), as UTC."""
    sunday = start_date + timedelta(weeks=week - 1, days=6)
    return datetime.combine(sunday, time(23, 59, 59), tzinfo=_CENTRAL).astimezone(timezone.utc)


# --- Pipeline ----------------------------------------------------------------------------------

def assignment_details(data_dir: Path, versions: list[str]) -> dict[tuple[str, str], tuple[str, datetime]]:
    """(version, file id) -> (assignment type, due_at UTC)."""
    out = {}
    for version in versions:
        types = {a.id: a.type for a in CourseDir(data_dir, version).assignments()}
        for item in schedule(data_dir, version):
            out[(version, item.file_id)] = (types[item.file_id], item.due_at)
    return out


def pipeline_rows(status: list[AssignmentStatus], through_week: int,
                  details: dict[tuple[str, str], tuple[str, datetime]] | None = None,
                  run_states: bool = True) -> list[dict]:
    """One row per assignment due up to `through_week`; type and local due date come from `details`. Without
    `run_states` (not fetched from the service) the run counts are None."""
    rows = []
    for s in status:
        if s.week > through_week:
            continue
        kind, due = (details or {}).get((s.version, s.file_id), (None, None))
        rows.append({"course": s.version, "assignment": lms_assignment_id(s.file_id, s.version), "week": s.week,
                     "type": kind, "due": due.astimezone(_CENTRAL).date() if due is not None else None,
                     "released": s.released, "sent": s.sent,
                     **{k: getattr(s, k) if run_states else None for k in _RUN_COUNTS}})
    return rows


# --- Classroom grid ----------------------------------------------------------------------------

@dataclass(frozen=True)
class GridCell:
    count: int  # findings in the area
    level: str | None  # the highest support level among them
    strengths: int


def _aggregate(cells: list[FindingCell]) -> dict[tuple[str, str], GridCell]:
    """(student hash, area) -> GridCell, summing counts and strengths and keeping the highest level."""
    acc: dict[tuple[str, str], list] = defaultdict(lambda: [0, [], 0])
    for c in cells:
        a = acc[(c.student_hash, c.area)]
        a[0] += c.count
        a[1].append(c.level)
        a[2] += c.strengths
    return {k: GridCell(count=n, level=highest_level(levels), strengths=s) for k, (n, levels, s) in acc.items()}


def grid(cells: list[FindingCell], pairs: list[tuple[str, str]] | None = None,
         through: datetime | None = None) -> tuple[list[str], list[str], dict]:
    """(row labels, area columns, values). Cells due after `through` are left out.

    Without pairs, a row is a student (labeled by the first 8 characters of the hash) and a value is a GridCell.
    With pairs (control hash, intervention hash), row i is "p{i+1:02d}" and a value is
    (control GridCell | None, intervention GridCell | None); pass the cells of both twins together.
    """
    if through is not None:
        cells = [c for c in cells if c.due_at <= through]
    per_student = _aggregate(cells)
    values: dict = {}
    if pairs is None:
        for (student, area), cell in per_student.items():
            values[(student[:8], area)] = cell
        rows = sorted({row for row, _ in values})
    else:
        rows = [f"p{i + 1:02d}" for i in range(len(pairs))]
        areas_of = defaultdict(set)
        for student, area in per_student:
            areas_of[student].add(area)
        for row, (control, intervention) in zip(rows, pairs):
            for area in areas_of[control] | areas_of[intervention]:
                values[(row, area)] = (per_student.get((control, area)), per_student.get((intervention, area)))
    areas = sorted({area for _, area in values}, key=area_sort_key)
    return rows, areas, values


def roster_pairs(data_dir: Path, control_version: str, intervention_version: str) -> list[tuple[str, str]]:
    """Twin pairs as (control hash, intervention hash), zipping the two rosters by position.

    Assumes both roster.jsonl files list students in pair order (true of the generated v2 courses), so the
    tab never needs the answer key's pair ids. Rosters of different lengths raise ValueError.
    """
    control = [r.student_hash for r in CourseDir(data_dir, control_version).roster()]
    intervention = [r.student_hash for r in CourseDir(data_dir, intervention_version).roster()]
    if len(control) != len(intervention):
        raise ValueError(f"rosters of {control_version} and {intervention_version} differ in length "
                         f"({len(control)} and {len(intervention)})")
    return list(zip(control, intervention))


def intervention_marks(data_dir: Path, version: str) -> list[dict]:
    """The course's interventions for the week axis: week, id, kind and students ("all" or a list of hashes)."""
    return [{"week": iv.week, "id": iv.id, "kind": iv.kind, "students": iv.students}
            for iv in CourseDir(data_dir, version).interventions()]


# --- Ground truth (lab only) -------------------------------------------------------------------

def ground_truth(data_dir: Path, version: str, week: int, standards_file: Path) -> dict[tuple[str, str], int]:
    """LAB ONLY: the planted level (1 approaching .. 3 beginning) per (student hash, area) as of `week`.

    The only reader of the course's answer_key/ in the Simulation tab, called only when its overlay toggle is
    on. For each (student, standard) the latest trajectory row with week <= `week` counts; an area takes
    the highest level of its standards.
    """
    from agentic.standards.loader import normalize_rows, read_xlsx

    from agentic_lab.answer_key import TrajectoryRow, answer_key_path

    area = {r.code: area_of(r.grade_min, r.strand_code, r.concept)
            for r in normalize_rows(read_xlsx(Path(standards_file)))}
    latest: dict[tuple[str, str], TrajectoryRow] = {}
    path = answer_key_path(data_dir, version) / "trajectory.jsonl"
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = TrajectoryRow.model_validate(json.loads(line))
        key = (row.student_hash, row.standard)
        if row.week <= week and (key not in latest or row.week >= latest[key].week):
            latest[key] = row
    out: dict[tuple[str, str], int] = {}
    for (student, code), row in latest.items():
        if code in area:
            k = (student, area[code])
            out[k] = max(out.get(k, 0), row.level)
    return out

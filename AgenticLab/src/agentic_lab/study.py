"""Lab only: the intervention study. Did the intervention classroom's students improve on the intervention's
skill families more than their control twins did? Reads verdict rows through a ResultsReader (answer-key
results or Postgres) and, for comparison, the planted effect from the answer key's trajectory.
Everything reported is a code, a count or a short student hash; never student text."""
import json
import statistics
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from math import comb
from pathlib import Path
from zoneinfo import ZoneInfo

from agentic.analytics.base import ResultsReader
from agentic.analytics.timeline import build_timeline, concept_code
from agentic.coursedata import CourseDir
from agentic.dataset import read_rows

from agentic_lab.answer_key import StudentKey, TrajectoryRow, answer_key_path

_CENTRAL = ZoneInfo("America/Chicago")
_END = datetime(2100, 1, 1, tzinfo=timezone.utc)
_TAIL = 3  # the term's last due dates


def sign_test_p(diffs: list[float]) -> float:
    """Exact two-sided binomial sign test (p = 0.5); zeros are dropped; 1.0 when nothing is left."""
    n = sum(1 for d in diffs if d)
    if n == 0:
        return 1.0
    k = min(sum(1 for d in diffs if d < 0), sum(1 for d in diffs if d > 0))
    return min(1.0, 2 * sum(comb(n, i) for i in range(k + 1)) / 2 ** n)


def wilcoxon_p(diffs: list[float]) -> float | None:
    """Wilcoxon signed-rank p-value (zeros split between the ranks); None below five pairs."""
    if len(diffs) < 5 or not any(diffs):
        return None
    from scipy.stats import wilcoxon
    return float(wilcoxon(diffs, zero_method="zsplit").pvalue)


@dataclass
class InterventionResult:
    id: str
    week: int
    kind: str
    families: list[str]
    n_pairs: int  # responders with a change in both arms
    skipped: int  # participants dropped for lacking points in a window (either arm)
    differences: list[float]  # intervention change minus twin change, in roster order; negative = improved
    median_difference: float | None
    planted_differences: list[float]
    planted_median_difference: float | None
    sign_p: float | None
    wilcoxon_p: float | None
    non_responder: dict | None = None  # {student, difference, planted_difference}


@dataclass
class StudyReport:
    twins: str
    interventions: list[InterventionResult] = field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)


Points = dict[str, list[tuple[datetime, int, int]]]  # student -> (due_at, course week, level)


def _week(due: datetime, start) -> int:
    """The course week (week 1 starts on `start`, a Monday) of a due date, by its America/Chicago date."""
    return (due.astimezone(_CENTRAL).date() - start).days // 7 + 1


def _change(points: list[tuple[datetime, int, int]], week: int) -> float | None:
    """`points` are one student's (due_at, week, level) over the family concepts. Change = mean level of the
    points on the last three distinct due dates, minus the mean level of the points due in `week` (the
    intervention week, Monday to Sunday). None when either set is empty. Negative = improved."""
    window = [lv for _, w, lv in points if w == week]
    tail_dates = sorted({d for d, _, _ in points})[-_TAIL:]
    tail = [lv for d, _, lv in points if d in tail_dates]
    if not window or not tail:
        return None
    return statistics.fmean(tail) - statistics.fmean(window)


def _arm(data_dir: Path, version: str, reader: ResultsReader, concepts: set[str]):
    """One course's measured points (from the reader's verdicts), planted points (the same grid, with the
    level the answer key's trajectory has for that student, week and concept) and answer-key student rows."""
    course = CourseDir(data_dir, version).course()
    key = answer_key_path(data_dir, version)
    keys = {k.student_hash: k for k in read_rows(key / "students.jsonl", StudentKey)}
    levels: dict[tuple[str, int, str], int] = {}
    for t in read_rows(key / "trajectory.jsonl", TrajectoryRow):
        k = (t.student_hash, t.week, concept_code(t.standard))
        levels[k] = max(levels.get(k, 0), t.level)
    measured: Points = {}
    planted: Points = {}
    for (student, concept), pts in sorted(build_timeline(reader.verdict_rows(course.id, _END)).items()):
        if concept in concepts:
            for p in pts:
                w = _week(p.due_at, course.start_date)
                measured.setdefault(student, []).append((p.due_at, w, p.level))
                planted.setdefault(student, []).append((p.due_at, w, levels.get((student, w, concept), 0)))
    return measured, planted, keys


def study(reader_by_version: dict[str, ResultsReader], data_dir: Path, twins: str = "2.00") -> StudyReport:
    """The twin study for courses `<twins>-control` and `<twins>-intervention`. For each intervention and each
    participating student, the control twin (same `pair_id`) is the comparison; see `_change`. The student
    the answer key marks as not responding to the intervention is reported separately, not in the pairs."""
    data_dir = Path(data_dir)
    control_v, iv_v = f"{twins}-control", f"{twins}-intervention"
    families = json.loads((data_dir / "skill_families.json").read_text(encoding="utf-8"))
    iv_dir = CourseDir(data_dir, iv_v)
    roster = [r.student_hash for r in iv_dir.roster()]
    report = StudyReport(twins)
    for iv in iv_dir.interventions():
        standards = {s for f in iv.families for s in families[f]}
        if standards != set(iv.standards):
            raise ValueError(f"{iv.id}: interventions.json standards differ from skill_families.json")
        concepts = {concept_code(s) for s in standards}
        m_c, p_c, k_c = _arm(data_dir, control_v, reader_by_version[control_v], concepts)
        m_i, p_i, k_i = _arm(data_dir, iv_v, reader_by_version[iv_v], concepts)
        twin_of = {k.pair_id: h for h, k in k_c.items()}
        diffs, planted_diffs, skipped, non = [], [], 0, None
        for student in (roster if iv.students == "all" else iv.students):
            twin = twin_of[k_i[student].pair_id]
            c_i, c_c = _change(m_i.get(student, []), iv.week), _change(m_c.get(twin, []), iv.week)
            q_i, q_c = _change(p_i.get(student, []), iv.week), _change(p_c.get(twin, []), iv.week)
            if None in (c_i, c_c, q_i, q_c):
                skipped += 1
            elif k_i[student].non_responder_iv == iv.id:
                non = {"student": student[:8], "difference": round(c_i - c_c, 9),
                       "planted_difference": round(q_i - q_c, 9)}
            else:
                diffs.append(round(c_i - c_c, 9))  # float residue must not count as a direction
                planted_diffs.append(round(q_i - q_c, 9))
        report.interventions.append(InterventionResult(
            iv.id, iv.week, iv.kind, list(iv.families), len(diffs), skipped, diffs,
            statistics.median(diffs) if diffs else None, planted_diffs,
            statistics.median(planted_diffs) if planted_diffs else None,
            sign_test_p(diffs) if diffs else None, wilcoxon_p(diffs), non))
    return report


def _fmt(x: float | None) -> str:
    return "n/a" if x is None else f"{x:.3f}"


def format_report(r: StudyReport) -> str:
    lines = [f"# Intervention study (twins {r.twins})", "",
             "Change = mean level over the last 3 due dates minus the intervention week's mean level, over the "
             "families' concepts. Difference = intervention change minus twin change; negative = improvement.", "",
             "| Intervention | Week | Families | n pairs | Skipped | Median diff | Planted | Sign p | Wilcoxon p "
             "| Non-responder diff |", "|---|---|---|---|---|---|---|---|---|---|"]
    for i in r.interventions:
        non = _fmt(i.non_responder["difference"]) if i.non_responder else "none"
        lines.append(f"| {i.id} | {i.week} | {', '.join(i.families)} | {i.n_pairs} | {i.skipped} | "
                     f"{_fmt(i.median_difference)} | {_fmt(i.planted_median_difference)} | {_fmt(i.sign_p)} | "
                     f"{_fmt(i.wilcoxon_p)} | {non} |")
    return "\n".join(lines)


def write_reports(r: StudyReport, reports_dir: Path) -> tuple[Path, Path]:
    reports_dir = Path(reports_dir)
    reports_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    json_path, md_path = reports_dir / f"study-{stamp}.json", reports_dir / f"study-{stamp}.md"
    json_path.write_text(json.dumps(r.as_dict(), indent=2), encoding="utf-8")
    md_path.write_text(format_report(r) + "\n", encoding="utf-8")
    return json_path, md_path

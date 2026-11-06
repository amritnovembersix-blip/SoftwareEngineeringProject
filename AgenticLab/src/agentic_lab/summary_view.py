"""Pure helpers for the Streamlit Class summary tab (no UI): table rows, the heatmap matrix and the twin
divergence series. Every value is an id (students as 8-character hashes), code, level or count; never student text."""
import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

from agentic.analytics.memory import InMemoryAnalyticsWriter
from agentic.analytics.run import analyze_classroom
from agentic.analytics.status import AnalyticsSettings, ConceptStatus, from_effective
from agentic.analytics.summary import ClassWeek, Gap, PairSuggestion, ReteachGroup, common_gaps, week_start
from agentic.analytics.timeline import build_timeline, concept_code
from agentic.config import Settings
from agentic.coursedata import CourseDir
from agentic.settings.base import effective, load_defs

from agentic_lab.analytics_lab import results_from_answer_key

_END = datetime(2100, 1, 1, tzinfo=timezone.utc)
_STATUS_ORDER = ("needs_attention", "emerging", "secure", "too_little_evidence")
_GRADES_3_TO_5 = {"3", "4", "5"}


def short(student_id: str) -> str:
    return student_id[:8]


@dataclass(frozen=True)
class Summary:
    statuses: list[ConceptStatus]
    weeks: list[ClassWeek]
    groups: list[ReteachGroup]
    pairs: list[PairSuggestion]
    gaps: list[Gap]


# --- Tables ------------------------------------------------------------------------------------

def status_rows(statuses: list[ConceptStatus]) -> list[dict]:
    n = {s: 0 for s in _STATUS_ORDER}
    for st in statuses:
        n[st.status] += 1
    return [{"status": s, "students": n[s]} for s in _STATUS_ORDER]


def gap_rows(gaps: list[Gap]) -> list[dict]:
    return [{"concept": g.concept_code, "students": len(g.students), "top_condition": g.top_condition,
             "who": ", ".join(short(s) for s in g.students)} for g in gaps]


def group_rows(groups: list[ReteachGroup]) -> list[dict]:
    """A group as a root-cause chain, e.g. `3.L.1.A ← 9-10.W.3.A: 7 students`."""
    return [{"chain": f"{g.root_concept} ← {g.grade_concept}: {len(g.students)} "
                      f"student{'' if len(g.students) == 1 else 's'}",
             "students": ", ".join(short(s) for s in g.students)} for g in groups]


def pair_rows(pairs: list[PairSuggestion]) -> list[dict]:
    return [{"concept": p.concept_code, "tutor": short(p.tutor_id), "tutee": short(p.tutee_id)} for p in pairs]


# --- Heatmap -----------------------------------------------------------------------------------

def band(concept: str) -> str:
    """The grade band of a concept code: 3-5 for grades 3, 4 and 5; otherwise its grade token ("6", "9-10")."""
    token = concept.split(".")[0]
    return "3-5" if token in _GRADES_3_TO_5 else token


def _group_label(concept: str) -> str:
    return f"{band(concept)}.{concept.split('.')[1]}"


def _mean(counts: list[int]) -> float | None:
    n = sum(counts)
    return sum(i * c for i, c in enumerate(counts)) / n if n else None


def heatmap(weeks: list[ClassWeek], group: bool = False) -> tuple[list[str], list[date], dict]:
    """(row labels, week starts, {(row, week_start): mean current level | None}). The mean runs over students
    with a level (too_little_evidence is left out). With `group`, rows are `<band>.<strand>` and the counts of
    the concepts in a group are pooled, so the mean is per student-concept. For display only."""
    pooled: dict[tuple[str, date], list[int]] = defaultdict(lambda: [0, 0, 0, 0])
    for w in weeks:
        acc = pooled[(_group_label(w.concept_code) if group else w.concept_code, w.week_start)]
        for i, c in enumerate(w.counts):
            acc[i] += c
    rows = sorted({r for r, _ in pooled}, key=lambda r: (int(r.split('.')[0].split('-')[0]), r))
    starts = sorted({d for _, d in pooled})
    values = {(r, d): _mean(pooled[(r, d)]) if (r, d) in pooled else None for r in rows for d in starts}
    return rows, starts, values


# --- Summary sources ---------------------------------------------------------------------------

def analytics_defaults() -> AnalyticsSettings:
    """The TOML defaults of the teacher/admin settings (no database overrides)."""
    return from_effective(effective(load_defs(Settings().config_dir), []))


def answer_key_summary(data_dir: Path, version: str, settings: AnalyticsSettings | None = None) -> Summary:
    """The analysis of one course's answer key as of the end of term, in memory (nothing is saved)."""
    reader, writer = results_from_answer_key(data_dir, version), InMemoryAnalyticsWriter()
    classroom = CourseDir(data_dir, version).course().id
    res = analyze_classroom(reader, writer, classroom, _END, settings or analytics_defaults(), "lab")
    return Summary(res.statuses, writer.saved[-1].weeks, res.groups, res.pairs, res.gaps)


SQL = {
    "status": "SELECT student_id, classroom_id, concept_code, status, current_level, basis, points, trend "
              "FROM concept_status_latest WHERE classroom_id = %s ORDER BY student_id, concept_code",
    "weeks": "SELECT concept_code, week_start, n_level0, n_level1, n_level2, n_level3, n_too_little "
             "FROM class_concept_week_latest WHERE classroom_id = %s ORDER BY concept_code, week_start",
    "groups": "SELECT group_id, root_concept, grade_concept, student_id FROM reteach_group_latest "
              "WHERE classroom_id = %s ORDER BY group_id, student_id",
    "pairs": "SELECT concept_code, tutor_id, tutee_id FROM peer_pair_latest WHERE classroom_id = %s "
             "ORDER BY concept_code, tutee_id",
}


def summary_from_rows(status_rows_: list[tuple], week_rows: list[tuple], group_rows_: list[tuple],
                      pair_rows_: list[tuple], classroom_id: str = "") -> Summary:
    """A Summary from rows of the V8 *_latest views, in the column orders of `SQL`. Gaps have no condition
    (the view keeps none)."""
    statuses = [ConceptStatus(s, c, k, st, int(lv), basis, tuple(int(p) for p in pts), trend)
                for s, c, k, st, lv, basis, pts, trend in status_rows_]
    weeks = [ClassWeek(classroom_id, k, d, (n0, n1, n2, n3), nt) for k, d, n0, n1, n2, n3, nt in week_rows]
    members: dict[tuple, list[str]] = defaultdict(list)
    for gid, root, grade, student in group_rows_:
        members[(gid, root, grade)].append(student)
    groups = sorted((ReteachGroup(root, grade, tuple(sorted(ids))) for (_, root, grade), ids in members.items()),
                    key=lambda g: (-len(g.students), g.root_concept, g.grade_concept))
    pairs = [PairSuggestion(k, tutor, tutee) for k, tutor, tutee in pair_rows_]
    return Summary(statuses, weeks, groups, pairs, common_gaps(statuses, {}))


def postgres_summary(conn, classroom_id: str) -> Summary | None:
    """The latest saved analysis of a classroom (the V8 *_latest views), or None when there is none."""
    got = {}
    for name, sql in SQL.items():
        with conn.cursor() as cur:
            cur.execute(sql, (classroom_id,))
            got[name] = cur.fetchall()
    if not got["status"]:
        return None
    return summary_from_rows(got["status"], got["weeks"], got["groups"], got["pairs"], classroom_id)


# --- Twin divergence ---------------------------------------------------------------------------

def intervention_ids(data_dir: Path, intervention_version: str) -> list[str]:
    return [iv.id for iv in CourseDir(data_dir, intervention_version).interventions()]


def _weekly_means(data_dir: Path, version: str, concepts: set[str], reader=None) -> list[tuple[int, float]]:
    """Mean evidence level per course week over the points of the given concepts (every student). `reader`
    defaults to the answer key's (the planted levels)."""
    course = CourseDir(data_dir, version).course()
    reader = reader or results_from_answer_key(data_dir, version)
    levels: dict[int, list[int]] = defaultdict(list)
    for (_, concept), pts in build_timeline(reader.verdict_rows(course.id, _END)).items():
        if concept in concepts:
            for p in pts:
                levels[(week_start(p.due_at) - course.start_date).days // 7 + 1].append(p.level)
    return [(w, sum(v) / len(v)) for w, v in sorted(levels.items())]


def divergence(data_dir: Path, intervention_id: str, twins: str = "2.00", reader=None) -> dict:
    """{"series": {classroom version: [(week, mean level)]}, "planted": {...} | None, "marks": [{"week", "id"}]}:
    the family concepts of one intervention, control vs intervention, plus every intervention's week. Without
    `reader`, "series" is the answer key's planted levels and "planted" is None. With a ResultsReader (saved
    reviews), "series" is what SASS measured and "planted" is the answer key's, for comparison (lab only)."""
    control_v, iv_v = f"{twins}-control", f"{twins}-intervention"
    ivs = CourseDir(data_dir, iv_v).interventions()
    chosen = next(iv for iv in ivs if iv.id == intervention_id)
    families = json.loads((Path(data_dir) / "skill_families.json").read_text(encoding="utf-8"))
    concepts = {concept_code(s) for f in chosen.families for s in families[f]}
    return {"series": {v: _weekly_means(data_dir, v, concepts, reader) for v in (control_v, iv_v)},
            "planted": {v: _weekly_means(data_dir, v, concepts) for v in (control_v, iv_v)} if reader else None,
            "marks": [{"week": iv.week, "id": iv.id} for iv in ivs]}


def divergence_rows(d: dict) -> list[dict]:
    """Long-form rows (week, classroom, level, source) for a line chart; `source` is "measured" or "planted"."""
    first = "measured" if d.get("planted") else "planted"
    out = [{"week": w, "classroom": v, "level": m, "source": first} for v, pts in d["series"].items() for w, m in pts]
    out += [{"week": w, "classroom": v, "level": m, "source": "planted"}
            for v, pts in (d.get("planted") or {}).items() for w, m in pts]
    return out

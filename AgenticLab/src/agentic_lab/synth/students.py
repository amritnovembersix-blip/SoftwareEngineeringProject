"""Course plan, twin rosters, interventions and layered trajectories for v2 course folders (lab only).

`build-course` turns a plan (`plans/<curriculum>.json`) into both twin course folders and their answer keys. No
LLM is involved; everything is deterministic from the seed. See the synthetic-data v2 design spec, sections 2
and 4.2-4.5.
"""
import hashlib
import json
import random
from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

from agentic.coursedata import (
    AssignmentFile, CourseFile, Curriculum, InterventionRow, course_path, curriculum_path, read_json)
from agentic_lab.answer_key import FamilyEntry, StandardLevel, StudentKey, TrajectoryRow, answer_key_path
from agentic_lab.standards_v2 import REVIEWABILITY_CSV, reviewable_codes
from agentic_lab.synth.families import SkillFamily, combine, family_levels, grade_key, load_families

Arm = Literal["control", "intervention"]
ARMS: tuple[Arm, ...] = ("control", "intervention")
WEEKS = range(1, 12)
QUIRK_STRANDS = ("W", "L")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CourseMeta(_Strict):
    name: str
    period: int


class Profile(_Strict):
    name: str
    count: int
    families: list[FamilyEntry]
    quirks: list[int]  # quirk count per student, in order
    start_week: int = 1
    jitter: bool = True

    @model_validator(mode="after")
    def _one_quirk_count_per_student(self):
        if len(self.quirks) != self.count:
            raise ValueError("profile quirks needs one count per student")
        return self


class PlanIntervention(_Strict):
    id: str
    week: int
    kind: Literal["class_lesson", "small_group", "individual"]
    profile: str  # a profile name, or "all"
    families: list[str]
    rate: int
    description: str
    assignment: AssignmentFile | None = None  # assigned_to is filled in by build_course


class NonResponder(_Strict):
    intervention: str
    index: int = 0  # position within the intervention's students


class CoursePlan(_Strict):
    curriculum: str
    version: str
    start_date: date
    teacher_id: str
    courses: dict[Arm, CourseMeta]
    profiles: list[Profile]
    interventions: list[PlanIntervention]
    non_responder: NonResponder | None = None


def load_plan(path: Path) -> CoursePlan:
    return read_json(Path(path), CoursePlan)


def student_hash(arm: str, pair_id: str, seed: int) -> str:
    return hashlib.sha256(f"{arm}:{pair_id}:{seed}".encode()).hexdigest()


def _covers(iv: PlanIntervention, student: StudentKey) -> bool:
    return iv.profile in ("all", student.profile)


def _family_maps(student: StudentKey, families: dict[str, SkillFamily], course_grade: int):
    return [(e.family, family_levels(families[e.family], e.secure_through, e.at_grade_level, course_grade))
            for e in student.families]


def build_students(plan: CoursePlan, families: dict[str, SkillFamily], reviewable: set[str], seed: int,
                   course_grade: int) -> list[StudentKey]:
    """Paired students without hashes, round-robin over profiles in plan order (p01, p02, ...)."""
    rng = random.Random(seed)
    pool = sorted(c for c in reviewable
                  if c.split(".")[1] in QUIRK_STRANDS and 3 <= grade_key(c) <= course_grade)
    rounds = max((p.count for p in plan.profiles), default=0)
    order = [(p, i) for i in range(rounds) for p in plan.profiles if i < p.count]
    nr = plan.non_responder
    nr_profile = next((iv.profile for iv in plan.interventions if nr and iv.id == nr.intervention), None)
    out = []
    for n, (profile, i) in enumerate(order, start=1):
        entries = [e.model_copy(update={"secure_through": min(8, max(2, e.secure_through
                                                                    + rng.choice([-1, 0, 1])))})
                   if profile.jitter else e for e in profile.families]
        own = {c for e in profile.families for c in families[e.family].codes}
        picked = rng.sample([c for c in pool if c not in own], profile.quirks[i])
        student = StudentKey(
            pair_id=f"p{n:02d}", profile=profile.name, families=entries,
            quirks=[StandardLevel(standard=c, level=1) for c in sorted(picked)], baseline={},
            start_week=profile.start_week)
        student.baseline = dict(sorted(combine(*(m for _, m in _family_maps(student, families, course_grade)),
                                               {q.standard: q.level for q in student.quirks}).items()))
        out.append(student)
    if nr_profile is not None:
        covered = [s for s in out if nr_profile in ("all", s.profile)]
        covered[nr.index].non_responder = True
        covered[nr.index].non_responder_iv = nr.intervention
    return out


def levels_at(student: StudentKey, week: int, interventions: list[PlanIntervention],
              families: dict[str, SkillFamily], course_grade: int) -> dict[str, int]:
    """Levels in `week` (spec 4.5): each covering intervention lowers its families' levels by
    floor((week - iv.week) / rate), never below 0; the non-responder is exempt from
    `student.non_responder_iv`."""
    quirks = {q.standard: q.level for q in student.quirks}
    if week < student.start_week:
        return quirks
    maps = []
    for family, levels in _family_maps(student, families, course_grade):
        drop = sum((week - iv.week) // iv.rate for iv in interventions
                   if family in iv.families and week > iv.week and _covers(iv, student)
                   and not (student.non_responder and iv.id == student.non_responder_iv))
        maps.append({c: lv - drop for c, lv in levels.items() if lv > drop})
    return combine(*maps, quirks)


def validate_codes(plan: CoursePlan, families: dict[str, SkillFamily], curriculum_dir: Path | None,
                   reviewable: set[str]) -> list[str]:
    """Every family, target and item/distractor code the plan or curriculum uses that the catalog lacks.

    Unknown family names come back as `family:<name>`. The curriculum is skipped when its folder is absent."""
    names = {e.family for p in plan.profiles for e in p.families} | {f for iv in plan.interventions
                                                                     for f in iv.families}
    missing = {f"family:{n}" for n in names if n not in families}
    codes = {c for n in names if n in families for c in families[n].codes}
    assignments = [iv.assignment for iv in plan.interventions if iv.assignment]
    if curriculum_dir is not None and (Path(curriculum_dir) / "curriculum.json").exists():
        cur = read_json(Path(curriculum_dir) / "curriculum.json", Curriculum)
        assignments += [read_json(Path(curriculum_dir) / "assignments" / f"{a}.json", AssignmentFile)
                        for a in cur.assignments]
    for a in assignments:
        codes |= set(a.targets)
        for item in a.items:
            codes |= {item.standard} | {c.standard for c in item.choices if c.standard}
    return sorted(missing | (codes - reviewable))


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def _json(data) -> str:
    return json.dumps(data, indent=1, ensure_ascii=False) + "\n"


def _jsonl(models) -> str:
    return "".join(m.model_dump_json() + "\n" for m in models)


def build_course(plan: CoursePlan, data_dir: Path, seed: int) -> None:
    """Write both twin course folders (course, roster, interventions, intervention assignments, answer key)."""
    data_dir = Path(data_dir)
    curriculum = read_json(curriculum_path(data_dir, plan.curriculum) / "curriculum.json", Curriculum)
    grade = curriculum.grade
    families = load_families(data_dir / "skill_families.json")
    paired = build_students(plan, families, reviewable_codes(REVIEWABILITY_CSV), seed, grade)
    for arm in ARMS:
        version = f"{plan.version}-{arm}"
        twin = f"{plan.version}-{ARMS[1 - ARMS.index(arm)]}"
        path = course_path(data_dir, version)
        students = [s.model_copy(update={"student_hash": student_hash(arm, s.pair_id, seed),
                                         "non_responder": s.non_responder and arm == "intervention",
                                         "non_responder_iv": s.non_responder_iv if arm == "intervention"
                                         else None})
                    for s in paired]
        ivs = plan.interventions if arm == "intervention" else []
        meta = plan.courses[arm]
        course = CourseFile(id=f"classroom_{version}", name=meta.name, grade=grade, subject="ela",
                            teacher_id=plan.teacher_id, period=meta.period, start_date=plan.start_date, arm=arm,
                            curriculum=plan.curriculum, twin=twin)
        _write(path / "course.json", _json(course.model_dump(mode="json")))
        _write(path / "roster.jsonl", "".join(json.dumps({"student_hash": s.student_hash}) + "\n"
                                              for s in students))
        rows = []
        for iv in ivs:
            hashes = [s.student_hash for s in students if _covers(iv, s)]
            standards = list(dict.fromkeys(c for f in iv.families for c in families[f].codes
                                           if grade_key(c) <= grade))
            rows.append(InterventionRow(
                id=iv.id, week=iv.week, kind=iv.kind, students="all" if iv.profile == "all" else hashes,
                families=iv.families, standards=standards,
                assignment_id=iv.assignment.id if iv.assignment else None, description=iv.description))
            if iv.assignment:
                assignment = iv.assignment.model_copy(update={"assigned_to": hashes})
                _write(path / "assignments" / f"{assignment.id}.json", _json(assignment.model_dump(mode="json")))
        _write(path / "interventions.json", _json([r.model_dump(mode="json") for r in rows]))
        key = answer_key_path(data_dir, version)
        _write(key / "students.jsonl", _jsonl(students))
        _write(key / "trajectory.jsonl", _jsonl(
            TrajectoryRow(student_hash=s.student_hash, week=w, standard=code, level=level)
            for s in students for w in WEEKS
            for code, level in sorted(levels_at(s, w, ivs, families, grade).items()) if level > 0))

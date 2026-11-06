import json
import shutil
from collections import defaultdict
from pathlib import Path

import pytest

from agentic.coursedata import CourseDir
from agentic_lab.answer_key import FamilyEntry, StudentKey, answer_key_path
from agentic_lab.config import SYNTHETIC_DIR
from agentic_lab.standards_v2 import REVIEWABILITY_CSV, reviewable_codes
from agentic_lab.synth.families import load_families
from agentic_lab.synth.students import (
    CoursePlan, build_course, build_students, levels_at, load_plan, student_hash, validate_codes)

FIXTURE = Path(__file__).resolve().parents[1] / "data" / "coursedir"
REAL_PLAN = SYNTHETIC_DIR / "plans" / "ela9-v2.json"
FAMILIES = load_families(SYNTHETIC_DIR / "skill_families.json")
REVIEWABLE = reviewable_codes(REVIEWABILITY_CSV)
WEAK = "Strong reader, weak conventions"
SHALLOW = "Fluent writer, shallow analysis"


def _plan(**over) -> CoursePlan:
    raw = {
        "curriculum": "ela9-fixture", "version": "0.95", "start_date": "2027-01-11", "teacher_id": "teacher-test",
        "courses": {"control": {"name": "Test 1", "period": 1}, "intervention": {"name": "Test 2", "period": 2}},
        "profiles": [
            {"name": "On track", "count": 2, "families": [], "quirks": [0, 1]},
            {"name": WEAK, "count": 2, "quirks": [1, 1],
             "families": [{"family": "agreement", "secure_through": 2, "at_grade_level": 3},
                          {"family": "sentence_boundaries", "secure_through": 4, "at_grade_level": 2}]},
            {"name": SHALLOW, "count": 2, "quirks": [1, 1], "jitter": False,
             "families": [{"family": "evidence", "secure_through": 7, "at_grade_level": 2}]},
            {"name": "Foundational gaps", "count": 2, "quirks": [0, 0],
             "families": [{"family": "mechanics", "secure_through": 4, "at_grade_level": 1}]},
            {"name": "Vocabulary", "count": 2, "quirks": [1, 1],
             "families": [{"family": "vocabulary", "secure_through": 4, "at_grade_level": 2}]},
            {"name": "Regressing", "count": 2, "quirks": [1, 0], "start_week": 7, "jitter": False,
             "families": [{"family": "organization", "secure_through": 8, "at_grade_level": 2}]},
        ],
        "interventions": [
            {"id": "iv1", "week": 4, "kind": "class_lesson", "profile": "all",
             "families": ["sentence_boundaries"], "rate": 2, "description": "Mini-lesson."},
            {"id": "iv2", "week": 4, "kind": "individual", "profile": WEAK, "families": ["agreement"], "rate": 1,
             "description": "Conferences.",
             "assignment": {"id": "ela9v2-i01", "module": "rl", "week": 4, "kind": "intervention",
                            "type": "paragraph", "title": "Revise", "prompt": "Revise your paragraph.",
                            "text_id": "fixture-story", "due_offset": {"week": 4, "day": "fri"},
                            "targets": ["9-10.W.3.A.c"]}},
            {"id": "iv3", "week": 8, "kind": "small_group", "profile": SHALLOW, "families": ["evidence"],
             "rate": 1, "description": "Small group."},
        ],
        "non_responder": {"intervention": "iv2", "index": 0},
    }
    raw.update(over)
    return CoursePlan.model_validate(raw)


def _students(plan=None, seed=2027):
    return build_students(plan or _plan(), FAMILIES, REVIEWABLE, seed, course_grade=9)


def _data_dir(tmp_path) -> Path:
    shutil.copytree(FIXTURE, tmp_path, dirs_exist_ok=True)
    shutil.copy(SYNTHETIC_DIR / "skill_families.json", tmp_path / "skill_families.json")
    return tmp_path


def _keys(data_dir, version):
    lines = (answer_key_path(data_dir, version) / "students.jsonl").read_text(encoding="utf-8").splitlines()
    return [StudentKey.model_validate_json(x) for x in lines]


def _weak(non_responder=False) -> StudentKey:
    return StudentKey(pair_id="p02", profile=WEAK, quirks=[], baseline={}, start_week=1,
                      non_responder=non_responder, non_responder_iv="iv2" if non_responder else None,
                      families=[FamilyEntry(family="agreement", secure_through=2, at_grade_level=3),
                                FamilyEntry(family="sentence_boundaries", secure_through=4, at_grade_level=2)])


def test_twins_identical_except_hash(tmp_path):
    data = _data_dir(tmp_path)
    build_course(_plan(), data, 2027)
    control, iv = _keys(data, "0.95-control"), _keys(data, "0.95-intervention")
    assert len(control) == len(iv) == 12
    for c, i in zip(control, iv):
        assert (c.pair_id, c.profile, c.families, c.quirks, c.baseline) == \
               (i.pair_id, i.profile, i.families, i.quirks, i.baseline)
        assert c.student_hash != i.student_hash
        assert c.student_hash == student_hash("control", c.pair_id, 2027)


def test_first_six_one_per_profile():
    students = _students()
    assert [s.profile for s in students[:6]] == [p.name for p in _plan().profiles]
    assert [s.pair_id for s in students[:3]] == ["p01", "p02", "p03"]


def test_quirks_outside_own_families_and_jitter_off():
    for s in _students():
        own = {c for e in s.families for c in FAMILIES[e.family].codes}
        assert not own & {q.standard for q in s.quirks}
        assert all(q.level == 1 and q.standard.split(".")[1] in ("W", "L") for q in s.quirks)
        if s.profile in (SHALLOW, "Regressing"):
            assert s.families == [e for p in _plan().profiles if p.name == s.profile for e in p.families]


def test_iv2_responder_spec_trajectory():
    plan = _plan()

    def lv(code, week):
        return levels_at(_weak(), week, plan.interventions, FAMILIES, 9).get(code, 0)

    assert [lv("3.L.1.A.e", w) for w in (4, 5)] == [1, 0]
    assert [lv("9-10.W.3.A.c", w) for w in (4, 5, 6, 7, 8)] == [3, 2, 1, 1, 0]


def test_non_responder_agreement_unchanged():
    plan = _plan()
    before = levels_at(_weak(True), 4, plan.interventions, FAMILIES, 9)
    after = levels_at(_weak(True), 11, plan.interventions, FAMILIES, 9)
    only_agreement = set(FAMILIES["agreement"].codes) - set(FAMILIES["sentence_boundaries"].codes)
    assert {c: after.get(c, 0) for c in only_agreement} == {c: before.get(c, 0) for c in only_agreement}
    assert after["3.L.1.A.e"] == before["3.L.1.A.e"] == 1
    assert after["9-10.W.3.A.c"] == 3
    marked = [s for s in _students() if s.non_responder]
    assert [(s.profile, s.pair_id, s.non_responder_iv) for s in marked] == [(WEAK, "p02", "iv2")]


def test_build_course_non_responder_trajectory(tmp_path):
    data = _data_dir(tmp_path)
    plan = _plan()
    plan.profiles[1].jitter = False
    build_course(plan, data, 2027)
    keys = _keys(data, "0.95-intervention")
    nr = next(k for k in keys if k.non_responder)
    responder = next(k for k in keys if k.profile == WEAK and not k.non_responder)
    assert nr.non_responder_iv == "iv2" and responder.non_responder_iv is None
    assert not any(k.non_responder or k.non_responder_iv for k in _keys(data, "0.95-control"))
    levels = defaultdict(int)
    for line in (answer_key_path(data, "0.95-intervention") / "trajectory.jsonl").read_text("utf-8").splitlines():
        row = json.loads(line)
        levels[row["student_hash"], row["week"], row["standard"]] = row["level"]
    code = "3.L.1.A.e"
    assert levels[nr.student_hash, 4, code] == levels[nr.student_hash, 11, code] == 1
    assert levels[responder.student_hash, 4, code] == 1
    assert levels[responder.student_hash, 11, code] == 0


def test_control_has_no_interventions_and_levels_constant(tmp_path):
    data = _data_dir(tmp_path)
    build_course(_plan(), data, 2027)
    control = CourseDir(data, "0.95-control")
    assert control.interventions() == []
    assert not (control.path / "assignments").exists()
    by_student = defaultdict(lambda: defaultdict(dict))
    keys = {k.student_hash: k for k in _keys(data, "0.95-control")}
    for line in (answer_key_path(data, "0.95-control") / "trajectory.jsonl").read_text("utf-8").splitlines():
        row = json.loads(line)
        by_student[row["student_hash"]][row["week"]][row["standard"]] = row["level"]
    for h, weeks in by_student.items():
        if keys[h].start_week == 1:
            assert sorted(weeks) == list(range(1, 12))
            assert all(weeks[w] == weeks[1] == keys[h].baseline for w in weeks)


def test_intervention_files(tmp_path):
    data = _data_dir(tmp_path)
    build_course(_plan(), data, 2027)
    course = CourseDir(data, "0.95-intervention")
    keys = _keys(data, "0.95-intervention")
    weak = [k.student_hash for k in keys if k.profile == WEAK]
    ivs = {r.id: r for r in course.interventions()}
    assert ivs["iv1"].students == "all" and ivs["iv1"].assignment_id is None
    assert ivs["iv2"].students == weak and ivs["iv2"].assignment_id == "ela9v2-i01"
    assert "9-10.W.3.A.c" in ivs["iv2"].standards
    assert not any(c.startswith("11-12") for r in ivs.values() for c in r.standards)
    i01 = course.assignment("ela9v2-i01")
    assert i01.assigned_to == weak and i01.kind == "intervention"
    c = course.course()
    assert (c.id, c.arm, c.twin, c.grade, c.period) == ("classroom_0.95-intervention", "intervention",
                                                        "0.95-control", 9, 2)
    assert [r.student_hash for r in course.roster()] == [k.student_hash for k in keys]


def test_regressing_quirks_only_before_week_7():
    plan = _plan()
    reg = [s for s in _students() if s.profile == "Regressing"]
    for s in reg:
        quirks = {q.standard: q.level for q in s.quirks}
        for w in range(1, 7):
            assert levels_at(s, w, plan.interventions, FAMILIES, 9) == quirks
        assert "9-10.W.3.A.a" in levels_at(s, 7, plan.interventions, FAMILIES, 9)
        assert set(s.baseline) > set(quirks)


def test_validate_codes_lists_missing():
    plan = _plan()
    curriculum = FIXTURE / "curricula" / "ela9-fixture"
    assert validate_codes(plan, FAMILIES, curriculum, REVIEWABLE) == []
    plan.interventions[1].assignment.targets.append("9-10.W.99.Z")
    assert validate_codes(plan, FAMILIES, curriculum, REVIEWABLE - {"5.R.1.B.b"}) == ["5.R.1.B.b", "9-10.W.99.Z"]
    assert validate_codes(plan, FAMILIES, None, REVIEWABLE - {"3.L.1.A.e"}) == ["3.L.1.A.e", "9-10.W.99.Z"]


def test_build_course_is_deterministic(tmp_path):
    outs = []
    for name in ("a", "b"):
        data = _data_dir(tmp_path / name)
        build_course(_plan(), data, 2027)
        outs.append({p.relative_to(data / "courses").as_posix(): p.read_bytes()
                     for p in sorted((data / "courses").rglob("*")) if p.is_file()})
    assert outs[0] == outs[1]
    assert b"\r\n" not in b"".join(outs[0].values())


def test_cli_build_course_reports_missing_codes(tmp_path, monkeypatch, capsys):
    from agentic_lab.__main__ import main
    data = _data_dir(tmp_path / "data")
    plan = _plan()
    plan.interventions[1].assignment.targets.append("9-10.W.99.Z")
    path = tmp_path / "plan.json"
    path.write_text(plan.model_dump_json(), encoding="utf-8")
    monkeypatch.setenv("DATA_DIR", str(data))
    assert main(["build-course", "--plan", str(path)]) == 2
    assert "9-10.W.99.Z" in capsys.readouterr().err
    assert not (data / "courses").exists()


def test_cli_build_course_writes_both_arms(tmp_path, monkeypatch):
    from agentic_lab.__main__ import main
    data = _data_dir(tmp_path / "data")
    path = tmp_path / "plan.json"
    path.write_text(_plan().model_dump_json(), encoding="utf-8")
    monkeypatch.setenv("DATA_DIR", str(data))
    assert main(["build-course", "--plan", str(path), "--seed", "7"]) == 0
    assert _keys(data, "0.95-control")[0].student_hash == student_hash("control", "p01", 7)


def test_real_plan_loads():
    plan = load_plan(REAL_PLAN)
    assert sum(p.count for p in plan.profiles) == 18
    assert [p.count for p in plan.profiles] == [4, 4, 3, 3, 2, 2]
    assert {p.name for p in plan.profiles if not p.jitter} == {SHALLOW, "Regressing"}
    assert [(i.id, i.week, i.kind, i.rate) for i in plan.interventions] == [
        ("iv1", 4, "class_lesson", 2), ("iv2", 4, "individual", 1), ("iv3", 8, "small_group", 1)]
    assert [i.assignment.id for i in plan.interventions if i.assignment] == ["ela9v2-i01", "ela9v2-i02"]

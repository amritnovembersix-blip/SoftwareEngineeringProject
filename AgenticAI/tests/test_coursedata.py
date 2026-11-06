import builtins
import json
import shutil
from datetime import date, datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from agentic.coursedata import (
    AssignmentFile, Choice, CourseDir, DueOffset, SubmissionRow, course_path, curriculum_path,
    due_at, file_assignment_id, lms_assignment_id)

DATA = Path(__file__).parent / "data" / "coursedir"
CONTROL, INTERV = "0.90-control", "0.90-intervention"


def _asg(**over) -> dict:
    base = {"id": "x-a1", "module": "rl", "week": 1, "kind": "regular", "type": "multiple_choice",
            "title": "t", "prompt": "p", "text_id": "tx", "due_offset": {"week": 1, "day": "mon"},
            "assigned_to": None, "items": [{"id": "q1", "standard": "s", "stem": "?", "choices": [
                {"key": "A", "text": "a", "correct": True},
                {"key": "B", "text": "b", "misconception": "m", "standard": "s"}]}]}
    return {**base, **over}


def test_due_at_a02():
    assert due_at(date(2027, 1, 11), DueOffset(week=1, day="fri")) == datetime(
        2027, 1, 16, 5, 59, tzinfo=timezone.utc)
    assert due_at(date(2027, 1, 11), DueOffset(week=3, day="wed")) == datetime(
        2027, 1, 28, 5, 59, tzinfo=timezone.utc)


def test_extra_text_ids_default_and_declared():
    assert AssignmentFile.model_validate(_asg()).extra_text_ids == []
    a = AssignmentFile.model_validate(_asg(extra_text_ids=["ty", "tz"]))
    assert a.extra_text_ids == ["ty", "tz"]


def test_id_round_trip():
    lms = lms_assignment_id("ela9v2-a05", "2.00-control")
    assert lms == "ela9v2-a05@2.00-control"
    assert file_assignment_id(lms) == ("ela9v2-a05", "2.00-control")
    with pytest.raises(ValueError):
        file_assignment_id("ela9v2-a05")


def test_paths():
    assert course_path(DATA, CONTROL) == DATA / "courses" / CONTROL
    assert curriculum_path(DATA, "ela9-test") == DATA / "curricula" / "ela9-test"


def test_course_and_curriculum():
    cd = CourseDir(DATA, CONTROL)
    c = cd.course()
    assert (c.id, c.arm, c.twin, c.start_date) == ("classroom_0.90-control", "control",
                                                   "0.90-intervention", date(2027, 1, 11))
    assert cd.curriculum().assignments == ["ela9v2-a01", "ela9v2-a02", "ela9v2-a03"]
    assert len(cd.roster()) == 2 and cd.interventions() == []


def test_assignments_in_order_with_own():
    assert [a.type for a in CourseDir(DATA, CONTROL).assignments()] == [
        "paragraph", "short_answer", "multiple_choice"]
    ids = [a.id for a in CourseDir(DATA, INTERV).assignments()]
    assert ids == ["ela9v2-a01", "ela9v2-a02", "ela9v2-a03", "ela9v2-i01"]
    i01 = CourseDir(DATA, INTERV).assignment("ela9v2-i01")
    assert i01.kind == "intervention" and len(i01.assigned_to) == 1
    with pytest.raises(LookupError):
        CourseDir(DATA, CONTROL).assignment("nope")


def test_interventions_and_submissions():
    iv = CourseDir(DATA, INTERV)
    assert iv.interventions()[0].students == ["c3c3c3c3c3c3c3c3"]
    assert [s.course_id for s in iv.submissions("ela9v2-i01")] == ["classroom_0.90-intervention"]
    assert iv.submissions("ela9v2-a01") == []  # no file: nobody submitted
    mc = CourseDir(DATA, CONTROL).submissions("ela9v2-a03")
    assert [r.answers[0].choice for r in mc] == ["A", "B"] and mc[0].paragraphs is None


def test_text():
    assert "synthetic fox" in CourseDir(DATA, CONTROL).text("test-story")


def test_mc_two_correct_names_file(tmp_path):
    root = tmp_path / "d"
    shutil.copytree(DATA, root)
    bad = _asg(id="ela9v2-a03")
    bad["items"][0]["choices"][1] = {"key": "B", "text": "b", "correct": True}
    (root / "curricula/ela9-test/assignments/ela9v2-a03.json").write_text(
        json.dumps(bad), encoding="utf-8")
    with pytest.raises(ValueError, match=r"ela9v2-a03\.json"):
        CourseDir(root, CONTROL).assignment("ela9v2-a03")


def test_assignment_validators():
    AssignmentFile.model_validate(_asg())
    with pytest.raises(ValidationError):
        AssignmentFile.model_validate(_asg(type="essay"))  # essay has no items
    with pytest.raises(ValidationError):
        AssignmentFile.model_validate(_asg(type="short_answer", items=[]))
    with pytest.raises(ValidationError):
        AssignmentFile.model_validate(_asg(extra_field=1))
    with pytest.raises(ValidationError):
        Choice(key="B", text="b")  # not correct, no misconception/standard


def test_submission_needs_exactly_one_body():
    base = dict(student_hash="h", submission_id="s", attempt=1, assignment_id="a", course_id="c",
                submitted_at="2027-01-15T18:00:00Z")
    with pytest.raises(ValidationError):
        SubmissionRow(**base)
    with pytest.raises(ValidationError):
        SubmissionRow(**base, paragraphs=["x"], answers=[{"item_id": "q1", "text": "y"}])
    assert SubmissionRow(**base, paragraphs=["x"]).answers is None


def test_never_opens_answer_key(monkeypatch):
    seen: list[str] = []
    real_open, real_read = builtins.open, Path.read_text
    monkeypatch.setattr(builtins, "open", lambda f, *a, **k: (seen.append(str(f)), real_open(f, *a, **k))[1])
    real_popen = Path.open
    monkeypatch.setattr(Path, "open", lambda self, *a, **k: (seen.append(str(self)), real_popen(self, *a, **k))[1])
    monkeypatch.setattr(Path, "read_text", lambda self, *a, **k: (seen.append(str(self)), real_read(self, *a, **k))[1])
    for v in (CONTROL, INTERV):
        cd = CourseDir(DATA, v)
        cd.course(), cd.curriculum(), cd.roster(), cd.interventions(), cd.text("test-story")
        for a in cd.assignments():
            cd.submissions(a.id)
    assert seen and not any("answer_key" in p for p in seen)


def test_invalid_file_error_never_echoes_content(tmp_path):
    root = tmp_path / "d"
    shutil.copytree(DATA, root)
    (root / "courses" / CONTROL / "course.json").write_text('{"id": "SECRET-TEXT"}', encoding="utf-8")
    with pytest.raises(ValueError) as err:
        CourseDir(root, CONTROL).course()
    assert "course.json" in str(err.value) and "SECRET-TEXT" not in str(err.value)

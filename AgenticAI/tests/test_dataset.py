import pytest
from fakes import essay_row, label_row, write_essays, write_labels

from agentic import dataset


def test_round_trip_essays_and_ids(tmp_path):
    write_labels(tmp_path, "1.00", [label_row()])
    write_essays(tmp_path, "1.00", [essay_row(paragraphs=["x", "y"])])
    essays = dataset.load_essays(tmp_path, "1.00")
    assert essays[0].paragraphs == ["x", "y"] and essays[0].attempt == 1
    assert essays[0].submitted_at.year == 2026
    assert dataset.classroom_grade(tmp_path, "1.00") == 9
    assert dataset.course_id("1.00") == "classroom_1.00"
    assert dataset.assignment_id("1.00") == "asg-classroom_1.00-essay1"


def test_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError, match="classroom_9.99"):
        dataset.load_essays(tmp_path, "9.99")


def test_classroom_grade_mismatch_raises(tmp_path):
    write_labels(tmp_path, "1.00", [label_row("a" * 64, grade=9), label_row("b" * 64, grade=10)])
    with pytest.raises(ValueError):
        dataset.classroom_grade(tmp_path, "1.00")


def test_resolve_student_unique_prefix(tmp_path):
    write_essays(tmp_path, "1.00", [essay_row("abc1" + "0" * 60), essay_row("def2" + "0" * 60)])
    essays = dataset.load_essays(tmp_path, "1.00")
    assert dataset.resolve_student(essays, "abc").student_hash.startswith("abc1")


def test_resolve_student_unknown_and_ambiguous(tmp_path):
    write_essays(tmp_path, "1.00", [essay_row("abc1" + "0" * 60), essay_row("abc2" + "0" * 60)])
    essays = dataset.load_essays(tmp_path, "1.00")
    with pytest.raises(LookupError, match="no student matches"):
        dataset.resolve_student(essays, "zzz")
    with pytest.raises(LookupError, match="ambiguous: 2 students match"):
        dataset.resolve_student(essays, "abc")


def _assert_clean(exc_info):
    e = exc_info.value
    assert "SECRET ESSAY" not in str(e) and "SECRET ESSAY" not in repr(e)
    assert e.__cause__ is None and e.__context__ is None


def test_invalid_essay_row_error_has_no_essay_text(tmp_path):
    row = essay_row(paragraphs=["SECRET ESSAY text"])
    del row["attempt"]
    write_essays(tmp_path, "1.00", [row])
    with pytest.raises(ValueError, match="attempt") as ei:
        dataset.load_essays(tmp_path, "1.00")
    _assert_clean(ei)


def test_malformed_json_error_has_no_essay_text(tmp_path):
    path = dataset.essays_path(tmp_path, "1.00")
    path.write_text('{"paragraphs": ["SECRET ESSAY" oops\n', encoding="utf-8")
    with pytest.raises(ValueError, match="malformed") as ei:
        dataset.load_essays(tmp_path, "1.00")
    _assert_clean(ei)


# --- classroom assignment info (assignment type + review strands) ---------------------------

from agentic.dataset import AssignmentInfo, assignment_path, load_assignment  # noqa: E402


def test_missing_assignment_file_means_generic_argument(tmp_path):
    a = load_assignment(tmp_path, "1.00")
    assert a == AssignmentInfo(type="argument", text=None, strands=["W"])


def test_assignment_strands_default_from_type(tmp_path):
    assignment_path(tmp_path, "1.02").write_text('{"type": "rl", "text": "Animal Farm"}', encoding="utf-8")
    a = load_assignment(tmp_path, "1.02")
    assert (a.type, a.text, a.strands) == ("rl", "Animal Farm", ["W", "RL"])


def test_assignment_explicit_strands_always_include_writing(tmp_path):
    assignment_path(tmp_path, "1.03").write_text('{"type": "ri", "strands": ["ri"]}', encoding="utf-8")
    assert load_assignment(tmp_path, "1.03").strands == ["W", "RI"]


def test_assignment_invalid_type_is_content_free_error(tmp_path):
    assignment_path(tmp_path, "1.04").write_text('{"type": "poem SECRET"}', encoding="utf-8")
    with pytest.raises(ValueError) as e:
        load_assignment(tmp_path, "1.04")
    assert "SECRET" not in str(e.value) and "classroom_1.04.assignment.json" in str(e.value)


def test_assignment_v2_fields_round_trip(tmp_path):
    assignment_path(tmp_path, "1.01").write_text(
        '{"id": "rl-theme", "type": "rl", "title": "Theme", "prompt": "Choose ONE theme.",'
        ' "targets": [" 9-10.RL.1.A", "9-10.RL.1.D", "9-10.RL.1.A"]}', encoding="utf-8")
    a = load_assignment(tmp_path, "1.01")
    assert (a.id, a.title, a.prompt) == ("rl-theme", "Theme", "Choose ONE theme.")
    assert a.targets == ["9-10.RL.1.A", "9-10.RL.1.D"]


def test_missing_assignment_has_no_targets(tmp_path):
    a = load_assignment(tmp_path, "1.00")
    assert a.targets == [] and a.prompt is None and a.id == "argument"


def test_assignment_notes_round_trip(tmp_path):
    assignment_path(tmp_path, "1.06").write_text(
        '{"type": "rl", "targets": ["9-10.RL.1.D"], "notes": {" 9-10.RL.1.D ": "One theme only."}}',
        encoding="utf-8")
    assert load_assignment(tmp_path, "1.06").notes == {"9-10.RL.1.D": "One theme only."}

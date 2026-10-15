from fakes import essay_row, label_row, write_essays, write_labels

from agentic.lms.base import Course, LMSClient
from agentic.lms.jsonl_client import JsonlLMSClient
from agentic.review.nodes import html_to_paragraphs

PARAS = ['a < b & "c" it\'s', "Second <b>not bold</b> & done."]


def _client(tmp_path):
    write_labels(tmp_path, "1.00", [label_row("a" * 64, grade=9, meets_all_standards=False,
                 underdeveloped=[{"standard": "s", "standard_grade": "9-10", "level": 1,
                                  "level_label": "L", "strand": "W", "concept": "c", "description": "d"}])])
    write_essays(tmp_path, "1.00", [essay_row("a" * 64, paragraphs=PARAS)])
    return JsonlLMSClient(tmp_path, "1.00")


def test_file_lms_course_and_submission(tmp_path):
    client = _client(tmp_path)
    assert isinstance(client, LMSClient)
    course = client.get_course("classroom_1.00")
    assert course.grade_level == 9 and course.subject == "ela" and course.name == "Classroom 1.00"
    sub = client.get_submission("classroom_1.00", "asg-classroom_1.00-essay1", "a" * 64)
    assert sub.user_id == "a" * 64 and sub.attempt == 1 and sub.id == "sub-1"
    assert html_to_paragraphs(sub.body) == PARAS


def test_course_has_no_label_data():
    assert "underdeveloped" not in Course.model_fields


def test_get_assignment_maps_title_and_prompt(tmp_path):
    import pytest
    from agentic import dataset

    write_labels(tmp_path, "1.01", [label_row(grade=9)])
    dataset.assignment_path(tmp_path, "1.01").write_text(
        '{"id": "rl-theme", "type": "rl", "title": "Theme in Animal Farm", "prompt": "Choose ONE theme."}',
        encoding="utf-8")
    c = JsonlLMSClient(tmp_path, "1.01")
    a = c.get_assignment("classroom_1.01", "asg-classroom_1.01-essay1")
    assert (a.id, a.name, a.description) == ("asg-classroom_1.01-essay1", "Theme in Animal Farm", "Choose ONE theme.")
    with pytest.raises(LookupError):
        c.get_assignment("classroom_9.99", "x")


def test_get_assignment_without_file_uses_id_as_name(tmp_path):
    write_labels(tmp_path, "1.00", [label_row(grade=9)])
    a = JsonlLMSClient(tmp_path, "1.00").get_assignment("classroom_1.00", "asg-classroom_1.00-essay1")
    assert a.name == "argument" and a.description is None


def test_get_assignment_carries_notes(tmp_path):
    from agentic import dataset

    write_labels(tmp_path, "1.01", [label_row(grade=9)])
    dataset.assignment_path(tmp_path, "1.01").write_text(
        '{"prompt": "P", "notes": {"9-10.RL.1.D": "One theme only."}}', encoding="utf-8")
    a = JsonlLMSClient(tmp_path, "1.01").get_assignment("classroom_1.01", "x")
    assert a.notes == {"9-10.RL.1.D": "One theme only."}


def test_list_submissions_latest_attempt_per_student(tmp_path):
    from agentic import dataset

    write_labels(tmp_path, "1.01", [label_row("a" * 64), label_row("b" * 64)])
    common = {"course_id": "classroom_1.01", "assignment_id": "asg-1"}
    write_essays(tmp_path, "1.01", [
        essay_row("a" * 64, submission_id="s-a", attempt=1, **common),
        essay_row("a" * 64, submission_id="s-a", attempt=2, **common),
        essay_row("b" * 64, submission_id="s-b", attempt=1, **common),
        essay_row("b" * 64, submission_id="s-b2", attempt=1, course_id="classroom_1.01", assignment_id="asg-2"),
    ])
    c = JsonlLMSClient(tmp_path, "1.01")
    subs = c.list_submissions("classroom_1.01")
    assert [(x.id, x.attempt) for x in subs] == [("s-a", 2), ("s-b", 1), ("s-b2", 1)]
    assert [x.id for x in c.list_submissions("classroom_1.01", "asg-2")] == ["s-b2"]
    assert dataset.list_versions(tmp_path) == ["1.01"]


def test_list_submissions_unknown_course(tmp_path):
    import pytest

    with pytest.raises(LookupError):
        _client(tmp_path).list_submissions("classroom_x")

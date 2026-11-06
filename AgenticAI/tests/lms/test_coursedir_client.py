from datetime import datetime, timezone
from pathlib import Path

import pytest

from agentic.lms.base import LMSClient
from agentic.lms.coursedir_client import CourseDirLMSClient
from agentic.review.nodes import html_to_paragraphs

DATA = Path(__file__).resolve().parents[1] / "data" / "coursedir"
CTRL, INTV = "0.90-control", "0.90-intervention"
A = "a1a1a1a1a1a1a1a1"
B = "b2b2b2b2b2b2b2b2"
C = "c3c3c3c3c3c3c3c3"
D = "d4d4d4d4d4d4d4d4"


def ctrl():
    return CourseDirLMSClient(DATA, CTRL), f"classroom_{CTRL}"


def intv():
    return CourseDirLMSClient(DATA, INTV), f"classroom_{INTV}"


def test_is_lms_client_and_course():
    c, cid = ctrl()
    assert isinstance(c, LMSClient)
    course = c.get_course(cid)
    assert (course.id, course.subject, course.grade_level) == (cid, "ela", 9)
    assert course.name


def test_paragraph_assignment_and_body():
    c, cid = ctrl()
    a = c.get_assignment(cid, f"ela9v2-a01@{CTRL}")
    assert a.id == f"ela9v2-a01@{CTRL}" and a.submission_type == "paragraph"
    assert a.due_at is not None and a.due_at.tzinfo is not None and a.items == []
    s = c.get_submission(cid, f"ela9v2-a01@{CTRL}", A)
    assert s.assignment_id == f"ela9v2-a01@{CTRL}" and s.answers == []
    assert html_to_paragraphs(s.body) == ["The fox found a key. It was synthetic."]


def test_due_date_pinned_utc():
    c, cid = ctrl()
    a = c.get_assignment(cid, f"ela9v2-a02@{CTRL}")
    assert a.due_at == datetime(2027, 1, 21, 5, 59, tzinfo=timezone.utc)  # Wed wk2 23:59 CST


def test_short_answer_body_and_answers():
    c, cid = ctrl()
    a = c.get_assignment(cid, f"ela9v2-a02@{CTRL}")
    assert a.submission_type == "short_answer" and a.items[0]["id"] == "q1"
    s = c.get_submission(cid, f"ela9v2-a02@{CTRL}", A)
    assert s.body == "<p>q1: A key.</p>"
    assert s.answers == [{"item_id": "q1", "text": "A key."}]


def test_multiple_choice_body_answers_items():
    c, cid = ctrl()
    a = c.get_assignment(cid, f"ela9v2-a03@{CTRL}")
    assert a.submission_type == "multiple_choice"
    assert a.items[0]["choices"][1]["misconception"]
    s = c.get_submission(cid, f"ela9v2-a03@{CTRL}", B)
    assert s.body == "" and s.answers == [{"item_id": "q1", "choice": "B"}]


def test_assignment_carries_prompt_and_title():
    c, cid = ctrl()
    a = c.get_assignment(cid, f"ela9v2-a03@{CTRL}")
    assert a.name == "Test multiple choice" and a.description == "Pick one answer."


def test_list_submissions_sorted_and_scoped():
    c, cid = ctrl()
    rows = c.list_submissions(cid)
    assert len(rows) == 6
    keys = [(s.assignment_id, s.user_id) for s in rows]
    assert keys == sorted(keys) and all(k[0].endswith(f"@{CTRL}") for k in keys)
    one = c.list_submissions(cid, f"ela9v2-a02@{CTRL}")
    assert [s.user_id for s in one] == [A, B]


def test_intervention_assignment_partially_assigned():
    c, cid = intv()
    rows = c.list_submissions(cid, f"ela9v2-i01@{INTV}")
    assert [s.user_id for s in rows] == [C]
    assert c.list_submissions(cid, f"ela9v2-a01@{INTV}") == []
    with pytest.raises(LookupError):
        c.get_submission(cid, f"ela9v2-i01@{INTV}", D)


def test_latest_attempt_wins(tmp_path):
    import shutil
    shutil.copytree(DATA, tmp_path / "d")
    p = tmp_path / "d" / "courses" / CTRL / "submissions" / "ela9v2-a01.jsonl"
    first = p.read_text(encoding="utf-8").splitlines()[0]
    p.write_text(p.read_text(encoding="utf-8") + first.replace('"attempt": 1', '"attempt": 2')
                 .replace("found a key", "found B") + "\n", encoding="utf-8", newline="\n")
    c = CourseDirLMSClient(tmp_path / "d", CTRL)
    cid = f"classroom_{CTRL}"
    s = c.get_submission(cid, f"ela9v2-a01@{CTRL}", A)
    assert s.attempt == 2
    assert len([x for x in c.list_submissions(cid, f"ela9v2-a01@{CTRL}") if x.user_id == A]) == 1


@pytest.mark.parametrize("call", [
    lambda c, cid: c.get_course("classroom_9.99"),
    lambda c, cid: c.get_assignment("classroom_9.99", f"ela9v2-a01@{CTRL}"),
    lambda c, cid: c.get_assignment(cid, "ela9v2-a01"),  # no @
    lambda c, cid: c.get_assignment(cid, "ela9v2-a01@9.99"),
    lambda c, cid: c.get_assignment(cid, f"nope@{CTRL}"),
    lambda c, cid: c.get_assignment(cid, f"../x@{CTRL}"),
    lambda c, cid: c.get_submission(cid, f"../../x@{CTRL}", A),
    lambda c, cid: c.get_submission(cid, f"ela9v2-a01@{CTRL}", "zz"),
    lambda c, cid: c.list_submissions("classroom_9.99"),
    lambda c, cid: c.list_submissions(cid, f"nope@{CTRL}"),
    lambda c, cid: c.list_submissions(cid, "ela9v2-a01"),
])
def test_unknown_ids_raise_lookup_error_without_paths(call):
    c, cid = ctrl()
    with pytest.raises(LookupError) as e:
        call(c, cid)
    msg = str(e.value)
    assert str(DATA) not in msg and ".json" not in msg


def test_unknown_version_is_lookup_error_without_path():
    c = CourseDirLMSClient(DATA, "9.99")
    with pytest.raises(LookupError) as e:
        c.get_course("classroom_9.99")
    assert str(DATA) not in str(e.value) and "\\" not in str(e.value)


def test_never_opens_answer_key(monkeypatch):
    import builtins
    opened = []
    real_open, real_pop, real_rt = builtins.open, Path.open, Path.read_text

    def spy(p):
        opened.append(str(p))

    monkeypatch.setattr(builtins, "open", lambda f, *a, **k: (spy(f), real_open(f, *a, **k))[1])
    monkeypatch.setattr(Path, "open", lambda self, *a, **k: (spy(self), real_pop(self, *a, **k))[1])
    monkeypatch.setattr(Path, "read_text", lambda self, *a, **k: (spy(self), real_rt(self, *a, **k))[1])
    c, cid = ctrl()
    c.get_course(cid)
    c.list_submissions(cid)
    c.get_assignment(cid, f"ela9v2-a03@{CTRL}")
    assert opened and not [p for p in opened if "answer_key" in p or "skill_families" in p]


@pytest.mark.parametrize("call", [
    lambda c, cid: c.get_course(cid),
    lambda c, cid: c.get_assignment(cid, "ela9v2-a01@9.99"),
    lambda c, cid: c.get_submission(cid, "ela9v2-a01@9.99", A),
    lambda c, cid: c.list_submissions(cid),
    lambda c, cid: c.list_submissions(cid, "ela9v2-a01@9.99"),
])
def test_missing_version_folder_raises_lookup_error_without_paths(call):
    c = CourseDirLMSClient(DATA, "9.99")
    with pytest.raises(LookupError) as e:
        call(c, "classroom_9.99")
    assert str(DATA) not in str(e.value) and "9.99" not in str(DATA)


def test_reading_strand_from_module():
    c, cid = ctrl()
    assert c.get_assignment(cid, f"ela9v2-a02@{CTRL}").reading_strand == "RL"


def test_reading_strand_ri(tmp_path):
    import shutil
    shutil.copytree(DATA, tmp_path / "d")
    p = tmp_path / "d" / "curricula" / "ela9-test" / "curriculum.json"
    p.write_text(p.read_text(encoding="utf-8").replace('"RL"', '"RI"'), encoding="utf-8", newline="\n")
    c = CourseDirLMSClient(tmp_path / "d", CTRL)
    assert c.get_assignment(f"classroom_{CTRL}", f"ela9v2-a02@{CTRL}").reading_strand == "RI"

import pytest

from agentic.dataset import labels_path
from agentic_lab.labels import load_labels
from agentic.standards.loader import normalize_rows, read_xlsx
from agentic_lab.config import lab_settings

S = lab_settings()
S.standards_file = S.standards_file.with_name("mls-ela-writing-v1.xlsx")  # pinned: the MVP labels use the v1 file


def test_standards_file_loads():
    if not S.standards_file.exists():
        pytest.skip(f"{S.standards_file.name} not present")
    rows = normalize_rows(read_xlsx(S.standards_file))  # parses every grade label
    assert rows
    codes = [r.code for r in rows]
    assert len(codes) == len(set(codes))


def test_labels_exist_in_standards_at_grade():
    if not S.standards_file.exists() or not labels_path(S.data_dir, S.classroom).exists():
        pytest.skip("standards file or labels file not present")
    by_code = {r.code: r for r in normalize_rows(read_xlsx(S.standards_file))}
    for label in load_labels(S.data_dir, S.classroom):
        for u in label.underdeveloped:
            assert u.standard in by_code, f"unknown standard {u.standard}"
            std = by_code[u.standard]
            assert std.grade_min <= label.grade <= std.grade_max, f"{u.standard} not in grade {label.grade}"


# ---- v2 course folders (the committed slice) ----
import json
from pathlib import Path

from agentic.coursedata import CourseDir, SubmissionRow
from agentic_lab.answer_key import ExpectedRow, FidelityRow, answer_key_path

V2_VERSIONS = ["2.00-control", "2.00-intervention"]


def _v2_dirs():
    return [v for v in V2_VERSIONS if (S.data_dir / "courses" / v / "submissions").exists()]


def _rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_v2_slice_submissions_load():
    versions = _v2_dirs()
    if not versions:
        pytest.skip("no v2 submissions committed")
    for v in versions:
        cd = CourseDir(S.data_dir, v)
        total = 0
        for a in cd.assignments():
            rows = cd.submissions(a.id)  # validates every row (SubmissionRow, strict)
            total += len(rows)
            for r in rows:
                assert isinstance(r, SubmissionRow)
                assert r.assignment_id == a.id and r.course_id == f"classroom_{v}"
        assert total > 0, v


def test_v2_expected_and_fidelity_reference_existing_submissions():
    versions = _v2_dirs()
    if not versions:
        pytest.skip("no v2 submissions committed")
    for v in versions:
        cd = CourseDir(S.data_dir, v)
        ids = {r.submission_id for a in cd.assignments() for r in cd.submissions(a.id)}
        key = answer_key_path(S.data_dir, v)
        for path in sorted((key / "expected").glob("*.jsonl")):
            want = {r.submission_id for r in cd.submissions(path.stem)}
            got = {ExpectedRow.model_validate(row).submission_id for row in _rows(path)}
            assert got == want, f"{v}/{path.name}"
        for row in _rows(key / "fidelity.jsonl"):
            assert FidelityRow.model_validate(row).submission_id in ids, f"{v}/fidelity.jsonl"


def test_v2_answer_key_is_never_under_a_folder_production_reads(monkeypatch):
    """CourseDir and CourseDirLMSClient read the course folder and the shared curriculum; they never open
    answer_key/ or skill_families.json."""
    import builtins
    from agentic.lms.coursedir_client import CourseDirLMSClient
    versions = _v2_dirs()
    if not versions:
        pytest.skip("no v2 submissions committed")
    opened: list[str] = []
    real_open, real_pop, real_rt = builtins.open, Path.open, Path.read_text

    def spy(p):
        opened.append(str(p))

    monkeypatch.setattr(builtins, "open", lambda f, *a, **k: (spy(f), real_open(f, *a, **k))[1])
    monkeypatch.setattr(Path, "open", lambda self, *a, **k: (spy(self), real_pop(self, *a, **k))[1])
    monkeypatch.setattr(Path, "read_text", lambda self, *a, **k: (spy(self), real_rt(self, *a, **k))[1])
    for v in versions:
        cd = CourseDir(S.data_dir, v)
        cd.course(), cd.roster(), cd.interventions(), cd.curriculum()
        assigns = cd.assignments()
        for a in assigns:
            cd.submissions(a.id)
        text_id = assigns[0].text_id
        if text_id:
            cd.text(text_id)
        client, cid = CourseDirLMSClient(S.data_dir, v), f"classroom_{v}"
        client.get_course(cid)
        client.list_submissions(cid)
        client.get_assignment(cid, f"{assigns[0].id}@{v}")
    assert opened
    assert any("submissions" in p for p in opened) and any("roster" in p for p in opened)
    assert not [p for p in opened if "answer_key" in p or "skill_families" in p]


def test_v2_mc_answers_point_at_the_current_quiz_items():
    """Every multiple-choice row answers each current item exactly once, with one of that item's choice keys."""
    versions = _v2_dirs()
    if not versions:
        pytest.skip("no v2 submissions committed")
    checked = 0
    for v in versions:
        cd = CourseDir(S.data_dir, v)
        for a in cd.assignments():
            if a.type != "multiple_choice":
                continue
            keys = {i.id: {c.key for c in i.choices} for i in a.items}
            for row in cd.submissions(a.id):
                assert sorted(x.item_id for x in row.answers) == sorted(keys), f"{v}/{row.submission_id}"
                assert all(x.choice in keys[x.item_id] for x in row.answers), f"{v}/{row.submission_id}"
                checked += 1
    assert checked

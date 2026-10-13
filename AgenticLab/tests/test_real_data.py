import pytest

from agentic.dataset import labels_path
from agentic_lab.labels import load_labels
from agentic.standards.loader import normalize_rows, read_xlsx
from agentic_lab.config import lab_settings

S = lab_settings()


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

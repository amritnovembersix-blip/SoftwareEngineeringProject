"""Fixture builders for synthetic dataset files (import as `from fakes import ...`)."""
import json
from pathlib import Path

from agentic.dataset import essays_path, labels_path


def label_row(student_hash="a" * 64, grade=9, meets_all_standards=True, underdeveloped=None, salt="1234"):
    return {
        "student_hash": student_hash,
        "salt": salt,
        "grade": grade,
        "meets_all_standards": meets_all_standards,
        "underdeveloped": underdeveloped or [],
    }


def essay_row(student_hash="a" * 64, paragraphs=None, submission_id="sub-1", attempt=1,
              assignment_id="asg-classroom_1.00-essay1", course_id="classroom_1.00",
              submitted_at="2026-09-01T12:00:00Z"):
    return {
        "student_hash": student_hash,
        "submission_id": submission_id,
        "attempt": attempt,
        "assignment_id": assignment_id,
        "course_id": course_id,
        "submitted_at": submitted_at,
        "paragraphs": paragraphs if paragraphs is not None else ["First.", "Second."],
    }


def _write(path: Path, rows) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return path


def write_labels(tmp_path, version, rows):
    return _write(labels_path(tmp_path, version), rows)


def write_essays(tmp_path, version, rows):
    return _write(essays_path(tmp_path, version), rows)


XLSX_HEADERS = ["Content Area", "MLS Code", "Grade", "Strand", "Strand Description", "Theme/Big Idea",
                "Theme/Big Idea Description", "Concept", "Concept Description", "MLS",
                "MLS Description", None, "Sub expectation", None]


def xlsx_fixture(tmp_path, rows: list[dict]) -> Path:
    """Write a workbook with the standards spreadsheet's headers (note the blank headers)."""
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(XLSX_HEADERS)
    for r in rows:
        ws.append([r.get(h) if h else None for h in XLSX_HEADERS])
    path = Path(tmp_path) / "standards.xlsx"
    wb.save(path)
    return path


def std_row(code="9-10.W.1.A.a", grade="9 to 10", strand_code="W", strand="Writing",
            concept="Research", description="Do research.", sub=None, big_idea="Task"):
    return {"Content Area": "ELA", "MLS Code": code, "Grade": grade, "Strand": strand_code,
            "Strand Description": strand, "Theme/Big Idea Description": big_idea,
            "Concept Description": concept, "MLS Description": description,
            "Sub expectation": sub}

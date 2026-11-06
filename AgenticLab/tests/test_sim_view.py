import ast
import inspect
import json
from datetime import date, datetime, timezone
from pathlib import Path

import openpyxl
import pytest

from agentic_lab import sim_view
from agentic_lab.sim.findings_query import FindingCell, area_of, band
from agentic_lab.sim.simulator import AssignmentStatus
from agentic_lab.sim_view import (GridCell, assignment_details, ground_truth, grid, intervention_marks,
                                  pipeline_rows, roster_pairs, week_end)

DATA = Path(__file__).resolve().parents[1] / "data" / "synthetic"
CONTROL, INTERVENTION = "2.00-control", "2.00-intervention"
GRAMMAR, PUNCT, REVISE = "3–5 · L · Grammar", "3–5 · L · Punctuation", "9–10 · W · Revise and Edit"


def _utc(*args):
    return datetime(*args, tzinfo=timezone.utc)


def _cell(student, area, due, count=1, level=None, strengths=0):
    return FindingCell(student_hash=student, area=area, due_at=due, count=count, level=level, strengths=strengths)


# --- areas -------------------------------------------------------------------------------------

def test_band_and_area_labels():
    assert [band(g) for g in (3, 4, 5, 6, 8, 9, 11)] == ["3–5", "3–5", "3–5", "6–8", "6–8", "9–10", "11–12"]
    assert area_of(3, "L", "Grammar") == GRAMMAR
    assert area_of(9, "W", "Revise and Edit") == REVISE


# --- week_end ----------------------------------------------------------------------------------

def test_week_end_is_sunday_night_chicago_in_utc():
    assert week_end(date(2027, 1, 11), 1) == _utc(2027, 1, 18, 5, 59, 59)
    assert week_end(date(2027, 1, 11), 2) == _utc(2027, 1, 25, 5, 59, 59)
    assert week_end(date(2027, 1, 11), 9) == _utc(2027, 3, 15, 4, 59, 59)  # daylight time from 2027-03-14


# --- grid --------------------------------------------------------------------------------------

W1, W2 = _utc(2027, 1, 16, 5, 59), _utc(2027, 1, 23, 5, 59)


def test_grid_rows_areas_and_aggregation():
    cells = [_cell("bbbbbbbb22", GRAMMAR, W1, 2, "approaching"),
             _cell("bbbbbbbb22", GRAMMAR, W2, 1, "beginning", strengths=1),
             _cell("aaaaaaaa11", REVISE, W1, 1, "developing"),
             _cell("aaaaaaaa11", PUNCT, W1, 0, None, strengths=2)]
    rows, areas, values = grid(cells)
    assert rows == ["aaaaaaaa", "bbbbbbbb"]
    assert areas == [GRAMMAR, PUNCT, REVISE]  # band order, then strand, then concept
    assert values[("bbbbbbbb", GRAMMAR)] == GridCell(count=3, level="beginning", strengths=1)
    assert values[("aaaaaaaa", PUNCT)] == GridCell(count=0, level=None, strengths=2)
    assert ("aaaaaaaa", GRAMMAR) not in values


def test_grid_sorts_areas_by_band_order_not_text():
    cells = [_cell("a", area_of(11, "W", "Research"), W1), _cell("a", area_of(9, "L", "Grammar"), W1),
             _cell("a", area_of(6, "W", "Draft"), W1), _cell("a", area_of(3, "W", "Draft"), W1)]
    assert grid(cells)[1] == ["3–5 · W · Draft", "6–8 · W · Draft", "9–10 · L · Grammar", "11–12 · W · Research"]


def test_grid_excludes_later_cells():
    cells = [_cell("a", GRAMMAR, W1, 1, "approaching"), _cell("a", GRAMMAR, W2, 4, "beginning"),
             _cell("b", REVISE, W2, 1)]
    rows, areas, values = grid(cells, through=week_end(date(2027, 1, 11), 1))
    assert rows == ["a"] and areas == [GRAMMAR]
    assert values == {("a", GRAMMAR): GridCell(count=1, level="approaching", strengths=0)}


def test_grid_paired_rows_align_control_and_intervention():
    pairs = [("c1", "i1"), ("c2", "i2")]
    cells = [_cell("c1", GRAMMAR, W1, 1, "developing"), _cell("i1", GRAMMAR, W1, 2, "approaching"),
             _cell("i2", REVISE, W1, 1, "beginning"), _cell("stranger", PUNCT, W1)]
    rows, areas, values = grid(cells, pairs=pairs)
    assert rows == ["p01", "p02"]
    assert areas == [GRAMMAR, REVISE]  # a student outside the pairs adds nothing
    assert values[("p01", GRAMMAR)] == (GridCell(1, "developing", 0), GridCell(2, "approaching", 0))
    assert values[("p02", REVISE)] == (None, GridCell(1, "beginning", 0))
    assert ("p01", REVISE) not in values


def test_roster_pairs_zip_both_rosters_by_position():
    pairs = roster_pairs(DATA, CONTROL, INTERVENTION)
    assert len(pairs) == 18
    assert pairs[0][0].startswith("916e13a4")
    assert len({c for c, _ in pairs} | {i for _, i in pairs}) == 36


def test_roster_pairs_rejects_different_lengths(tmp_path):
    for version, n in (("x", 2), ("y", 3)):
        d = tmp_path / "courses" / version
        d.mkdir(parents=True)
        (d / "roster.jsonl").write_text(
            "".join(json.dumps({"student_hash": f"{version}{k}"}) + "\n" for k in range(n)), encoding="utf-8")
    with pytest.raises(ValueError, match="2 and 3"):
        roster_pairs(tmp_path, "x", "y")


# --- pipeline ----------------------------------------------------------------------------------

def test_pipeline_rows_up_to_the_week_with_details():
    status = [AssignmentStatus(CONTROL, "ela9v2-a01", 1, True, 18, succeeded=17, failed=1),
              AssignmentStatus(CONTROL, "ela9v2-a02", 1, True, 18, queued=3, running=2, succeeded=13),
              AssignmentStatus(CONTROL, "ela9v2-a03", 2, False, 0)]
    details = {(CONTROL, "ela9v2-a01"): ("multiple_choice", _utc(2027, 1, 13, 5, 59))}
    rows = pipeline_rows(status, 1, details)
    assert [r["assignment"] for r in rows] == ["ela9v2-a01@2.00-control", "ela9v2-a02@2.00-control"]
    assert rows[0] == {"course": CONTROL, "assignment": "ela9v2-a01@2.00-control", "week": 1,
                       "type": "multiple_choice", "due": date(2027, 1, 12), "released": True, "sent": 18,
                       "queued": 0, "running": 0, "succeeded": 17, "failed": 1, "unknown": 0}
    assert rows[1]["type"] is None and rows[1]["due"] is None and rows[1]["queued"] == 3


def test_pipeline_rows_without_run_states_leave_the_counts_blank():
    status = [AssignmentStatus(CONTROL, "ela9v2-a01", 1, True, 18)]
    row = pipeline_rows(status, 1, run_states=False)[0]
    assert row["released"] is True and row["sent"] == 18
    assert [row[k] for k in ("queued", "running", "succeeded", "failed", "unknown")] == [None] * 5


def test_assignment_details_from_course_data():
    details = assignment_details(DATA, [CONTROL, INTERVENTION])
    assert details[(CONTROL, "ela9v2-a01")][0] == "multiple_choice"
    assert details[(CONTROL, "ela9v2-a02")] == ("paragraph", _utc(2027, 1, 16, 5, 59))
    assert (INTERVENTION, "ela9v2-i01") in details and (CONTROL, "ela9v2-i01") not in details


# --- interventions -----------------------------------------------------------------------------

def test_intervention_marks():
    assert intervention_marks(DATA, CONTROL) == []
    marks = intervention_marks(DATA, INTERVENTION)
    assert [(m["week"], m["id"], m["kind"]) for m in marks] == [
        (4, "iv1", "class_lesson"), (4, "iv2", "individual"), (8, "iv3", "small_group")]
    assert marks[0]["students"] == "all" and len(marks[1]["students"]) == 4


# --- ground truth (lab only) -------------------------------------------------------------------

def _standards_xlsx(path: Path, rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    ws.append(["Content Area", "MLS Code", "Grade", "Strand", "Strand Description", "Theme/Big Idea",
               "Theme/Big Idea Description", "Concept", "Concept Description", "MLS", "MLS Description",
               None, "Sub expectation"])
    for code, grade, strand, concept in rows:
        ws.append(["ELA", code, grade, strand, "x", "1", "x", "A", concept, code, "description", None, None])
    wb.save(path)


def test_ground_truth_latest_row_per_standard_and_max_per_area(tmp_path):
    xlsx = tmp_path / "standards.xlsx"
    _standards_xlsx(xlsx, [("3.L.1.A.a", "3", "L", "Grammar"), ("4.L.1.A.b", "4", "L", "Grammar"),
                           ("9-10.W.3.A.a", "9 to 10", "W", "Revise and Edit")])
    key = tmp_path / "courses" / "v" / "answer_key"
    key.mkdir(parents=True)
    rows = [("s1", 1, "3.L.1.A.a", 3), ("s1", 4, "3.L.1.A.a", 1), ("s1", 2, "4.L.1.A.b", 2),
            ("s1", 6, "4.L.1.A.b", 3), ("s1", 1, "9-10.W.3.A.a", 2), ("s2", 5, "9-10.W.3.A.a", 1)]
    (key / "trajectory.jsonl").write_text("".join(
        json.dumps({"student_hash": s, "week": w, "standard": c, "level": lv}) + "\n" for s, w, c, lv in rows),
        encoding="utf-8")
    assert ground_truth(tmp_path, "v", 4, xlsx) == {("s1", GRAMMAR): 2, ("s1", REVISE): 2}
    assert ground_truth(tmp_path, "v", 6, xlsx) == {("s1", GRAMMAR): 3, ("s1", REVISE): 2, ("s2", REVISE): 1}


def test_only_ground_truth_touches_the_answer_key():
    source = inspect.getsource(sim_view)
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            assert "answer_key" not in ast.unparse(node)
    inside = inspect.getsource(sim_view.ground_truth)
    assert "answer_key" in inside
    assert "answer_key" not in source.replace(inside, "")

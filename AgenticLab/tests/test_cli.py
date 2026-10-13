import pytest
from fakes import essay_row, label_row, write_essays, write_labels

from agentic_lab.__main__ import main

SECRET = "Secret essay sentence alpha."


@pytest.fixture
def cli_env(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    write_labels(data, "1.01", [label_row(student_hash="a" * 64, grade=9),
                                label_row(student_hash="ab" + "c" * 62, grade=9)])
    write_essays(data, "1.01", [
        essay_row(student_hash="a" * 64, paragraphs=[SECRET, "Second para.", "Third para."],
                  course_id="classroom_1.01", assignment_id="asg-classroom_1.01-essay1",
                  submission_id="sub-1"),
        essay_row(student_hash="ab" + "c" * 62, paragraphs=["Other."], course_id="classroom_1.01",
                  assignment_id="asg-classroom_1.01-essay1", submission_id="sub-2"),
    ])
    reports = tmp_path / "reports"
    for k, v in {"FAKE_LLM": "1", "VERIFY": "1", "REVIEW_STRATEGY": "full", "DATA_DIR": str(data),
                 "REPORTS_DIR": str(reports), "CLASSROOM": "1.01"}.items():
        monkeypatch.setenv(k, v)
    return reports


def test_evaluate_writes_report_and_prints(cli_env, capsys):
    assert main(["evaluate", "--strategy", "full", "--students", "1"]) == 0
    out = capsys.readouterr().out
    assert "full" in out and SECRET not in out
    files = list(cli_env.glob("eval-*.json"))
    assert len(files) == 1 and SECRET not in files[0].read_text(encoding="utf-8")


def test_evaluate_bad_students_is_setup_error(cli_env, capsys):
    assert main(["evaluate", "--students", "0"]) == 2
    assert capsys.readouterr().err.strip()


def test_evaluate_defaults_to_both_strategies(cli_env, capsys):
    assert main(["evaluate"]) == 0
    out = capsys.readouterr().out
    assert "full" in out and "rag" in out
    body = next(cli_env.glob("eval-*.json")).read_text(encoding="utf-8")
    assert '"full"' in body and '"rag"' in body


def test_evaluate_uses_classroom_assignment_strands(cli_env, capsys, tmp_path):
    import json

    (tmp_path / "data" / "classroom_1.01.assignment.json").write_text('{"type": "ri"}', encoding="utf-8")
    assert main(["evaluate", "--strategy", "full"]) == 0
    out = capsys.readouterr().out
    assert "Strands reviewed: W, RI" in out
    report = next(cli_env.glob("eval-*.json"))
    assert json.loads(report.read_text(encoding="utf-8"))["strands"] == ["W", "RI"]


def test_evaluate_compare_context_runs_both(cli_env, capsys):
    import json

    assert main(["evaluate", "--strategy", "full", "--compare-context"]) == 0
    report = json.loads(next(cli_env.glob("eval-*.json")).read_text(encoding="utf-8"))
    assert set(report["strategies"]) == {"full", "full-noctx"}
    assert "full-noctx" in capsys.readouterr().out


def test_evaluate_writes_markdown_report_next_to_json(cli_env, capsys):
    assert main(["evaluate", "--strategy", "full"]) == 0
    md = next(cli_env.glob("eval-*.md"))
    assert next(cli_env.glob("eval-*.json")).stem == md.stem
    assert md.read_text(encoding="utf-8").startswith("# Evaluation")
    assert str(md) in capsys.readouterr().out

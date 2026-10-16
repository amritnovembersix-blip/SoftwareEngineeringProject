import pytest
from fakes import essay_row, label_row, write_essays, write_labels

from agentic.__main__ import main

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


def test_review_prints_findings_and_writes_report(cli_env, capsys):
    assert main(["review", "aaaa"]) == 0
    out = capsys.readouterr().out
    assert "Findings (2)" in out
    assert SECRET not in out
    reports = list(cli_env.glob("sub-1-full-*.json"))
    assert len(reports) == 1
    assert str(reports[0]) in out
    assert SECRET not in reports[0].read_text(encoding="utf-8")


def test_review_no_verify_and_strategy_override(cli_env, capsys):
    assert main(["review", "aaaa", "--no-verify", "--strategy", "full"]) == 0
    assert "Verification: disabled" in capsys.readouterr().out


@pytest.mark.parametrize("prefix", ["zzz", "a"])
def test_review_unknown_or_ambiguous_student(cli_env, capsys, prefix):
    assert main(["review", prefix]) == 2
    cap = capsys.readouterr()
    assert cap.err.strip()
    assert "Traceback" not in cap.out + cap.err
    assert SECRET not in cap.out + cap.err


@pytest.fixture
def cli_db(cli_env, db_conn, tmp_path, monkeypatch):
    """Run the CLI against db_conn (rolled back) so no data is left in the dev DB."""
    from fakes import clear_standards, std_row, xlsx_fixture

    from agentic import wiring
    from agentic.llm.fake import FakeEmbedder
    from agentic.standards.fake import demo_standards
    from agentic.standards.loader import load, normalize_rows, read_xlsx

    monkeypatch.setattr(wiring, "connect", lambda s, autocommit=True: db_conn)
    clear_standards(db_conn)  # the dev DB may hold real standards and saved reviews
    rows = normalize_rows(read_xlsx(xlsx_fixture(tmp_path, [std_row(s.code) for s in demo_standards()])))
    load(db_conn, rows, FakeEmbedder())
    return db_conn


@pytest.mark.db
def test_seed_demo_prints_student_count(cli_db, capsys):
    assert main(["seed-demo"]) == 0
    assert "2" in capsys.readouterr().out


@pytest.mark.db
def test_cli_persist_duplicate_message(cli_db, capsys):
    assert main(["seed-demo"]) == 0
    capsys.readouterr()
    assert main(["review", "aaaa", "--persist"]) == 0
    first = capsys.readouterr().out
    assert "saved (review_run " in first
    assert main(["review", "aaaa", "--persist"]) == 0
    second = capsys.readouterr().out
    assert "already reviewed (review_run " in second
    assert "nothing written" in second
    assert SECRET not in first + second


def test_review_persist_failure_is_content_free(cli_env, monkeypatch, capsys):
    from agentic import wiring

    class Boom:
        def save(self, result, trigger="manual"):
            raise ValueError("SECRET ESSAY TEXT")

    monkeypatch.setattr(wiring, "build_results_writer", lambda s: Boom())
    assert main(["review", "aaaa", "--persist"]) == 1
    cap = capsys.readouterr()
    assert "persist failed: ValueError" in cap.err
    assert "SECRET" not in cap.out + cap.err


def test_review_bad_provider_is_clean_error(cli_env, monkeypatch, capsys):
    monkeypatch.setenv("FAKE_LLM", "0")
    monkeypatch.setenv("LLM_PROVIDER", "nope")
    assert main(["review", "aaaa"]) == 2
    cap = capsys.readouterr()
    assert "Traceback" not in cap.out + cap.err
    assert "nope" in cap.err


def test_load_standards_missing_file(cli_env, tmp_path, capsys):
    missing = tmp_path / "nope.xlsx"
    assert main(["load-standards", "--file", str(missing)]) == 2
    assert f"standards file not found: {missing}" in capsys.readouterr().err


def test_review_run_failure_is_content_free(cli_env, monkeypatch, capsys):
    from agentic import wiring

    class Boom:
        models = {}

        def run(self, ref):
            raise ValueError("SECRET ESSAY TEXT")

    monkeypatch.setattr(wiring, "build_reviewer", lambda *a, **k: Boom())
    assert main(["review", "aaaa"]) == 1
    cap = capsys.readouterr()
    assert "review failed: ValueError" in cap.err
    assert "SECRET" not in cap.out + cap.err
    assert "Traceback" not in cap.out + cap.err


def test_load_standards_failure_is_content_free(cli_env, tmp_path, monkeypatch, capsys):
    from agentic.standards import loader

    f = tmp_path / "s.xlsx"
    f.write_bytes(b"x")

    def boom(path):
        raise ValueError("SECRET ESSAY TEXT")

    monkeypatch.setattr(loader, "read_xlsx", boom)
    assert main(["load-standards", "--file", str(f)]) == 1
    cap = capsys.readouterr()
    assert "load-standards failed: ValueError" in cap.err
    assert "SECRET" not in cap.out + cap.err


def test_review_strands_flag_is_recorded(cli_env, capsys):
    import json

    assert main(["review", "aaaa", "--strands", "RL"]) == 0
    out = capsys.readouterr().out
    assert "Strands: W, RL" in out
    report = next(cli_env.glob("sub-1-full-*.json"))
    assert json.loads(report.read_text(encoding="utf-8"))["strands"] == ["W", "RL"]


def test_review_assignment_context_off_flag(cli_env, capsys):
    assert main(["review", "aaaa", "--assignment-context", "off"]) == 0
    assert "Assignment context: off" in capsys.readouterr().out

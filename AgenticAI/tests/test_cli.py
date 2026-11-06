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


def test_legacy_review_form_stays_on_the_mvp_pipeline(cli_env, capsys, monkeypatch):
    """Ruling T9a: `review <student>` keeps its MVP output until the v2 CLI (Task 13)."""
    from agentic import wiring
    from agentic.review.graph import Reviewer

    monkeypatch.setenv("REVIEW_PIPELINE", "v2")
    built, real = [], wiring.build_reviewer
    monkeypatch.setattr(wiring, "build_reviewer", lambda *a, **k: built.append(real(*a, **k)) or built[-1])
    assert main(["review", "aaaa"]) == 0
    assert len(built) == 1 and type(built[0]) is Reviewer
    assert "Findings (2)" in capsys.readouterr().out


# --- review --course (v2 data) ------------------------------------------------------------------

import json  # noqa: E402
import shutil  # noqa: E402
from pathlib import Path  # noqa: E402

COURSEDIR = Path(__file__).parent / "data" / "coursedir"
PROMPTS = Path(__file__).parents[1] / "prompts"
SLICE = Path(__file__).parents[2] / "agentic_lab" / "data" / "synthetic" / "courses" / "2.00-control" / "submissions"


@pytest.fixture
def v2_env(tmp_path, monkeypatch):
    """FAKE_LLM against the committed test course folder; any DB access or persist fails the test."""
    import psycopg

    from agentic import wiring

    for k, v in {"FAKE_LLM": "1", "DATA_DIR": str(COURSEDIR), "REPORTS_DIR": str(tmp_path / "reports"),
                 "BANDS_PROFILE": "full"}.items():
        monkeypatch.setenv(k, v)

    def no_db(*a, **k):
        raise AssertionError("opened a database connection")

    monkeypatch.setattr(psycopg, "connect", no_db)
    monkeypatch.setattr(wiring, "build_results_writer", no_db)
    return tmp_path


def _v2(*extra):
    return ["review", "--course", "0.90-control", "--assignment", "ela9v2-a02", "--student", "a1a1", *extra]


def test_review_course_writes_json_with_trace_and_persists_nothing(v2_env, capsys):
    out = v2_env / "out.json"
    assert main(_v2("--json", str(out))) == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["pipeline"] == "v2"
    assert data["trace"]["reviewers"]  # the trace reaches disk only here, because the user asked
    printed = capsys.readouterr().out
    assert "A key." not in printed  # the summary shows codes and counts, never segment text
    assert "Verdicts" in printed and "tokens" in printed.lower()


def test_review_course_options_pass_through(v2_env):
    out = v2_env / "out.json"
    assert main(_v2("--json", str(out), "--no-root-cause", "--no-verify", "--profile", "lean")) == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["verification"]["status"] == "disabled"
    assert "root_cause" not in data["prompt_versions"]


def test_review_course_unknown_student(v2_env, capsys):
    argv = _v2()
    argv[argv.index("a1a1")] = "zzzz"
    assert main(argv) == 2
    cap = capsys.readouterr()
    assert "zzzz" in cap.err
    assert "Traceback" not in cap.out + cap.err


def test_review_course_ambiguous_student(v2_env, monkeypatch, capsys):
    data = v2_env / "data"
    shutil.copytree(COURSEDIR, data)
    roster = data / "courses" / "0.90-control" / "roster.jsonl"
    roster.write_text(roster.read_text(encoding="utf-8") + '{"student_hash": "a1a1ffffffffffff"}\n',
                      encoding="utf-8")
    monkeypatch.setenv("DATA_DIR", str(data))
    assert main(_v2()) == 2
    assert "a1a1" in capsys.readouterr().err


def test_review_course_unknown_assignment_or_course(v2_env, capsys):
    assert main(["review", "--course", "0.90-control", "--assignment", "nope", "--student", "a1a1"]) == 2
    assert main(["review", "--course", "9.99-none", "--assignment", "ela9v2-a02", "--student", "a1a1"]) == 2
    assert "Traceback" not in capsys.readouterr().err


def test_review_course_needs_all_three_flags(v2_env, capsys):
    assert main(["review", "--course", "0.90-control", "--student", "a1a1"]) == 2
    assert main(["review"]) == 2


def test_review_course_missing_prompt_file_exits_2_naming_it(v2_env, capsys):
    assert main(_v2("--prompt", "3-5-language=v9")) == 2
    assert "per_standard.v9" in capsys.readouterr().err


def test_review_course_prompt_that_does_not_fit_the_mode_exits_2(v2_env, capsys):
    assert main(_v2("--prompt", "6+-writing=v3")) == 2  # full profile: mode progression
    err = capsys.readouterr().err
    assert "6+-writing" in err and "progression.v3" in err and "Traceback" not in err


def test_review_course_bad_prompt_argument(v2_env, capsys):
    assert main(_v2("--prompt", "3-5-language")) == 2
    assert main(_v2("--prompt", "no-such-reviewer=v2")) == 2


def test_review_course_prompt_version_changes_tuning_hash(v2_env, monkeypatch):
    prompts = v2_env / "prompts"
    shutil.copytree(PROMPTS, prompts)
    (prompts / "per_standard.v2.md").write_text(
        (PROMPTS / "per_standard.v1.md").read_text(encoding="utf-8") + "\nBe concise.\n", encoding="utf-8")
    monkeypatch.setenv("PROMPTS_DIR", str(prompts))
    base, tuned = v2_env / "base.json", v2_env / "tuned.json"
    assert main(_v2("--json", str(base))) == 0
    assert main(_v2("--json", str(tuned), "--prompt", "3-5-language=v2")) == 0
    a, b = (json.loads(p.read_text(encoding="utf-8")) for p in (base, tuned))
    assert a["tuning_hash"] != b["tuning_hash"]
    assert b["prompt_versions"]["3-5-language"] == "per_standard.v2"


def test_review_course_persist_saves_through_the_writer(v2_env, monkeypatch, capsys):
    from agentic import wiring
    from agentic.results.base import SaveOutcome

    saved = []

    class Writer:
        def save(self, result):
            saved.append(result)
            return SaveOutcome(status="written", review_run_id="7")

    monkeypatch.setattr(wiring, "build_results_writer", lambda s: Writer())
    assert main(_v2("--persist")) == 0
    assert len(saved) == 1 and saved[0].pipeline == "v2"
    assert "review_run 7" in capsys.readouterr().out


@pytest.mark.skipif(not SLICE.exists(), reason="the 2.00 slice data is not generated yet")
def test_review_course_on_the_slice(tmp_path, monkeypatch, capsys):
    import re

    monkeypatch.setenv("FAKE_LLM", "1")
    monkeypatch.setenv("DATA_DIR", str(SLICE.parents[2]))
    first = next((SLICE / "ela9v2-a02.jsonl").open(encoding="utf-8"))
    prefix = re.search(r'"student_hash": "([0-9a-f]{8})', first).group(1)
    out = tmp_path / "out.json"
    assert main(["review", "--course", "2.00-control", "--assignment", "ela9v2-a02", "--student", prefix,
                 "--json", str(out)]) == 0
    assert json.loads(out.read_text(encoding="utf-8"))["pipeline"] == "v2"


def test_review_course_unexpected_lookup_errors_print_the_class_only(v2_env, monkeypatch, capsys):
    import agentic.__main__ as cli

    for error in (KeyError("Secret essay sentence alpha."), LookupError("Secret essay sentence alpha.")):
        def boom(*a, error=error, **k):
            raise error
        monkeypatch.setattr(cli, "review_one", boom)
        assert main(_v2()) == 1
        err = capsys.readouterr().err
        assert "Secret" not in err and f"review failed: {type(error).__name__}" in err


def test_review_course_not_found_names_only_the_missing_thing(v2_env, monkeypatch, capsys):
    import agentic.__main__ as cli

    for message in ("no submission found", "unknown assignment", "unknown course 'classroom_x'"):
        def missing(*a, message=message, **k):
            raise LookupError(message)
        monkeypatch.setattr(cli, "review_one", missing)
        assert main(_v2()) == 2
        assert message in capsys.readouterr().err


@pytest.mark.parametrize("flag", [["--profile", "lean"], ["--prompt", "3-5-language=v2"], ["--no-root-cause"],
                                  ["--pipeline", "v2"], ["--json", "out.json"]])
def test_legacy_form_rejects_v2_only_flags(cli_env, capsys, flag):
    assert main(["review", "aaaa", *flag]) == 2
    err = capsys.readouterr().err
    assert flag[0] in err and "--course" in err


def test_review_course_persist_uses_the_settings_store(v2_env, monkeypatch):
    from agentic import wiring
    from agentic.results.base import SaveOutcome
    from agentic.settings.base import InMemorySettingsStore, load_defs

    built, saved = [], []

    def store(s, conn=None):
        st = InMemorySettingsStore(load_defs(s.config_dir))
        st.append("notable_strengths_enabled", False, "admin")
        built.append(conn)
        return st

    class Writer:
        def save(self, result):
            saved.append(result)
            return SaveOutcome(status="written", review_run_id="7")

    monkeypatch.setattr(wiring, "build_settings_store", store)
    monkeypatch.setattr(wiring, "build_results_writer", lambda s: Writer())
    out = v2_env / "plain.json"
    assert main(_v2("--json", str(out))) == 0
    assert built == []  # without --persist: TOML defaults, no store
    assert main(_v2("--persist")) == 0
    assert built == [None]  # FAKE_LLM: the in-memory store, no connection
    assert saved[0].settings_hash != json.loads(out.read_text(encoding="utf-8"))["settings_hash"]


def test_review_course_reasoning_reaches_resolved_options_and_changes_tuning_hash(v2_env, monkeypatch):
    from agentic import wiring

    seen = []
    real = wiring.resolve_options
    monkeypatch.setattr(wiring, "resolve_options", lambda s, o=None, store=None: seen.append(o) or real(s, o, store))
    base, low = v2_env / "base.json", v2_env / "low.json"
    assert main(_v2("--json", str(base), "--profile", "lean")) == 0
    assert main(_v2("--json", str(low), "--profile", "lean", "--reasoning", "3-5-all=low")) == 0
    assert seen[-1].reasoning == {"3-5-all": "low"}
    a, b = (json.loads(p.read_text(encoding="utf-8")) for p in (base, low))
    assert a["tuning_hash"] != b["tuning_hash"]


def test_review_course_bad_reasoning_exits_2_naming_it(v2_env, capsys):
    assert main(_v2("--profile", "lean", "--reasoning", "no-such-reviewer=low")) == 2
    assert "no-such-reviewer" in capsys.readouterr().err
    assert main(_v2("--profile", "lean", "--reasoning", "3-5-all=extreme")) == 2
    assert "extreme" in capsys.readouterr().err
    assert main(_v2("--reasoning", "3-5-all")) == 2
    assert "--reasoning" in capsys.readouterr().err


# --- analyze ---------------------------------------------------------------------------------------
from datetime import datetime, timedelta, timezone  # noqa: E402

from agentic.analytics.memory import InMemoryAnalyticsWriter, InMemoryResultsReader  # noqa: E402
from agentic.analytics.models import LinkRow, VerdictRow  # noqa: E402

A_CONCEPT = "9-10.W.3.A"
A_T0 = datetime(2026, 9, 1, 12, tzinfo=timezone.utc)
A_IDS = ["aaaa1111" + "0" * 8, "aaaa2222" + "0" * 8, "bbbb3333" + "0" * 8, "cccc4444" + "0" * 8]


def _vr(student, n, level, strength=False):
    due = A_T0 + timedelta(days=7 * n)
    return VerdictRow(student_id=student, classroom_id="c1", submission_id=f"{student}-{n}", due_at=due,
                      submitted_at=due, submission_type="essay", standard_code=A_CONCEPT + ".a",
                      verdict="growth_area" if level else "meets",
                      support_level={1: "approaching", 2: "developing", 3: "beginning"}.get(level),
                      notable_strength=strength, confidence=0.8,
                      finding_condition="with a prompt" if level else None, overruled=False)


@pytest.fixture
def analyze_env(monkeypatch):
    from agentic import wiring

    monkeypatch.setenv("FAKE_LLM", "1")
    rows = [_vr(s, n, 2) for s in A_IDS[:3] for n in range(3)]
    rows += [_vr(A_IDS[3], n, 0, strength=(n == 3)) for n in range(4)]
    links = [LinkRow(A_IDS[0], f"{A_IDS[0]}-2", A_CONCEPT, "7.W.3.A"),
             LinkRow(A_IDS[2], f"{A_IDS[2]}-1", A_CONCEPT, "7.W.3.A")]
    reader = InMemoryResultsReader(rows, links, {"c1"}, courses={"classroom_1.01": "c1"})
    writer = InMemoryAnalyticsWriter()
    monkeypatch.setattr(wiring, "build_analytics_reader", lambda s: reader)
    monkeypatch.setattr(wiring, "build_analytics_writer", lambda s: writer)
    return writer


def test_analyze_prints_counts_gaps_groups_and_pairs(analyze_env, capsys):
    assert main(["analyze", "--classroom", "1.01", "--as-of", "2026-12-31", "--no-save"]) == 0
    out = capsys.readouterr().out
    assert "needs_attention 3" in out and "secure 1" in out
    assert f"{A_CONCEPT}: 3 students" in out and "with a prompt" in out
    assert f"7.W.3.A <- {A_CONCEPT}: 2 students" in out
    assert f"{A_CONCEPT}: cccc4444 -> " in out
    assert A_IDS[3] not in out  # short hashes only
    assert analyze_env.saved == []  # --no-save uses its own in-memory writer


def test_analyze_saves_through_the_writer_by_default(analyze_env, capsys):
    assert main(["analyze", "--classroom", "1.01", "--as-of", "2026-12-31"]) == 0
    assert len(analyze_env.saved) == 1 and analyze_env.saved[0].run.trigger == "manual"
    end = analyze_env.saved[0].run.as_of
    assert (end.hour, end.minute, end.second, end.microsecond) == (23, 59, 59, 999999)
    assert end.utcoffset() == timedelta(hours=-6)  # America/Chicago in December


def test_analyze_as_of_before_any_work_is_empty_and_succeeds(analyze_env, capsys):
    assert main(["analyze", "--classroom", "1.01", "--as-of", "2026-01-01", "--no-save"]) == 0
    out = capsys.readouterr().out
    assert "statuses: 0" in out and "groups" not in out.replace("Reteach groups (0)", "")


def test_analyze_unknown_classroom_exits_2(analyze_env, capsys):
    assert main(["analyze", "--classroom", "9.99", "--no-save"]) == 2
    assert "9.99" in capsys.readouterr().err


def test_analyze_student_prefix(analyze_env, capsys):
    assert main(["analyze", "--classroom", "1.01", "--student", "bbbb", "--as-of", "2026-12-31",
                 "--no-save"]) == 0
    out = capsys.readouterr().out
    assert "statuses: 1" in out and "Reteach" not in out
    assert "Pair suggestions" not in out and "Top gaps" not in out


@pytest.mark.parametrize("prefix", ["aaaa", "zzzz"])
def test_analyze_ambiguous_or_unknown_student_exits_2(analyze_env, capsys, prefix):
    assert main(["analyze", "--classroom", "1.01", "--student", prefix, "--as-of", "2026-12-31",
                 "--no-save"]) == 2
    assert prefix in capsys.readouterr().err


def test_analyze_bad_date_exits_2(analyze_env, capsys):
    assert main(["analyze", "--classroom", "1.01", "--as-of", "yesterday", "--no-save"]) == 2


def test_analyze_failure_prints_the_class_name_only(analyze_env, monkeypatch, capsys):
    import agentic.__main__ as cli

    def boom(*a, **k):
        raise RuntimeError("secret essay text")

    monkeypatch.setattr(cli, "analyze_classroom", boom)
    assert main(["analyze", "--classroom", "1.01", "--no-save"]) == 1
    err = capsys.readouterr().err
    assert "RuntimeError" in err and "secret" not in err

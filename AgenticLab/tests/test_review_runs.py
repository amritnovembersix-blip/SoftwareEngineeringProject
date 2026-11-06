"""Lab review runs: run one v2 review, save it, build display tables, diff two runs (FAKE_LLM, fixture course)."""
import agentic
import json
import shutil
from pathlib import Path

import pytest

from agentic.config import Settings
from agentic.models import FindingLink
from agentic.review.v2.options import ReviewOptions
from agentic_lab import review_runs

FIXTURE = Path(agentic.__file__).resolve().parents[2] / "tests" / "data" / "coursedir"
VERSION, ASSIGNMENT, STUDENT = "0.90-control", "ela9v2-a02", "a1a1a1a1a1a1a1a1"
SUBMISSION = f"sub-control-{ASSIGNMENT}-{STUDENT[:8]}"
MISSED = "9-10.W.9.Z"  # planted, never reported by the fake model


@pytest.fixture
def s(tmp_path, monkeypatch) -> Settings:
    data = tmp_path / "data"
    shutil.copytree(FIXTURE, data)
    for k, v in {"FAKE_LLM": "1", "DATA_DIR": str(data)}.items():
        monkeypatch.setenv(k, v)
    return Settings.load()


@pytest.fixture
def result(s):
    return review_runs.run_review(s, VERSION, ASSIGNMENT, STUDENT[:4], ReviewOptions())


def _key(s: Settings, planted: list[tuple[str, int]]) -> None:
    path = Path(s.data_dir) / "courses" / VERSION / "answer_key" / "expected" / f"{ASSIGNMENT}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {"student_hash": STUDENT, "submission_id": SUBMISSION,
           "weaknesses": [{"standard": c, "level": lv} for c, lv in planted]}
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")


def _growth(result) -> list[str]:
    return [v.standard_code for v in result.verdicts if v.verdict == "growth_area"]


def test_run_review_is_v2_and_resolves_prefix(result):
    assert result.pipeline == "v2"
    assert result.submission_ref.user_id == STUDENT
    assert result.trace["reviewers"]


def test_run_review_unknown_student_or_course(s):
    with pytest.raises(LookupError):
        review_runs.run_review(s, VERSION, ASSIGNMENT, "zzzz", ReviewOptions())
    with pytest.raises(LookupError):
        review_runs.run_review(s, "9.99-none", ASSIGNMENT, "a1a1", ReviewOptions())


def test_run_review_persists_nothing(s, monkeypatch):
    import psycopg
    from agentic import wiring

    def no_db(*a, **k):
        raise AssertionError("opened a database connection")

    monkeypatch.setattr(psycopg, "connect", no_db)
    monkeypatch.setattr(wiring, "build_results_writer", no_db)
    review_runs.run_review(s, VERSION, ASSIGNMENT, "a1a1", ReviewOptions(profile="lean"))


def test_save_run_includes_trace_and_two_saves_do_not_collide(result, tmp_path):
    runs = tmp_path / "runs"
    first = review_runs.save_run(result, ReviewOptions(profile="lean"), runs)
    second = review_runs.save_run(result, ReviewOptions(), runs)
    assert first != second and len(list(runs.glob("*.json"))) == 2
    data = json.loads(first.read_text(encoding="utf-8"))
    assert set(data) == {"saved_at", "course", "assignment", "student", "options", "result"}
    assert (data["course"], data["assignment"], data["student"]) == (VERSION, ASSIGNMENT, STUDENT)
    assert data["options"]["profile"] == "lean"
    assert data["result"]["trace"]["reviewers"]


def test_list_runs_newest_first_and_load_run(result, tmp_path):
    runs = tmp_path / "runs"
    assert review_runs.list_runs(runs) == []  # a folder that does not exist yet
    paths = [review_runs.save_run(result, ReviewOptions(), runs) for _ in range(3)]
    assert review_runs.list_runs(runs) == paths[::-1]
    saved = review_runs.load_run(paths[0])
    assert saved.result.verdicts == result.verdicts
    assert saved.options == ReviewOptions()
    assert saved.student == STUDENT


def test_view_has_a_row_per_reviewer_and_growth_rows(s, result):
    v = review_runs.view(result, s)
    reviewers = [r["reviewer"] for r in v["verdicts"]]
    assert reviewers == list(dict.fromkeys(x.reviewer for x in result.verdicts))
    assert all(set(r) >= {"reviewer", "meets", "growth area", "not evident"} for r in v["verdicts"])
    assert {g["code"] for g in v["growth_areas"]} == set(_growth(result))
    g = v["growth_areas"][0]
    assert set(g) >= {"code", "reviewer", "support level", "confidence", "condition", "pattern",
                      "skill state", "segments", "summary"}
    assert [r["reviewer"] for r in v["usage"]][:len(result.usage)] == list(result.usage)
    assert set(v["usage"][0]) >= {"reviewer", "prompt", "input tokens", "output tokens", "seconds"}
    assert v["totals"]["calls"] == result.llm_calls
    text = json.dumps(v).lower()
    assert "weakness" not in text and "severity" not in text


def test_view_never_carries_submission_text(s, result):
    assert "A key." not in json.dumps(review_runs.view(result, s))


def test_view_links_and_strengths(s, result):
    code = _growth(result)[0]
    linked = result.model_copy(update={"links": [FindingLink(
        finding_code=code, root_code="3.W.1.A", rationale="Same gap in an earlier grade")]})
    v = review_runs.view(linked, s)
    assert v["links"] == [{"growth area": code, "root": "3.W.1.A", "rationale": "Same gap in an earlier grade"}]
    strong = next(x for x in result.verdicts if x.verdict == "meets").model_copy(
        update={"notable_strength": True, "strength_note": "Clear voice", "confidence": 0.9})
    with_strength = result.model_copy(update={"verdicts": [strong, *result.verdicts[1:]]})
    rows = review_runs.view(with_strength, s)["strengths"]
    assert {"code": strong.standard_code, "note": "Clear voice", "confidence": 0.9} in rows


def test_view_cost_priced_by_the_results_models(s, result):
    assert result.models and set(result.models) >= set(result.usage) - {"root_cause"}
    assert review_runs.view(result, s)["totals"]["unpriced_models"] == ["fake-chat"]  # FAKE_LLM's model
    priced = result.model_copy(update={"models": {k: "gemini-2.5-flash" for k in result.models}})
    unknown = s.model_copy(update={"llm_model": "no-such-model"})  # the result's models win
    cost = review_runs.view(priced, unknown)["totals"]["usd"]
    assert cost is not None and cost > 0


def test_view_cost_of_a_run_saved_before_models_uses_profile_else_settings_model(s, result):
    legacy = result.model_copy(update={"models": {}})
    s_priced = s.model_copy(update={"llm_model": "gemini-2.5-flash"})
    cost = review_runs.view(legacy, s_priced)["totals"]["usd"]
    assert cost is not None and cost > 0
    unknown = s.model_copy(update={"llm_model": "no-such-model"})
    assert review_runs.view(legacy, unknown)["totals"]["usd"] is None


def test_expected_for_reads_answer_key(s):
    _key(s, [("3.L.1.A.a", 2), (MISSED, 1)])
    assert review_runs.expected_for(s, VERSION, ASSIGNMENT, STUDENT) == {"3.L.1.A.a", MISSED}
    assert review_runs.expected_for(s, VERSION, ASSIGNMENT, "b2b2b2b2b2b2b2b2") == set()


def test_expected_for_without_a_key_is_none(s):
    assert review_runs.expected_for(s, VERSION, ASSIGNMENT, STUDENT) is None


def test_view_marks_planted_and_lists_missed(s, result):
    hit = _growth(result)[0]
    v = review_runs.view(result, s, expected={hit, MISSED})
    by_code = {g["code"]: g["planted"] for g in v["growth_areas"]}
    assert by_code[hit] == "planted"
    assert all(p == "not planted" for c, p in by_code.items() if c != hit)
    assert v["missed"] == [MISSED]
    assert "planted" not in review_runs.view(result, s)["growth_areas"][0]  # no key shown, no column


def test_diff_identical_runs_is_empty(result):
    d = review_runs.diff_runs(result, ReviewOptions(), result, ReviewOptions())
    assert d["verdicts"] == [] and d["only_a"] == [] and d["only_b"] == []
    assert d["links_only_a"] == [] and d["links_only_b"] == [] and d["options"] == {}
    assert d["tokens"]["a"] == d["tokens"]["b"]


def test_diff_reports_changed_verdict_options_and_links(result):
    code = _growth(result)[0]
    changed = [v.model_copy(update={"verdict": "meets", "support_level": None}) if v.standard_code == code else v
               for v in result.verdicts]
    link = FindingLink(finding_code="9-10.W.1.A", root_code="3.W.1.A", rationale="Same gap earlier")
    other = result.model_copy(update={"verdicts": changed, "links": [link], "llm_calls": result.llm_calls + 2})
    d = review_runs.diff_runs(result, ReviewOptions(), other, ReviewOptions(profile="lean"))
    row = next(r for r in d["verdicts"] if r["code"] == code)
    assert (row["verdict A"], row["verdict B"]) == ("growth area", "meets")
    assert row["support level A"] == "developing" and row["support level B"] in (None, "")
    assert [r["code"] for r in d["verdicts"]] == [code]
    assert d["links_only_b"] == [{"growth area": "9-10.W.1.A", "root": "3.W.1.A"}]
    assert d["options"] == {"profile": [None, "lean"]}
    assert d["calls"] == {"a": result.llm_calls, "b": result.llm_calls + 2}


def test_diff_growth_areas_only_in_one_run(result):
    code = _growth(result)[0]
    dropped = [v for v in result.verdicts if v.standard_code != code]
    other = result.model_copy(update={"verdicts": dropped})
    d = review_runs.diff_runs(result, ReviewOptions(), other, ReviewOptions())
    assert d["only_a"] == [code] and d["only_b"] == []


def test_choices_for_the_ui(s):
    assert VERSION in review_runs.course_versions(s)
    assert ASSIGNMENT in [a for a, _ in review_runs.assignment_choices(s, VERSION)]
    students = review_runs.student_choices(s, VERSION, ASSIGNMENT)
    assert STUDENT in [h for h, _ in students]
    assert review_runs.student_choices(s, VERSION, "ela9v2-nothing") == []  # nothing submitted
    assert review_runs.course_versions(s.model_copy(update={"data_dir": Path(s.data_dir) / "absent"})) == []


def test_usage_rows_show_thinking_and_the_reasoning_level(s, result):
    from agentic.llm.base import Usage

    usage = {k: Usage(input_tokens=10, output_tokens=100, thinking_tokens=60, estimated=False)
             for k in result.usage}
    models = {k: "gemini-3-flash-preview@low" for k in result.usage}
    v = review_runs.view(result.model_copy(update={"usage": usage, "models": models}), s)
    assert v["usage"][0]["thinking tokens"] == 60 and v["usage"][0]["model"] == "gemini-3-flash-preview@low"
    assert v["totals"]["thinking_tokens"] == 60 * len(usage)
    assert v["totals"]["unpriced_models"] == [] and v["totals"]["usd"] is not None  # priced as the base model

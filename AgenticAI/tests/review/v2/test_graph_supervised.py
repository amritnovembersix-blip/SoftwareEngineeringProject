"""Review supervisor, Task 5: the planner, critic and arbiter in the v2 graph (fakes only)."""
import json
import re
from pathlib import Path

import pytest
from fakes import essay_row, label_row, write_essays, write_labels

from agentic import wiring
from agentic.__main__ import main
from agentic.config import Settings
from agentic.review.v2.options import ReviewOptions
from agentic.review.v2.run import review_one
from agentic.review.v2.supervisor import (
    ArbiterOut, CriticCall, CriticOut, PlanChoice, Planner, PlannerOut,
)

PARAS = ["Secret essay sentence alpha.", "Secret essay sentence beta.", "Secret essay sentence gamma."]
USER = "a" * 64
SKIPPABLE = ["3-writing", "4-writing", "5-writing", "3-reading", "4-reading", "5-reading"]


@pytest.fixture(autouse=True)
def _fresh_planner_cache():
    Planner.clear_cache()
    yield
    Planner.clear_cache()


def _chat(monkeypatch, extra=None):
    c = wiring.demo_fake_chat(extra)
    monkeypatch.setattr(wiring, "build_chat", lambda _s: c)
    return c


def _settings(tmp_path, profile="supervised"):
    write_labels(tmp_path, "1.01", [label_row(grade=9)])
    write_essays(tmp_path, "1.01", [essay_row(paragraphs=PARAS, course_id="classroom_1.01",
                                              assignment_id="asg-classroom_1.01-essay1")])
    return Settings(fake_llm=True, data_dir=tmp_path, classroom="1.01", review_pipeline="v2", bands_profile=profile)


def _review(s, **options):
    return review_one(s, "classroom_1.01", "asg-classroom_1.01-essay1", USER, ReviewOptions(**options))


def _pairs(res):
    return [(v.standard_code, v.verdict) for v in res.verdicts]


def test_switches_off_gives_the_same_verdicts_as_graded(tmp_path, monkeypatch):
    _chat(monkeypatch)
    graded = _review(_settings(tmp_path), profile="graded")
    off = _review(_settings(tmp_path), profile="supervised", planner=False, critic=False, arbiter=False)
    assert sorted(_pairs(graded)) == sorted(_pairs(off))
    assert "supervisor" not in off.trace and "stages" not in off.trace


def test_skipped_reviewer_standards_are_not_evident_and_never_sent(tmp_path, monkeypatch):
    skip = PlannerOut(choices=[PlanChoice(reviewer=r, action="skip", reason="Not exercised.") for r in SKIPPABLE])
    chat = _chat(monkeypatch, {PlannerOut: lambda _s, _u: skip})
    res = _review(_settings(tmp_path), critic=False, arbiter=False)
    planner = [v for v in res.verdicts if v.reviewer == "planner"]
    assert planner and all(v.verdict == "not_evident" for v in planner)
    assert {v.standard_code.split(".")[1] for v in planner} <= {"W", "R"}
    assert not {r for r in res.usage} & set(SKIPPABLE)
    assert res.trace["supervisor"]["planner"]["skipped"] == {r: "Not exercised." for r in SKIPPABLE}
    assert {s.code for s in res.standards} >= {v.standard_code for v in planner}
    assert sum(1 for c in chat.calls if c[0] is PlannerOut) == 1


def test_critic_then_arbiter_and_stages_in_trace(tmp_path, monkeypatch):
    def drop_first(_s, user):
        code = user.split("\n- ", 1)[1].split(" |", 1)[0]
        return CriticOut(calls=[CriticCall(standard=code, action="drop", outcome="not_shown", reason="Not shown.")])
    chat = _chat(monkeypatch, {CriticOut: drop_first})
    res = _review(_settings(tmp_path))
    order = [c[0] for c in chat.calls if c[0] in (CriticOut, ArbiterOut)]
    assert order[0] is CriticOut and ArbiterOut in order
    assert list(res.trace["stages"]) == ["reviewers", "critic", "arbiter"]
    dropped = res.trace["supervisor"]["changes"][0]["standard"]
    assert dropped in res.trace["stages"]["reviewers"] and dropped not in res.trace["stages"]["critic"]


def test_usage_models_prompt_versions_record_the_steps(tmp_path, monkeypatch):
    _chat(monkeypatch)
    res = _review(_settings(tmp_path))
    assert {"planner", "critic", "arbiter"} <= set(res.usage) and {"planner", "critic", "arbiter"} <= set(res.models)
    assert res.prompt_versions["critic"] == "critic.v1" and res.prompt_versions["arbiter"] == "arbiter.v1"
    assert res.llm_calls >= 3 + len([k for k in res.usage if k not in ("planner", "critic", "arbiter")])


def test_trace_holds_no_segment_text(tmp_path, monkeypatch):
    _chat(monkeypatch)
    res = _review(_settings(tmp_path))
    text = json.dumps(res.trace["supervisor"]) + json.dumps(res.trace["stages"])
    assert not any(p[:20] in text for p in PARAS)


SLICE = Path(__file__).parents[4] / "agentic_lab" / "data" / "synthetic" / "courses" / "2.00-control" / "submissions"


@pytest.mark.skipif(not SLICE.exists(), reason="the 2.00 slice data is not generated yet")
def test_review_cli_accepts_the_step_switches(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_LLM", "1")
    monkeypatch.setenv("DATA_DIR", str(SLICE.parents[2]))
    first = next((SLICE / "ela9v2-a02.jsonl").open(encoding="utf-8"))
    prefix = re.search(r'"student_hash": "([0-9a-f]{8})', first).group(1)
    out = tmp_path / "out.json"
    assert main(["review", "--course", "2.00-control", "--assignment", "ela9v2-a02", "--student", prefix,
                 "--profile", "supervised", "--no-critic", "--no-arbiter", "--json", str(out)]) == 0
    trace = json.loads(out.read_text(encoding="utf-8"))["trace"]
    assert list(trace["stages"]) == ["reviewers"]


@pytest.mark.skipif(not SLICE.exists(), reason="the 2.00 slice data is not generated yet")
def test_multiple_choice_is_untouched(tmp_path, monkeypatch):
    chat = _chat(monkeypatch)
    monkeypatch.setenv("FAKE_LLM", "1")
    monkeypatch.setenv("DATA_DIR", str(SLICE.parents[2]))
    first = next((SLICE / "ela9v2-a01.jsonl").open(encoding="utf-8"))
    prefix = re.search(r'"student_hash": "([0-9a-f]{8})', first).group(1)
    out = tmp_path / "out.json"
    assert main(["review", "--course", "2.00-control", "--assignment", "ela9v2-a01", "--student", prefix,
                 "--profile", "supervised", "--json", str(out)]) == 0
    res = json.loads(out.read_text(encoding="utf-8"))
    assert res["submission_type"] == "multiple_choice" and "supervisor" not in res["trace"]
    assert not [c for c in chat.calls if c[0] in (PlannerOut, CriticOut, ArbiterOut)]


def test_a_failed_planner_is_flagged_invalid_in_the_trace(tmp_path, monkeypatch):
    def boom(_s, _u):
        raise RuntimeError("x")
    _chat(monkeypatch, {PlannerOut: boom})
    res = _review(_settings(tmp_path), critic=False, arbiter=False)
    assert res.trace["supervisor"]["invalid"] == ["planner"]
    assert res.trace["supervisor"]["planner"]["skipped"] == {}

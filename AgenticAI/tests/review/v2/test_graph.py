"""v2 graph end to end, with fakes only (no database, no network)."""
import re
from pathlib import Path

import pytest
from fakes import essay_row, label_row, write_essays, write_labels

from agentic import wiring
from agentic.config import Settings
from agentic.decisions.fake import FakeDecisionModel
from agentic.llm.fake import FakeChatModel, FakeEmbedder
from agentic.lms.base import Assignment, Course, Submission
from agentic.lms.coursedir_client import CourseDirLMSClient
from agentic.models import StandardRef, SubmissionRef
from agentic.review.v2.graph import ReviewerV2, build_v2_graph
from agentic.review.v2.options import ReviewOptions
from agentic.review.v2.run import review_one
from agentic.review.v2.schemas import (
    PerStandardOut, ProgressionOut, RootCauseOut, SegmentEvidence, StandardJudgment, StepOut,
)
from agentic.review.v2.segment import segment
from agentic.settings.base import InMemorySettingsStore, load_defs
from agentic.standards.fake import InMemoryStandards
from agentic.tuning import Profile, ReviewerSpec, load_prompt

COURSEDIR = Path(__file__).parents[2] / "data" / "coursedir"
PARAS = ["Secret essay sentence alpha.", "Secret essay sentence beta.", "Secret essay sentence gamma."]
USER = "a" * 64
BANDS = (PerStandardOut, ProgressionOut)


@pytest.fixture
def chat(monkeypatch):
    """The demo fake chat, shared so a test can count its calls."""
    c = wiring.demo_fake_chat()
    monkeypatch.setattr(wiring, "build_chat", lambda _s: c)
    return c


def _jsonl(tmp_path, grade=9):
    write_labels(tmp_path, "1.01", [label_row(grade=grade)])
    write_essays(tmp_path, "1.01", [essay_row(paragraphs=PARAS, course_id="classroom_1.01",
                                              assignment_id="asg-classroom_1.01-essay1")])
    return Settings(fake_llm=True, data_dir=tmp_path, classroom="1.01", review_pipeline="v2", bands_profile="full")


def _review_jsonl(s, **options):
    return review_one(s, "classroom_1.01", "asg-classroom_1.01-essay1", USER, ReviewOptions(**options))


def _band_calls(chat):
    return [c for c in chat.calls if c[0] in BANDS]


def test_grade_9_essay_full_profile_makes_five_band_calls(tmp_path, chat):
    res = _review_jsonl(_jsonl(tmp_path))
    assert len(_band_calls(chat)) == 5
    assert res.pipeline == "v2" and res.submission_type == "essay"
    assert set(res.usage) >= {"3-5-language", "3-5-writing", "3-5-reading", "6+-writing", "6+-reading"}
    assert res.verdicts and res.findings and res.tuning_hash and res.settings_hash
    assert all(f.support_level == f.severity for f in res.findings)
    # per standard: developing; progressions (fake): approaching on the first unmet step, developing after
    assert {f.support_level for f in res.findings} == {"approaching", "developing"}


def test_lean_profile_makes_two_band_calls(tmp_path, chat):
    res = _review_jsonl(_jsonl(tmp_path), profile="lean")
    assert len(_band_calls(chat)) == 2
    assert set(res.trace["reviewers"]) == {"3-5-all", "6+-all"}


def test_trace_has_an_entry_per_reviewer(tmp_path, chat):
    res = _review_jsonl(_jsonl(tmp_path))
    ids = {"3-5-language", "3-5-writing", "3-5-reading", "6+-writing", "6+-reading"}
    assert set(res.trace["reviewers"]) == ids
    for entry in res.trace["reviewers"].values():
        assert set(entry) == {"prompt", "standards_count", "tokens", "seconds", "rounds", "reply"}
        assert entry["standards_count"] > 0 and entry["tokens"] > 0 and entry["reply"]
    assert res.prompt_versions["6+-writing"] == "progression.v2"
    assert res.prompt_versions["root_cause"] == "root_cause.v1"


def test_jsonl_essay_runs_rl_and_ri_progressions(tmp_path, chat):
    """Review Focus 2: legacy data has no submission_type and no reading_strand."""
    res = _review_jsonl(_jsonl(tmp_path))
    reading = [u for schema, _, u in chat.calls if schema is ProgressionOut and "## RL." in u]
    assert len(reading) == 1 and "## RI." in reading[0]
    codes = {v.standard_code.split(".")[1] for v in res.verdicts if v.reviewer == "6+-reading"}
    assert codes == {"RL", "RI"}


def test_graded_profile_runs_the_6plus_search_with_the_demo_fake(tmp_path, chat):
    s = _jsonl(tmp_path).model_copy(update={"bands_profile": "graded"})
    res = _review_jsonl(s)
    searches = [u for schema, _, u in chat.calls if schema is StepOut]
    assert searches and all(schema is not ProgressionOut for schema, _, _ in chat.calls)
    trace = res.trace["reviewers"]
    assert trace["6+-writing"]["prompt"] == "progression.v3" and trace["6+-writing"]["rounds"] >= 2
    assert trace["3-writing"]["rounds"] == 0
    six = [v for v in res.verdicts if v.reviewer == "6+-writing"]
    assert {v.verdict for v in six} == {"meets", "growth_area"}
    assert {v.working_grade for v in six} == {6}  # the fake meets each group at its lowest step


def test_grade_4_course_runs_only_the_3_5_reviewers(tmp_path, chat):
    """Review Focus 1: no progression reviewers below grade 6."""
    res = _review_jsonl(_jsonl(tmp_path, grade=4))
    assert set(res.trace["reviewers"]) == {"3-5-language", "3-5-writing", "3-5-reading"}
    assert all(schema is not ProgressionOut for schema, _, _ in chat.calls)
    assert all(s.grade_min <= 4 for s in res.standards)
    assert len(_band_calls(chat)) == 3


def test_root_cause_runs_by_default_and_not_when_off(tmp_path, chat):
    s = _jsonl(tmp_path)
    on = _review_jsonl(s)
    assert sum(c[0] is RootCauseOut for c in chat.calls) == 1 and "root_cause" in on.usage
    chat.calls.clear()
    off = _review_jsonl(s, root_cause=False)
    assert not any(c[0] is RootCauseOut for c in chat.calls)
    assert "root_cause" not in off.usage and "root_cause" not in off.prompt_versions
    assert off.tuning_hash != on.tuning_hash  # a test run is never mistaken for a configured run


def test_settings_store_and_overrides_change_the_settings_hash(tmp_path, chat):
    s = _jsonl(tmp_path)
    base = _review_jsonl(s)
    store = InMemorySettingsStore(load_defs(s.config_dir))
    store.append("notable_strengths_enabled", False, "admin")
    stored = review_one(s, "classroom_1.01", "asg-classroom_1.01-essay1", USER, ReviewOptions(), store=store)
    assert stored.settings_hash != base.settings_hash
    assert stored.tuning_hash == base.tuning_hash


def test_verify_maps_probabilities_to_confidence(tmp_path, chat):
    res = _review_jsonl(_jsonl(tmp_path))
    assert res.verification.status == "ok"
    assert all(f.confidence == 0.8 for f in res.findings)
    growth = [v for v in res.verdicts if v.verdict == "growth_area"]
    assert growth and all(v.confidence == 0.8 for v in growth)
    off = _review_jsonl(_jsonl(tmp_path), verify=False)
    assert off.verification.status == "disabled" and all(f.confidence is None for f in off.findings)


def test_unknown_student_is_a_lookup_error(tmp_path, chat):
    with pytest.raises(LookupError):
        review_one(_jsonl(tmp_path), "classroom_1.01", "asg-classroom_1.01-essay1", "z" * 64, ReviewOptions())


# --- course folders: multiple choice and short answer ---------------------------------------

def _coursedir(**update):
    return Settings(fake_llm=True, data_dir=COURSEDIR, lms_source="coursedir", review_pipeline="v2", **update)


def test_multiple_choice_makes_no_chat_calls_and_gives_verdicts(chat):
    res = review_one(_coursedir(), "classroom_0.90-control", "ela9v2-a03@0.90-control",
                     "b2b2b2b2b2b2b2b2", ReviewOptions())
    assert chat.calls == [] and res.llm_calls == 0
    assert res.submission_type == "multiple_choice"
    assert [(v.standard_code, v.verdict) for v in res.verdicts] == [("9-10.RL.1.B", "not_evident")]
    assert [o.level for o in res.observations] == ["item"]  # one pick is an observation, not a finding
    assert res.findings == [] and res.trace == {"reviewers": {}}


def test_multiple_choice_finding_threshold_comes_from_settings(chat):
    s = _coursedir()
    store = InMemorySettingsStore(load_defs(s.config_dir))
    store.append("mc_finding_min_picks", 1, "admin")
    res = review_one(s, "classroom_0.90-control", "ela9v2-a03@0.90-control", "b2b2b2b2b2b2b2b2",
                     ReviewOptions(), store=store)
    assert [f.standard_code for f in res.findings] == ["9-10.RL.1.B"]


def test_short_answer_gives_one_segment_per_answer():
    lms = CourseDirLMSClient(COURSEDIR, "0.90-control")
    a = lms.get_assignment("classroom_0.90-control", "ela9v2-a02@0.90-control")
    sub = lms.get_submission("classroom_0.90-control", "ela9v2-a02@0.90-control", "a1a1a1a1a1a1a1a1")
    sub = sub.model_copy(update={"answers": sub.answers + [{"item_id": "q2", "text": "Second."}]})
    assert segment(sub, a) == (["q1: What did the fox find? — answer: A key.", "q2:  — answer: Second."],
                               "answer")


def test_short_answer_review_cites_answers(chat):
    res = review_one(_coursedir(), "classroom_0.90-control", "ela9v2-a02@0.90-control",
                     "a1a1a1a1a1a1a1a1", ReviewOptions())
    assert res.submission_type == "short_answer"
    prompts = [u for schema, _, u in chat.calls if schema in BANDS]
    assert prompts and all("[S1] q1: What did the fox find? — answer: A key." in u for u in prompts)
    assert res.observations and {o.level for o in res.observations} == {"answer"}


# --- the graph directly ---------------------------------------------------------------------

class _LMS:
    def __init__(self, grade=4):
        self.grade = grade

    def get_course(self, course_id):
        return Course(id=course_id, name="c", subject="ela", grade_level=self.grade)

    def get_assignment(self, course_id, assignment_id):
        return Assignment(id=assignment_id, name="a", description="Write.")

    def get_submission(self, course_id, assignment_id, user_id):
        return Submission(id="s1", user_id=user_id, assignment_id=assignment_id, attempt=1,
                          body="<p>One.</p><p>Two.</p>", submitted_at="2026-09-01T12:00:00Z")


REF = SubmissionRef(course_id="c1", assignment_id="a1", user_id="u1", submission_id="s1", attempt=1)


def _std(code, grade):
    return StandardRef(code=code, concept="Concept", description="Do it.", grade_min=grade, grade_max=grade,
                       strand=code.split(".")[1])


def _spec(id_, strands):
    return ReviewerSpec(id=id_, grades=(3, 5), strands=tuple(strands), mode="per_standard",
                        prompt="per_standard.v1")


def _graph_reviewer(s, profile, refs, chat, decision=None):
    options = ReviewOptions(root_cause=False).resolve(s, {"notable_strengths_enabled": True,
                                                          "mc_finding_min_picks": 2})
    templates = {"per_standard.v1": load_prompt(s.prompts_dir, "per_standard.v1")}
    graph = build_v2_graph(_LMS(), InMemoryStandards(refs, FakeEmbedder()), chat, decision, {}, templates,
                           options, profile)
    return ReviewerV2(graph, options, tuning_hash="t")


def _per_standard_chat(strength=False):
    def handler(_system, user):
        codes = re.findall(r"^- (\S+) \|", user, flags=re.M)
        return PerStandardOut(judgments=[
            StandardJudgment(standard_code=c, verdict="meets", notable_strength=strength,
                             strength_note="Clear and well organized." if strength else None,
                             evidence=[SegmentEvidence(segment=1, reason="Clear topic sentence")])
            for c in codes])
    return FakeChatModel({PerStandardOut: handler})


def test_a_code_judged_by_two_reviewers_raises():
    s = Settings(fake_llm=True, review_pipeline="v2")
    profile = Profile(name="overlap", reviewers=[_spec("one", ["L"]), _spec("two", ["L", "W"])])
    refs = [_std("3.L.1.A.a", 3), _std("4.W.1.A.a", 4)]
    with pytest.raises(ValueError, match=r"3\.L\.1\.A\.a"):
        _graph_reviewer(s, profile, refs, _per_standard_chat()).run(REF)


def test_reviewer_with_no_standards_makes_no_call():
    s = Settings(fake_llm=True, review_pipeline="v2")
    profile = Profile(name="p", reviewers=[_spec("lang", ["L"]), _spec("write", ["W"])])
    chat = _per_standard_chat()
    res = _graph_reviewer(s, profile, [_std("4.W.1.A.a", 4)], chat).run(REF)
    assert len(chat.calls) == 1
    assert res.trace["reviewers"]["lang"]["standards_count"] == 0
    assert [v.standard_code for v in res.verdicts] == ["4.W.1.A.a"]


def test_notable_strengths_are_verified_and_get_confidence():
    s = Settings(fake_llm=True, review_pipeline="v2")
    profile = Profile(name="p", reviewers=[_spec("write", ["W"])])
    decision = FakeDecisionModel(lambda k: 0.8 if k.startswith("finding:") else 0.6)
    res = _graph_reviewer(s, profile, [_std("4.W.1.A.a", 4)], _per_standard_chat(strength=True),
                          decision).run(REF)
    assert [m.standard_code for m in res.met_standards] == ["4.W.1.A.a"]
    assert res.met_standards[0].p_met == 0.6
    assert res.verdicts[0].notable_strength and res.verdicts[0].confidence == 0.6


def test_build_reviewer_dispatches_on_pipeline(tmp_path):
    s = _jsonl(tmp_path)
    assert isinstance(wiring.build_reviewer(s), ReviewerV2)
    assert not isinstance(wiring.build_reviewer(s.model_copy(update={"review_pipeline": "mvp"})), ReviewerV2)


def test_review_pipeline_defaults_to_v2():
    assert Settings().review_pipeline == "v2"


def test_a_failing_band_fails_the_whole_review():
    """Spec section 4: no partial verdicts. The error class propagates; callers record only it."""
    from agentic.llm.base import DailyQuotaExceeded

    s = Settings(fake_llm=True, review_pipeline="v2")
    profile = Profile(name="p", reviewers=[_spec("write", ["W"])])
    for error in (RuntimeError("Secret essay sentence alpha."), DailyQuotaExceeded()):
        def boom(_system, _user, error=error):
            raise error
        with pytest.raises(type(error)):
            _graph_reviewer(s, profile, [_std("4.W.1.A.a", 4)], FakeChatModel({PerStandardOut: boom})).run(REF)


def test_prompt_version_override_changes_prompts_and_tuning_hash(tmp_path, chat):
    import shutil

    s = _jsonl(tmp_path)
    prompts = tmp_path / "prompts"
    shutil.copytree(s.prompts_dir, prompts)
    (prompts / "per_standard.v2.md").write_text(
        (prompts / "per_standard.v1.md").read_text(encoding="utf-8") + "\nBe brief.\n", encoding="utf-8")
    s = s.model_copy(update={"prompts_dir": prompts})
    base = _review_jsonl(s)
    tuned = _review_jsonl(s, prompt_versions={"3-5-writing": "v2"})
    assert tuned.prompt_versions["3-5-writing"] == "per_standard.v2"
    assert tuned.prompt_versions["3-5-language"] == "per_standard.v1"
    assert tuned.trace["reviewers"]["3-5-writing"]["prompt"] == "per_standard.v2"
    assert tuned.tuning_hash != base.tuning_hash
    assert any("Be brief." in u for _, _, u in chat.calls)


def test_a_reviewer_with_its_own_model_uses_its_chat():
    s = Settings(fake_llm=True, review_pipeline="v2")
    own = ReviewerSpec(id="write", grades=(3, 5), strands=("W",), mode="per_standard",
                       prompt="per_standard.v1", model="m2")
    profile = Profile(name="p", reviewers=[_spec("lang", ["L"]), own])
    default, other = _per_standard_chat(), _per_standard_chat()
    options = ReviewOptions(root_cause=False).resolve(s, {"notable_strengths_enabled": True})
    templates = {"per_standard.v1": load_prompt(s.prompts_dir, "per_standard.v1")}
    refs = [_std("4.L.1.A.a", 4), _std("4.W.1.A.a", 4)]
    graph = build_v2_graph(_LMS(), InMemoryStandards(refs, FakeEmbedder()), default, None, {}, templates,
                           options, profile, chats={"m2": other})
    ReviewerV2(graph, options, tuning_hash="t").run(REF)
    assert ["4.L.1.A.a" in u for _, _, u in default.calls] == [True]
    assert ["4.W.1.A.a" in u for _, _, u in other.calls] == [True]


def test_models_name_the_chat_each_reviewer_root_cause_and_verify_used(tmp_path, chat):
    res = _review_jsonl(_jsonl(tmp_path))
    bands = {"3-5-language", "3-5-writing", "3-5-reading", "6+-writing", "6+-reading"}
    assert res.models == {**{b: "fake-chat" for b in bands}, "root_cause": "fake-chat", "verify": "fake-jev"}
    off = _review_jsonl(_jsonl(tmp_path), root_cause=False, verify=False)
    assert set(off.models) == bands
    assert _review_jsonl(_jsonl(tmp_path), pipeline="mvp").models == {}  # the MVP leaves it empty


def test_models_carry_a_reviewers_own_model_and_skip_reviewers_with_no_call():
    s = Settings(fake_llm=True, review_pipeline="v2")
    own = ReviewerSpec(id="write", grades=(3, 5), strands=("W",), mode="per_standard",
                       prompt="per_standard.v1", model="m2")
    profile = Profile(name="p", reviewers=[_spec("lang", ["L"]), own, _spec("read", ["RL"])])
    options = ReviewOptions(root_cause=False, verify=False).resolve(s, {"notable_strengths_enabled": True})
    templates = {"per_standard.v1": load_prompt(s.prompts_dir, "per_standard.v1")}
    refs = [_std("4.L.1.A.a", 4), _std("4.W.1.A.a", 4)]
    other = FakeChatModel(_per_standard_chat()._handlers, model_name="model-two")
    graph = build_v2_graph(_LMS(), InMemoryStandards(refs, FakeEmbedder()), _per_standard_chat(), None, {},
                           templates, options, profile, chats={"m2": other})
    res = ReviewerV2(graph, options, tuning_hash="t").run(REF)
    assert res.models == {"lang": "fake-chat", "write": "model-two"}


def test_review_one_on_the_mvp_pipeline_honors_verify(tmp_path, chat):
    s = _jsonl(tmp_path)
    res = _review_jsonl(s, pipeline="mvp", verify=False)
    assert res.pipeline == "mvp" and res.verification.status == "disabled"
    assert _review_jsonl(s, pipeline="mvp").verification.status == "ok"


def test_growth_summary_stays_within_40_words_with_50_cited_segments():
    from agentic.models import Finding, word_count
    from agentic.review.v2.graph import _summary

    def finding(paragraphs, concept="Concept"):
        return Finding(standard_code="9-10.W.1.A.a", concept=concept, description="d", paragraphs=paragraphs,
                       reasons=[], observation_count=len(paragraphs), summary="")

    many = _summary(finding(list(range(1, 51))))
    assert word_count(many) <= 40 and many.startswith("9-10.W.1.A.a: growth area in S1, S2,")
    assert many.endswith("S30 and 20 more.")
    assert _summary(finding([2, 5])) == "Concept: growth area in S2, S5."
    long_concept = " ".join(["word"] * 36)
    assert _summary(finding([2, 5], long_concept)) == "9-10.W.1.A.a: growth area in S2, S5."


def test_models_carry_the_reasoning_level_unless_default():
    """Task R: "<model>@<level>" for a non-default level; plain model id otherwise."""
    s = Settings(fake_llm=True, review_pipeline="v2")
    own = ReviewerSpec(id="write", grades=(3, 5), strands=("W",), mode="per_standard",
                       prompt="per_standard.v1", model="m2", reasoning="low")
    profile = Profile(name="p", reviewers=[_spec("lang", ["L"]), own, _spec("read", ["RL"])])
    options = ReviewOptions(root_cause=False, verify=False).resolve(s, {"notable_strengths_enabled": True})
    templates = {"per_standard.v1": load_prompt(s.prompts_dir, "per_standard.v1")}
    refs = [_std("4.L.1.A.a", 4), _std("4.W.1.A.a", 4)]
    other = FakeChatModel(_per_standard_chat()._handlers, model_name="model-two")
    other.reasoning = "low"
    graph = build_v2_graph(_LMS(), InMemoryStandards(refs, FakeEmbedder()), _per_standard_chat(), None, {},
                           templates, options, profile, chats={"m2@low": other})
    res = ReviewerV2(graph, options, tuning_hash="t").run(REF)
    assert res.models == {"lang": "fake-chat", "write": "model-two@low"}


def test_a_reasoning_level_alone_selects_its_own_chat():
    s = Settings(fake_llm=True, review_pipeline="v2")
    low = ReviewerSpec(id="write", grades=(3, 5), strands=("W",), mode="per_standard",
                       prompt="per_standard.v1", reasoning="low")
    profile = Profile(name="p", reviewers=[_spec("lang", ["L"]), low])
    default, other = _per_standard_chat(), _per_standard_chat()
    options = ReviewOptions(root_cause=False).resolve(s, {"notable_strengths_enabled": True})
    templates = {"per_standard.v1": load_prompt(s.prompts_dir, "per_standard.v1")}
    refs = [_std("4.L.1.A.a", 4), _std("4.W.1.A.a", 4)]
    graph = build_v2_graph(_LMS(), InMemoryStandards(refs, FakeEmbedder()), default, None, {}, templates,
                           options, profile, chats={"@low": other})
    ReviewerV2(graph, options, tuning_hash="t").run(REF)
    assert ["4.W.1.A.a" in u for _, _, u in other.calls] == [True]
    assert ["4.W.1.A.a" in u for _, _, u in default.calls] == [False]


def test_options_reasoning_reaches_the_effective_profile_and_tuning_hash(tmp_path, chat):
    s = _jsonl(tmp_path)
    base = _review_jsonl(s)
    low = _review_jsonl(s, reasoning={"3-5-writing": "low"})
    assert low.tuning_hash != base.tuning_hash
    assert _review_jsonl(s, reasoning={"3-5-writing": "default"}).tuning_hash == base.tuning_hash

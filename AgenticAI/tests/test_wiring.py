from pathlib import Path

import pytest
from fakes import essay_row, label_row, write_essays, write_labels

from agentic import wiring
from agentic.config import Settings
from agentic.lms.coursedir_client import CourseDirLMSClient
from agentic.lms.jsonl_client import JsonlLMSClient
from agentic.models import SubmissionRef

PARAS = ["Secret essay sentence alpha.", "Secret essay sentence beta.", "Secret essay sentence gamma."]


@pytest.fixture
def env(tmp_path):
    write_labels(tmp_path, "1.01", [label_row(grade=9)])
    write_essays(tmp_path, "1.01", [essay_row(paragraphs=PARAS, course_id="classroom_1.01",
                                              assignment_id="asg-classroom_1.01-essay1")])
    ref = SubmissionRef(course_id="classroom_1.01", assignment_id="asg-classroom_1.01-essay1",
                        user_id="a" * 64, submission_id="sub-1", attempt=1)
    return Settings(fake_llm=True, data_dir=tmp_path, classroom="1.01"), ref


def test_fake_mode_builds_runnable_reviewer(env):
    s, ref = env
    res = wiring.build_reviewer(s).run(ref)
    assert res.strategy == "full"
    assert len(res.findings) == 2
    assert all(f.p_correct == 0.8 for f in res.findings)
    assert all(f.paragraphs == [2] for f in res.findings)
    assert res.verification.status == "ok"
    assert all(m.p_met == 0.6 for m in res.met_standards)


def test_fake_rag_runs(env):
    s, ref = env
    res = wiring.build_reviewer(s, strategy="rag").run(ref)
    assert res.strategy == "rag" and res.llm_calls == 2
    assert len(res.findings) == 1 and res.findings[0].paragraphs == [2]


def test_verify_off_gives_disabled(env):
    s, ref = env
    s = s.model_copy(update={"verify": False})
    assert wiring.build_decision(s) is None
    res = wiring.build_reviewer(s).run(ref)
    assert res.verification.status == "disabled"
    assert all(f.p_correct is None for f in res.findings)


def test_strategy_override(env):
    s, _ = env
    assert wiring.build_strategy(s).name == "full"
    assert wiring.build_strategy(s, "rag").name == "rag"
    s2 = s.model_copy(update={"review_strategy": "rag"})
    assert wiring.build_strategy(s2).name == "rag"
    assert wiring.build_strategy(s2, "full").name == "full"
    with pytest.raises(ValueError):
        wiring.build_strategy(s, "bogus")


def test_demo_chat_is_deterministic_and_short(env):
    s, ref = env
    a = wiring.build_reviewer(s).run(ref)
    b = wiring.build_reviewer(s).run(ref)
    assert [f.reasons for f in a.findings] == [f.reasons for f in b.findings]
    assert all(len(r.split()) <= 12 for f in a.findings for r in f.reasons)


def test_fake_decision_when_provider_fake(env):
    s, _ = env
    s = s.model_copy(update={"fake_llm": False, "decision_provider": "fake"})
    d = wiring.build_decision(s)
    assert d.noul({}, {"finding:x": None, "met:x": None}).probabilities == {"finding:x": 0.8, "met:x": 0.6}


@pytest.mark.parametrize("field,builder,label", [
    ("llm_provider", wiring.build_chat, "LLM_PROVIDER"),
    ("embedding_provider", wiring.build_embedder, "EMBEDDING_PROVIDER"),
    ("decision_provider", wiring.build_decision, "DECISION_PROVIDER"),
])
def test_unknown_provider_raises(env, field, builder, label):
    s, _ = env
    s = s.model_copy(update={"fake_llm": False, field: "x"})
    with pytest.raises(ValueError, match=f"unknown {label} 'x'"):
        builder(s)


def test_fake_llm_forces_fakes_over_providers(env):
    s, _ = env
    s = s.model_copy(update={"llm_provider": "x", "embedding_provider": "x", "decision_provider": "x"})
    wiring.build_chat(s), wiring.build_embedder(s), wiring.build_decision(s)


def test_provider_fake_names_work_without_fake_llm(env):
    s, _ = env
    s = s.model_copy(update={"fake_llm": False, "llm_provider": "fake", "embedding_provider": "fake"})
    assert wiring.build_chat(s).model_name == "fake-chat"
    assert wiring.build_embedder(s).model_name == "fake-embed"


@pytest.mark.parametrize("builder,update,needed", [
    (wiring.build_chat, {"llm_api_key": "", "llm_model": ""}, ["LLM_API_KEY", "LLM_MODEL"]),
    (wiring.build_chat, {"llm_api_key": "SECRETKEY", "llm_model": ""}, ["LLM_MODEL"]),
    (wiring.build_embedder, {"embedding_api_key": "", "embedding_model": ""},
     ["EMBEDDING_API_KEY", "EMBEDDING_MODEL"]),
    (wiring.build_decision, {"jev_api_key": ""}, ["JEV_API_KEY"]),
])
def test_missing_config_raises_naming_env_vars(env, builder, update, needed):
    s, _ = env
    s = s.model_copy(update={"fake_llm": False, **update})
    with pytest.raises(ValueError) as e:
        builder(s)
    assert all(n in str(e.value) for n in needed)
    assert "FAKE_LLM=1" in str(e.value) and "SECRETKEY" not in str(e.value)


def test_reviewer_models(env):
    s, _ = env
    assert wiring.build_reviewer(s).models == {"chat": "fake-chat", "embedder": None, "decision": "fake-jev"}
    assert wiring.build_reviewer(s, strategy="rag").models["embedder"] == "fake-embed"
    off = s.model_copy(update={"verify": False})
    assert wiring.build_reviewer(off).models["decision"] is None


# --- review strands: flag > REVIEW_STRANDS > classroom assignment file > W -------------------

from agentic.dataset import assignment_path  # noqa: E402
from agentic.standards.strand_filter import strand_of  # noqa: E402


def test_strands_default_to_writing(env):
    s, _ = env
    assert wiring.resolve_strands(s) == ["W"]


def test_strands_come_from_classroom_assignment_file(env):
    s, _ = env
    assignment_path(s.data_dir, "1.01").write_text('{"type": "rl", "text": "Animal Farm"}', encoding="utf-8")
    assert wiring.resolve_strands(s) == ["W", "RL"]


def test_review_strands_setting_beats_assignment_file(env):
    s, _ = env
    assignment_path(s.data_dir, "1.01").write_text('{"type": "rl"}', encoding="utf-8")
    assert wiring.resolve_strands(s.model_copy(update={"review_strands": "ri"})) == ["W", "RI"]


def test_explicit_strands_beat_everything(env):
    s, _ = env
    s = s.model_copy(update={"review_strands": "RI"})
    assert wiring.resolve_strands(s, override=["RL", "RI"]) == ["W", "RL", "RI"]


def test_reviewer_reviews_only_resolved_strands(env):
    s, ref = env
    reviewer = wiring.build_reviewer(s, strands=["RL"])
    assert reviewer.strands == ["W", "RL"]
    res = reviewer.run(ref)
    assert res.standards and all(strand_of(x.code) in {"W", "RL"} for x in res.standards)


def test_targets_define_review_scope(env):
    s, ref = env
    assignment_path(s.data_dir, "1.01").write_text(
        '{"type": "rl", "targets": ["9-10.W.2.A", "9-10.W.3.A.d"]}', encoding="utf-8")
    reviewer = wiring.build_reviewer(s)
    assert reviewer.targets == ["9-10.W.2.A", "9-10.W.3.A.d"]
    res = reviewer.run(ref)
    assert sorted(x.code for x in res.standards) == ["9-10.W.2.A", "9-10.W.3.A.d"]


def test_no_targets_means_strand_scope(env):
    s, _ = env
    reviewer = wiring.build_reviewer(s)
    assert reviewer.targets == []
    assert len(wiring.build_review_standards(s).at_grade(9)) == 8


def test_gemini_chats_share_one_rate_limit():
    """Parallel reviews build several chat clients; LLM_RPM must stay one shared ceiling."""
    s = Settings(llm_provider="gemini", llm_model="m", llm_api_key="k", llm_rpm=7)
    a, b = wiring.build_chat(s), wiring.build_chat(s)
    assert a is not b and a._limiter is b._limiter
    other = wiring.build_chat(s.model_copy(update={"llm_rpm": 8}))
    assert other._limiter is not a._limiter


COURSEDIR = Path(__file__).parent / "data" / "coursedir"


def test_build_lms_picks_adapter_by_lms_source(tmp_path):
    assert isinstance(wiring.build_lms(Settings(data_dir=tmp_path, classroom="1.01")), JsonlLMSClient)
    s = Settings(data_dir=COURSEDIR, lms_source="coursedir")
    assert isinstance(wiring.build_lms(s, "0.90-control"), CourseDirLMSClient)
    with pytest.raises(ValueError):  # an unknown source never reaches build_lms
        Settings(data_dir=tmp_path, lms_source="canvas")


def test_build_course_lms_serves_a_coursedir_course():
    s = Settings(data_dir=COURSEDIR, lms_source="coursedir")
    assert wiring.build_course_lms(s, "classroom_0.90-control").get_course("classroom_0.90-control")


def test_list_course_ids_for_coursedir(tmp_path):
    s = Settings(data_dir=COURSEDIR, lms_source="coursedir")
    assert wiring.list_course_ids(s) == ["classroom_0.90-control", "classroom_0.90-intervention"]
    (tmp_path / "courses" / "no-course-json").mkdir(parents=True)
    assert wiring.list_course_ids(Settings(data_dir=tmp_path, lms_source="coursedir")) == []
    assert wiring.list_course_ids(Settings(data_dir=tmp_path / "missing", lms_source="coursedir")) == []


def test_v2_chats_one_per_distinct_reviewer_model(monkeypatch):
    """Ruling T9b: a reviewer's `model` gets its own chat; blank uses the default chat."""
    from agentic.tuning import Profile, ReviewerSpec

    def spec(id_, model):
        return ReviewerSpec(id=id_, grades=(3, 5), strands=("L",), mode="per_standard",
                            prompt="per_standard.v1", model=model)

    built = []
    monkeypatch.setattr(wiring, "build_chat", lambda st: built.append(st.llm_model) or object())
    s = Settings(fake_llm=False, llm_provider="gemini", llm_model="base", llm_api_key="k")
    profile = Profile(name="p", reviewers=[spec("a", None), spec("b", "m2"), spec("c", "m2"), spec("d", "m3")])
    chats = wiring.build_v2_chats(s, profile)
    assert sorted(chats) == ["m2", "m3"] and chats["m2"] is not chats["m3"] and sorted(built) == ["m2", "m3"]
    assert wiring.build_v2_chats(s.model_copy(update={"fake_llm": True}), profile) == {}  # fakes: one chat


def test_v2_chats_one_per_model_and_reasoning_pair_sharing_one_limiter():
    """Task R: same model at two levels = two chats, one rate limit (the limiter is keyed by model)."""
    from agentic.tuning import Profile, ReviewerSpec

    def spec(id_, model, level):
        return ReviewerSpec(id=id_, grades=(3, 5), strands=("L",), mode="per_standard",
                            prompt="per_standard.v1", model=model, reasoning=level)

    s = Settings(fake_llm=False, llm_provider="gemini", llm_model="base", llm_api_key="k", llm_reasoning="high")
    profile = Profile(name="p", reviewers=[
        spec("a", None, "high"),     # the default chat already runs at the global level
        spec("b", None, "low"), spec("c", None, "low"), spec("d", None, "default"),
        spec("e", "m2", "low"), spec("f", "m2", "default")])
    chats = wiring.build_v2_chats(s, profile)
    assert sorted(chats) == ["", "@low", "m2", "m2@low"]
    assert chats["@low"].reasoning == "low" and chats["@low"].model_name == "base"
    assert chats[""].reasoning == "default" and chats["m2@low"].model_name == "m2"
    assert chats["@low"]._limiter is chats[""]._limiter is wiring.build_chat(s)._limiter
    assert chats["m2"]._limiter is chats["m2@low"]._limiter


def test_build_chat_uses_the_global_reasoning_level():
    s = Settings(llm_provider="gemini", llm_model="m", llm_api_key="k", llm_reasoning="medium")
    assert wiring.build_chat(s).reasoning == "medium"
    assert wiring.build_chat(s.model_copy(update={"llm_reasoning": "default"})).reasoning == "default"

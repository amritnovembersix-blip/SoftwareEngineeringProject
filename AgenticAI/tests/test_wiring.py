import pytest
from fakes import essay_row, label_row, write_essays, write_labels

from agentic import wiring
from agentic.config import Settings
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


from agentic.decisions.base import DecisionError
from agentic.models import Finding, MetStandard, StandardRef
from agentic.review.verify import build_request, verify
from agentic.decisions.fake import FakeDecisionModel

STDS = [
    StandardRef(code="A", concept="Thesis", description="dA", grade_min=9, grade_max=10),
    StandardRef(code="B", concept="Evidence", description="dB", grade_min=9, grade_max=10),
    StandardRef(code="C", concept="Tone", description="dC", grade_min=9, grade_max=10),
]
PARAS = ["first para", "second para"]


def findings():
    return [Finding(standard_code="A", concept="Thesis", description="dA", paragraphs=[2],
                    reasons=["vague"], observation_count=1, summary="Thesis: weakness noted in ¶2.")]


def met():
    return [MetStandard(standard_code="B", concept="Evidence"), MetStandard(standard_code="C", concept="Tone")]


def run(decision, threshold=0.5):
    return verify(PARAS, 9, STDS, findings(), met(), decision, threshold)


def test_build_request_shape_and_keys():
    state, questions = build_request(PARAS, 9, STDS, findings(), met())
    assert set(state) == {"grade", "paragraphs", "standards", "findings"}
    assert state["grade"] == 9
    assert state["paragraphs"] == {"1": "first para", "2": "second para"}
    assert state["standards"]["A"] == {"concept": "Thesis", "description": "dA"}
    assert state["findings"] == {"A": {"paragraphs": [2], "reasons": ["vague"]}}
    assert set(questions) == {"finding:A", "met:B", "met:C"}
    q = questions["finding:A"]
    assert isinstance(q.instructions, dict)
    assert "`paragraphs`" in q.instructions["question"]
    assert "`findings.A`" in q.instructions["question"]
    assert "`standards.A`" in q.instructions["question"]
    assert set(q.criteria) == {"true", "false"}
    mq = questions["met:B"]
    assert "`standards.B`" in mq.instructions["question"]
    assert "`grade`" in mq.instructions["question"]
    assert set(mq.criteria) == {"true", "false"}


def test_verify_sets_probabilities_and_possible_miss():
    fake = FakeDecisionModel({"finding:A": 0.9, "met:B": 0.3, "met:C": 0.7})
    f, m, v = run(fake)
    assert f[0].p_correct == 0.9
    assert (m[0].p_met, m[0].possible_miss) == (0.3, True)
    assert (m[1].p_met, m[1].possible_miss) == (0.7, False)
    assert v.status == "ok" and v.model == "fake-jev" and v.input_tokens == 0
    assert len(fake.calls) == 1


def test_verify_disabled_when_no_model():
    f, m, v = run(None)
    assert v.status == "disabled"
    assert f == findings() and m == met()


def test_verify_unavailable_on_error_keeps_review():
    def boom(key):
        raise DecisionError("down")

    f, m, v = run(FakeDecisionModel(boom))
    assert v.status == "unavailable"
    assert f == findings() and m == met()


def test_verify_unavailable_on_any_exception():
    def boom(key):
        raise RuntimeError("x")

    _, _, v = run(FakeDecisionModel(boom))
    assert v.status == "unavailable"


def test_verify_partial_or_invalid_answers():
    fake = FakeDecisionModel({"finding:A": 1.7, "met:C": 0.2})  # met:B missing
    f, m, v = run(fake)
    assert v.status == "ok"
    assert f[0].p_correct is None
    assert (m[0].p_met, m[0].possible_miss) == (None, False)
    assert (m[1].p_met, m[1].possible_miss) == (0.2, True)


def test_verify_nothing_to_verify_skips_request():
    fake = FakeDecisionModel({})
    f, m, v = verify(PARAS, 9, STDS, [], [], fake, 0.5)
    assert v.status == "ok" and fake.calls == []
    assert f == [] and m == []


def test_build_request_with_assignment_context():
    from agentic.lms.base import Assignment

    asg = Assignment(id="a", name="Theme in Animal Farm", description="Choose ONE theme.")
    state, questions = build_request(PARAS, 9, STDS, findings(), met(), asg)
    assert state["assignment"] == {"name": "Theme in Animal Farm", "description": "Choose ONE theme."}
    assert all("`assignment`" in q.instructions["context"] for q in questions.values())
    state, questions = build_request(PARAS, 9, STDS, findings(), met(), None)
    assert "assignment" not in state and all("context" not in q.instructions for q in questions.values())


def test_verify_passes_assignment_to_the_decision_model():
    from agentic.lms.base import Assignment

    fake = FakeDecisionModel(lambda k: 0.5)
    verify(PARAS, 9, STDS, findings(), met(), fake, 0.5,
           assignment=Assignment(id="a", name="T", description="P"))
    state, _ = fake.calls[0]
    assert state["assignment"]["description"] == "P"


def test_build_request_includes_assignment_notes():
    from agentic.lms.base import Assignment

    asg = Assignment(id="a", name="T", description="P", notes={"A": "focus note"})
    state, _ = build_request(PARAS, 9, STDS, findings(), met(), asg)
    assert state["assignment"]["notes"] == {"A": "focus note"}

from agentic.decisions.base import DecisionModel, NoulQuestion
from agentic.decisions.fake import FakeDecisionModel


def test_fake_decision_model_scripted():
    qs = {"finding:A": NoulQuestion(instructions="i"), "met:B": NoulQuestion(instructions="j")}
    fake = FakeDecisionModel({"finding:A": 0.9, "met:B": 0.1})
    assert isinstance(fake, DecisionModel)
    res = fake.noul({"s": 1}, qs)
    assert res.probabilities == {"finding:A": 0.9, "met:B": 0.1}
    assert res.model == "fake-jev"
    assert fake.calls == [({"s": 1}, qs)]


def test_fake_decision_model_callable():
    fake = FakeDecisionModel(lambda key: 0.8 if key.startswith("finding") else 0.2)
    res = fake.noul({}, {"finding:A": NoulQuestion(instructions="i"), "met:B": NoulQuestion(instructions="j")})
    assert res.probabilities == {"finding:A": 0.8, "met:B": 0.2}

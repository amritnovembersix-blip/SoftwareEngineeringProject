import json

import httpx
import pytest

from agentic.decisions.base import DecisionError, DecisionModel, NoulQuestion
from agentic.decisions.jev import JevDecisionModel

OK = {
    "model": "jev-1.13.0",
    "answers": {"is_urgent": {"type": "noul", "noul": 0.95}},
    "usage": {"input_tokens": 296, "output_tokens": 20},
}
Q = {"is_urgent": NoulQuestion(instructions="Does this convey urgency?")}


def make(handler, sleeps=None):
    return JevDecisionModel(
        api_key="k",
        base_url="https://api.typesafe.ai",
        model="jev-latest",
        transport=httpx.MockTransport(handler),
        sleep=(sleeps.append if sleeps is not None else (lambda s: None)),
    )


def test_is_decision_model():
    assert isinstance(make(lambda r: httpx.Response(200, json=OK)), DecisionModel)


def test_request_shape():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["Authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=OK)

    qs = {
        "a": NoulQuestion(instructions="q1"),
        "b": NoulQuestion(instructions="q2", criteria={"true": "yes", "false": "no"}),
    }
    make(handler).noul({"x": 1}, qs)
    assert seen["url"] == "https://api.typesafe.ai/v1/systemone"
    assert seen["auth"] == "Bearer k"
    body = seen["body"]
    assert body["model"] == "jev-latest"
    assert body["state"] == {"x": 1}
    assert body["questions"]["a"] == {"type": "noul", "instructions": "q1"}
    assert "criteria" not in body["questions"]["a"]
    assert body["questions"]["b"]["criteria"] == {"true": "yes", "false": "no"}


def test_parses_noul_answers_and_usage():
    res = make(lambda r: httpx.Response(200, json=OK)).noul({}, Q)
    assert res.probabilities == {"is_urgent": 0.95}
    assert res.model == "jev-1.13.0"
    assert res.input_tokens == 296


def test_retries_429_and_529_then_succeeds():
    codes = iter([429, 529, 200])

    def handler(request):
        code = next(codes)
        return httpx.Response(code, json=OK if code == 200 else {"e": "x"})

    sleeps = []
    res = make(handler, sleeps).noul({}, Q)
    assert res.probabilities == {"is_urgent": 0.95}
    assert sleeps == [2.0, 4.0]


def test_retries_exhausted_raises():
    with pytest.raises(DecisionError, match="jev http 429"):
        make(lambda r: httpx.Response(429, text="SECRET ESSAY")).noul({}, Q)


def test_422_raises_decision_error_without_echoing_state():
    def handler(request):
        return httpx.Response(422, json={"detail": "SECRET ESSAY"})

    with pytest.raises(DecisionError) as ei:
        make(handler).noul({"essay": "SECRET ESSAY"}, Q)
    assert str(ei.value) == "jev http 422"
    assert "SECRET ESSAY" not in str(ei.value)


def test_transport_error_has_no_content():
    def handler(request):
        raise httpx.ConnectError("boom SECRET ESSAY")

    with pytest.raises(DecisionError) as ei:
        make(handler).noul({"essay": "SECRET ESSAY"}, Q)
    assert str(ei.value) == "jev transport error: ConnectError"


def test_non_json_2xx_raises_content_free_error():
    with pytest.raises(DecisionError) as ei:
        make(lambda r: httpx.Response(200, text="SECRET ESSAY not json")).noul({}, Q)
    assert str(ei.value) == "jev malformed response"
    assert "SECRET ESSAY" not in str(ei.value)


def test_2xx_missing_answers_raises():
    bad = {"model": "m", "usage": {"input_tokens": 1}}
    with pytest.raises(DecisionError, match="jev malformed response"):
        make(lambda r: httpx.Response(200, json=bad)).noul({}, Q)


def test_malformed_answer_entry_raises():
    bad = {"model": "m", "usage": {"input_tokens": 1}, "answers": {"is_urgent": "SECRET ESSAY"}}
    with pytest.raises(DecisionError, match="jev malformed response"):
        make(lambda r: httpx.Response(200, json=bad)).noul({}, Q)


def test_missing_answer_key_is_omitted():
    ok = {"model": "m", "usage": {"input_tokens": 1}, "answers": {}}
    res = make(lambda r: httpx.Response(200, json=ok)).noul({}, Q)
    assert res.probabilities == {}

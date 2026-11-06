from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from agentic.llm.base import Usage
from agentic.llm.gemini import GeminiChatModel, GeminiEmbedder


class Out(BaseModel):
    a: int


class HttpErr(Exception):
    def __init__(self, code):
        super().__init__("err")
        self.code = code


class StubModels:
    def __init__(self, responses=None):
        self.calls = []
        self.responses = list(responses or [])

    def generate_content(self, **kw):
        self.calls.append(kw)
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    def embed_content(self, **kw):
        self.calls.append(kw)
        return SimpleNamespace(
            embeddings=[SimpleNamespace(values=[float(len(t))]) for t in kw["contents"]]
        )


def _client(models):
    return SimpleNamespace(models=models)


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr("agentic.llm.ratelimit.time.sleep", lambda s: None)
    monkeypatch.setattr("agentic.llm.gemini.time.sleep", lambda s: None, raising=False)


def test_structured_passes_schema_and_system_and_returns_parsed():
    m = StubModels([SimpleNamespace(parsed=Out(a=1), text=None)])
    chat = GeminiChatModel("gm", "k", rpm=1000, client=_client(m))
    assert chat.structured("SYS", "USER", Out) == Out(a=1)
    kw = m.calls[0]
    assert kw["model"] == "gm" and kw["contents"] == "USER"
    cfg = kw["config"]
    assert cfg.system_instruction == "SYS"
    assert cfg.response_mime_type == "application/json"
    assert cfg.response_schema is Out


def test_structured_dict_and_text_fallback():
    m = StubModels([
        SimpleNamespace(parsed={"a": 2}, text=None),
        SimpleNamespace(parsed=None, text='{"a": 3}'),
    ])
    chat = GeminiChatModel("gm", "k", rpm=1000, client=_client(m))
    assert chat.structured("s", "u", Out).a == 2
    assert chat.structured("s", "u", Out).a == 3


def test_embed_kinds_map_to_task_types_and_dimensions():
    m = StubModels()
    emb = GeminiEmbedder("em", "k", dim=16, rpm=1000, client=_client(m))
    assert emb.embed(["ab"], "document") == [[2.0]]
    emb.embed(["ab"], "query")
    assert m.calls[0]["config"].task_type == "RETRIEVAL_DOCUMENT"
    assert m.calls[1]["config"].task_type == "RETRIEVAL_QUERY"
    assert m.calls[0]["config"].output_dimensionality == 16
    assert m.calls[0]["model"] == "em"


def test_embed_batches_over_100():
    m = StubModels()
    emb = GeminiEmbedder("em", "k", dim=8, rpm=1000, client=_client(m))
    out = emb.embed(["x"] * 150, "document")
    assert len(out) == 150
    assert [len(c["contents"]) for c in m.calls] == [100, 50]


def test_retry_on_429():
    m = StubModels([HttpErr(429), SimpleNamespace(parsed=Out(a=5), text=None)])
    chat = GeminiChatModel("gm", "k", rpm=1000, client=_client(m))
    assert chat.structured("s", "u", Out).a == 5
    assert len(m.calls) == 2


def test_no_retry_on_400():
    m = StubModels([HttpErr(400)])
    chat = GeminiChatModel("gm", "k", rpm=1000, client=_client(m))
    with pytest.raises(HttpErr):
        chat.structured("s", "u", Out)
    assert len(m.calls) == 1


def test_complete_returns_usage_from_metadata():
    meta = SimpleNamespace(prompt_token_count=11, candidates_token_count=5)
    m = StubModels([SimpleNamespace(parsed=Out(a=1), text=None, usage_metadata=meta)])
    chat = GeminiChatModel("gm", "k", rpm=1000, client=_client(m))
    out, usage = chat.complete("s", "u", Out)
    assert out == Out(a=1)
    assert usage == Usage(input_tokens=11, output_tokens=5, estimated=False)


def test_complete_estimates_usage_when_metadata_missing():
    m = StubModels([SimpleNamespace(parsed=Out(a=1), text='{"a": 1}')])
    chat = GeminiChatModel("gm", "k", rpm=1000, client=_client(m))
    _, usage = chat.complete("s" * 40, "u" * 40, Out)
    assert usage.estimated and usage.input_tokens == 20


def test_structured_drops_usage():
    meta = SimpleNamespace(prompt_token_count=1, candidates_token_count=1)
    m = StubModels([SimpleNamespace(parsed=Out(a=1), text=None, usage_metadata=meta)])
    chat = GeminiChatModel("gm", "k", rpm=1000, client=_client(m))
    assert chat.structured("s", "u", Out) == Out(a=1)


def test_429_honors_retry_after_header(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr("agentic.llm.gemini._sleep", sleeps.append)
    err = HttpErr(429)
    err.response = SimpleNamespace(headers={"Retry-After": "9"})
    m = StubModels([err, SimpleNamespace(parsed=Out(a=5), text=None)])
    chat = GeminiChatModel("gm", "k", rpm=1000, client=_client(m))
    assert chat.structured("s", "u", Out).a == 5
    assert sleeps[0] == 9.0  # the hint, not the 2 s backoff


@pytest.mark.parametrize("header,expected", [("-5", 0.0), ("100000", 60.0), ("12.5", 12.5), ("nan", None),
                                             ("soon", None)])
def test_retry_after_is_clamped_to_0_60(header, expected):
    from agentic.llm.gemini import retry_after_seconds

    err = HttpErr(429)
    err.response = SimpleNamespace(headers={"Retry-After": header})
    assert retry_after_seconds(err) == expected


def test_usage_counts_thought_tokens_as_output():
    meta = SimpleNamespace(prompt_token_count=11, candidates_token_count=5, thoughts_token_count=30)
    m = StubModels([SimpleNamespace(parsed=Out(a=1), text=None, usage_metadata=meta)])
    _, usage = GeminiChatModel("gm", "k", rpm=1000, client=_client(m)).complete("s", "u", Out)
    assert usage == Usage(input_tokens=11, output_tokens=35, estimated=False, thinking_tokens=30)
    meta = SimpleNamespace(prompt_token_count=11, candidates_token_count=5, thoughts_token_count=None)
    m = StubModels([SimpleNamespace(parsed=Out(a=1), text=None, usage_metadata=meta)])
    _, usage = GeminiChatModel("gm", "k", rpm=1000, client=_client(m)).complete("s", "u", Out)
    assert usage.output_tokens == 5


def _thinking(model, reasoning):
    m = StubModels([SimpleNamespace(parsed=Out(a=1), text=None)])
    kwargs = {} if reasoning is None else {"reasoning": reasoning}
    GeminiChatModel(model, "k", rpm=1000, client=_client(m), **kwargs).structured("s", "u", Out)
    return m.calls[0]["config"].thinking_config


def test_gemini_3_reasoning_sets_thinking_level():
    from google.genai import types

    assert _thinking("gemini-3-flash-preview", "low").thinking_level == types.ThinkingLevel.LOW
    assert _thinking("gemini-3-flash-preview", "medium").thinking_level == types.ThinkingLevel.MEDIUM
    assert _thinking("gemini-3-flash-preview", "high").thinking_level == types.ThinkingLevel.HIGH
    assert _thinking("gemini-3-flash-preview", "low").thinking_budget is None


@pytest.mark.parametrize("level,budget", [("low", 1024), ("medium", 4096), ("high", 16384)])
def test_gemini_2_5_reasoning_sets_thinking_budget(level, budget):
    cfg = _thinking("gemini-2.5-flash", level)
    assert cfg.thinking_budget == budget and cfg.thinking_level is None


def test_default_reasoning_sends_no_thinking_config():
    assert _thinking("gemini-3-flash-preview", None) is None
    assert _thinking("gemini-2.5-flash", "default") is None


def test_reasoning_is_exposed_and_unknown_level_is_refused():
    chat = GeminiChatModel("gm", "k", rpm=1000, client=_client(StubModels()), reasoning="low")
    assert chat.reasoning == "low"
    with pytest.raises(ValueError, match="reasoning"):
        GeminiChatModel("gm", "k", rpm=1000, client=_client(StubModels()), reasoning="extreme")


def test_thought_tokens_are_reported_separately_and_still_counted_as_output():
    meta = SimpleNamespace(prompt_token_count=11, candidates_token_count=5, thoughts_token_count=30)
    m = StubModels([SimpleNamespace(parsed=Out(a=1), text=None, usage_metadata=meta)])
    _, usage = GeminiChatModel("gm", "k", rpm=1000, client=_client(m)).complete("s", "u", Out)
    assert usage.thinking_tokens == 30 and usage.output_tokens == 35
    assert Usage(input_tokens=1, output_tokens=2, estimated=True).thinking_tokens == 0

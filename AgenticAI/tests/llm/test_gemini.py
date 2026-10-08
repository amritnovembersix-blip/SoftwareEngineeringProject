from types import SimpleNamespace

import pytest
from pydantic import BaseModel

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

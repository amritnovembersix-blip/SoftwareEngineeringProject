import math

import pytest
from pydantic import BaseModel

from agentic.llm.fake import FakeChatModel, FakeEmbedder


class Out(BaseModel):
    a: int


class Other(BaseModel):
    b: int


def test_fake_embedder_deterministic_and_unit_length():
    e = FakeEmbedder(dim=32)
    v1, v2, v3 = e.embed(["hello", "hello", "world"], "document")
    assert v1 == v2 and v1 != v3
    assert len(v1) == 32
    assert math.sqrt(sum(x * x for x in v1)) == pytest.approx(1.0)


def test_fake_chat_scripted_and_records_calls():
    chat = FakeChatModel({Out: lambda s, u: Out(a=len(u))})
    assert chat.structured("sys", "abc", Out) == Out(a=3)
    assert chat.calls == [(Out, "sys", "abc")]


def test_fake_chat_unscripted_raises():
    chat = FakeChatModel({Out: lambda s, u: Out(a=1)})
    with pytest.raises(KeyError):
        chat.structured("s", "u", Other)

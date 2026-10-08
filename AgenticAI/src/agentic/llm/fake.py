"""Deterministic fakes for ChatModel / Embedder (no network)."""
import math
import random
from collections.abc import Callable
from hashlib import sha256
from typing import Literal

from pydantic import BaseModel

from agentic.llm.base import T


class FakeChatModel:
    def __init__(
        self,
        handlers: dict[type, Callable[[str, str], BaseModel]],
        model_name: str = "fake-chat",
    ):
        self.model_name = model_name
        self._handlers = handlers
        self.calls: list[tuple[type, str, str]] = []

    def structured(self, system: str, user: str, schema: type[T]) -> T:
        self.calls.append((schema, system, user))
        handler = self._handlers[schema]  # KeyError for an unscripted schema
        return handler(system, user)  # type: ignore[return-value]


class FakeEmbedder:
    def __init__(self, dim: int = 768, model_name: str = "fake-embed"):
        self.dim = dim
        self.model_name = model_name

    def _vector(self, text: str) -> list[float]:
        seed = int.from_bytes(sha256(text.encode("utf-8")).digest()[:8], "big")
        rng = random.Random(seed)
        v = [rng.gauss(0.0, 1.0) for _ in range(self.dim)]
        norm = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / norm for x in v]

    def embed(self, texts: list[str], kind: Literal["document", "query"]) -> list[list[float]]:
        return [self._vector(t) for t in texts]

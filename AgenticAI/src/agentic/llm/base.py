"""Targets for chat and embedding models. Adapters live in sibling modules."""
from typing import Literal, Protocol, TypeVar, runtime_checkable

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


@runtime_checkable
class ChatModel(Protocol):
    model_name: str

    def structured(self, system: str, user: str, schema: type[T]) -> T: ...


@runtime_checkable
class Embedder(Protocol):
    model_name: str

    def embed(self, texts: list[str], kind: Literal["document", "query"]) -> list[list[float]]: ...

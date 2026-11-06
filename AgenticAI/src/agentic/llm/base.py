"""Targets for chat and embedding models. Adapters live in sibling modules."""
from typing import Literal, Protocol, TypeVar, runtime_checkable

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


REASONING_LEVELS = ("default", "low", "medium", "high")  # "default" = send no reasoning setting


def check_reasoning(level: str, where: str) -> str:
    if level not in REASONING_LEVELS:
        raise ValueError(f"{where}: reasoning must be one of {', '.join(REASONING_LEVELS)}, got {level!r}")
    return level


def chat_key(model: str | None, level: str | None) -> str:
    """Key of a chat in the per-reviewer chat map: the model id, plus "@<level>" when the level is not
    default. A blank model (the default model) gives "" or "@<level>"."""
    return f"{model or ''}@{level}" if level and level != "default" else (model or "")


def model_label(chat: object) -> str | None:
    """The model id for provenance: "<model>@<level>" when the chat reasons at a non-default level."""
    name = getattr(chat, "model_name", None)
    level = getattr(chat, "reasoning", "default")
    return name if not name or level == "default" else f"{name}@{level}"


class Usage(BaseModel):
    """Tokens one chat call used. `estimated` is True when the provider reported none (length / 4).
    `thinking_tokens` is the part of `output_tokens` the model spent thinking (0 when not reported);
    `calls` is how many chat calls the figures cover (0 when not recorded)."""

    input_tokens: int
    output_tokens: int
    estimated: bool
    thinking_tokens: int = 0
    calls: int = 0


class DailyQuotaExceeded(Exception):
    """The per-day request budget is used up; waiting a minute will not help."""

    def __init__(self, message: str = "daily request budget reached"):
        super().__init__(message)


@runtime_checkable
class ChatModel(Protocol):
    model_name: str

    def complete(self, system: str, user: str, schema: type[T]) -> tuple[T, Usage]: ...

    def structured(self, system: str, user: str, schema: type[T]) -> T: ...


@runtime_checkable
class Embedder(Protocol):
    model_name: str

    def embed(self, texts: list[str], kind: Literal["document", "query"]) -> list[list[float]]: ...

"""Target for the decision model (Jev)."""
from typing import Protocol, runtime_checkable

from pydantic import BaseModel


class DecisionError(Exception):
    """The decision model call failed. Never carries essay text."""


class NoulQuestion(BaseModel):
    instructions: str | dict
    criteria: dict[str, str] | None = None


class NoulResult(BaseModel):
    probabilities: dict[str, float]
    model: str
    input_tokens: int


@runtime_checkable
class DecisionModel(Protocol):
    def noul(self, state: dict, questions: dict[str, NoulQuestion]) -> NoulResult: ...

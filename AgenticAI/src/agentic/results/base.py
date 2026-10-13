"""Target for persisting review results."""
from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel

from agentic.models import ReviewResult


class SaveOutcome(BaseModel):
    status: Literal["written", "duplicate"]
    review_run_id: str


@runtime_checkable
class ResultsWriter(Protocol):
    def save(self, result: ReviewResult, trigger: str = "manual") -> SaveOutcome:
        """`trigger` is the review_run trigger_type (manual, on_grade, ...)."""
        ...

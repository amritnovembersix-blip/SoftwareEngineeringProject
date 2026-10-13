"""Scripted DecisionModel for tests and offline demos."""
from collections.abc import Callable

from agentic.decisions.base import NoulQuestion, NoulResult


class FakeDecisionModel:
    def __init__(self, probabilities: dict[str, float] | Callable[[str], float], model: str = "fake-jev"):
        self._probabilities = probabilities
        self.model = model
        self.calls: list[tuple[dict, dict[str, NoulQuestion]]] = []

    def noul(self, state: dict, questions: dict[str, NoulQuestion]) -> NoulResult:
        self.calls.append((state, questions))
        if callable(self._probabilities):
            probs = {k: self._probabilities(k) for k in questions}
        else:
            probs = {k: self._probabilities[k] for k in questions if k in self._probabilities}
        return NoulResult(probabilities=probs, model=self.model, input_tokens=0)

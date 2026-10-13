"""Decorator pattern (GoF): wrap any standards source and keep only part of it.

Each filter has the same interface as the source it wraps (`at_grade`, `search`), so the review
strategies use it without knowing it is there, and filters stack (TargetFilter over StrandFilter).
"""
from agentic.dataset import normalize_strands
from agentic.models import StandardRef


def strand_of(code: str) -> str:
    return code.split(".")[1]


class _ScopeFilter:
    def __init__(self, inner):
        self._inner = inner

    def _keeps(self, code: str) -> bool:
        raise NotImplementedError

    def _kept(self, rows: list[StandardRef]) -> list[StandardRef]:
        return [s for s in rows if self._keeps(s.code)]

    def at_grade(self, grade: int) -> list[StandardRef]:
        return self._kept(self._inner.at_grade(grade))

    def search(self, query_embedding: list[float], grade: int, k: int) -> list[StandardRef]:
        # Widen the inner search by the number of excluded standards at this grade, so excluded
        # rows ranking first can't crowd out the k we want.
        excluded = len(self._inner.at_grade(grade)) - len(self.at_grade(grade))
        return self._kept(self._inner.search(query_embedding, grade, k + excluded))[:k]


class StrandFilter(_ScopeFilter):
    """Keeps only the review strands; writing (W) is always included. `9-10.RL.1.A` -> `RL`."""

    def __init__(self, inner, strands):
        super().__init__(inner)
        self.strands = normalize_strands(strands)

    def _keeps(self, code: str) -> bool:
        return strand_of(code) in self.strands


class TargetFilter(_ScopeFilter):
    """Keeps only an assignment's targeted codes (the review's outer boundary).
    Target codes missing from the source are ignored."""

    def __init__(self, inner, codes):
        super().__init__(inner)
        self.codes = list(dict.fromkeys(codes))

    @property
    def strands(self) -> list[str]:
        return getattr(self._inner, "strands", ["W"])

    def _keeps(self, code: str) -> bool:
        return code in self.codes

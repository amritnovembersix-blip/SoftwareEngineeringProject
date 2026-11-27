"""Standards for the v2 band reviewers: a Decorator that scopes a source to a grade band and
strands, and progressions (the same skill traced across grades)."""
import re
from dataclasses import dataclass

from agentic.models import StandardRef
from agentic.review.strategies import StandardsSource
from agentic.standards.strand_filter import strand_of


class BandScope:
    """Decorator (GoF): the standards of grades `lo`..`hi` in the given strands, from any source."""

    def __init__(self, source: StandardsSource, lo: int, hi: int, strands: list[str]):
        self._source = source
        self._lo = lo
        self._hi = hi
        self._strands = list(strands)

    def standards(self) -> list[StandardRef]:
        return [s for s in self._source.in_range(self._lo, self._hi)
                if strand_of(s.code) in self._strands]


@dataclass(frozen=True)
class Progression:
    group: str  # strand.bigIdea.concept, e.g. W.2.A
    steps: list[tuple[int, list[StandardRef]]]  # (lowest grade of the step, its standards)


def group_of(code: str) -> str:
    """The code minus the grade prefix, first three segments: `9-10.W.2.A` -> `W.2.A`."""
    return ".".join(code.split(".")[1:4])


def conditions_for(conditions: dict[str, list[str]], code: str) -> tuple[str, list[str]] | None:
    """A code's conditions and the key they came from: the band key for the code's own grade (`6+:` for grade 6
    and up, else `3-5:`) wins over the plain key (R5, R7). None when neither exists."""
    return band_conditions(conditions, int(code.split(".")[0].split("-")[0]), group_of(code))


def band_conditions(conditions: dict[str, list[str]], grade: int, group: str) -> tuple[str, list[str]] | None:
    """`conditions_for` by grade and group, for callers that hold a group rather than a code."""
    band = "6+" if grade >= 6 else "3-5"
    for key in (f"{band}:{group}", group):
        if conditions.get(key):
            return key, conditions[key]
    return None


def _normalized(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def _step_text(refs: list[StandardRef]) -> str:
    return _normalized(" ".join(r.sub_expectation or r.description for r in refs))


def progressions(refs: list[StandardRef]) -> list[Progression]:
    by_group: dict[str, dict[int, list[StandardRef]]] = {}
    for r in sorted(refs, key=lambda r: r.code):
        by_group.setdefault(group_of(r.code), {}).setdefault(r.grade_min, []).append(r)
    out: list[Progression] = []
    for group in sorted(by_group):
        steps: list[tuple[int, list[StandardRef]]] = []
        last_text = None
        for grade in sorted(by_group[group]):
            step_refs = by_group[group][grade]
            text = _step_text(step_refs)
            if steps and text == last_text:
                steps[-1][1].extend(step_refs)  # unchanged from the previous grade: one step
            else:
                steps.append((grade, list(step_refs)))
            last_text = text
        out.append(Progression(group=group, steps=steps))
    return out


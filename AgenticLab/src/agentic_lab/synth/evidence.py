"""What a submission can evidence, how a student answers multiple choice, and multiple-choice fidelity
(spec 5.1-5.3)."""
import random
from collections.abc import Mapping

from agentic.coursedata import AssignmentFile, ItemAnswer
from agentic_lab.answer_key import FidelityRow
from agentic_lab.synth.families import grade_key


def concept_of(code: str) -> str:
    """A code's first 4 segments (`3.R.1.B` for `3.R.1.B.b`); the same rule as `agentic_lab.eval.concept_of`."""
    return ".".join(code.split(".")[:4])


def _strand(code: str) -> str:
    return code.split(".")[1]


def evidenced(assignment: AssignmentFile, module_strand: str, levels: dict[str, int]) -> dict[str, int]:
    """The subset of levels (code -> level) this submission can evidence."""
    if assignment.type in ("essay", "paragraph"):
        return {c: lv for c, lv in levels.items()
                if not (_strand(c) in ("RL", "RI") and 6 <= grade_key(c) <= 10 and _strand(c) != module_strand)}
    item_codes = {i.standard for i in assignment.items}
    if assignment.type == "short_answer":
        return {c: lv for c, lv in levels.items()
                if c in item_codes or _strand(c) in ("W", "L")
                or (_strand(c) == "R" and 3 <= grade_key(c) <= 5)}
    item_codes |= {ch.standard for i in assignment.items for ch in i.choices if ch.standard}
    return {c: lv for c, lv in levels.items() if c in item_codes}


def choose_answers(assignment: AssignmentFile, levels: dict[str, int], rng: random.Random) -> list[ItemAnswer]:
    """One decision per item, from its distractors whose standard has level >= 1: the highest level >= 2 wins
    (ties to the lowest key); else, with any at level 1, one roll at 0.5 picks the lowest such key; else the
    correct answer. `rng` is only consumed by that one roll."""
    answers = []
    for item in assignment.items:
        weak = [(levels.get(ch.standard, 0), ch.key) for ch in item.choices if not ch.correct and ch.standard]
        weak = [(lv, k) for lv, k in weak if lv >= 1]
        top = max((lv for lv, _ in weak), default=0)
        if top >= 2:
            key = min(k for lv, k in weak if lv == top)
        elif top == 1 and rng.random() < 0.5:
            key = min(k for _, k in weak)
        else:
            key = next(ch.key for ch in item.choices if ch.correct)
        answers.append(ItemAnswer(item_id=item.id, choice=key))
    return answers


def concept_picks(assignment: AssignmentFile, levels: Mapping[str, int],
                  answers: list[ItemAnswer]) -> dict[str, tuple[int, int]]:
    """concept -> (planted, picks) for each concept a quiz tests: the highest level among its tested codes
    (item and distractor standards) and the number of distractor picks on it."""
    planted: dict[str, int] = {}
    picks: dict[str, int] = {}
    chosen = {a.item_id: a.choice for a in answers}
    for item in assignment.items:
        for code in [item.standard] + [ch.standard for ch in item.choices if ch.standard]:
            c = concept_of(code)
            planted[c] = max(planted.get(c, 0), levels.get(code, 0))
            picks.setdefault(c, 0)
        pick = next((ch for ch in item.choices if ch.key == chosen.get(item.id) and not ch.correct), None)
        if pick is not None and pick.standard:
            picks[concept_of(pick.standard)] += 1
    return {c: (planted[c], picks[c]) for c in planted}


def mc_fidelity(assignment: AssignmentFile, levels: Mapping[str, int], answers: list[ItemAnswer],
                submission_id: str) -> FidelityRow | None:
    """Rule-based multiple-choice fidelity (spec 5.3): a planted (>= 2) concept with fewer than 2 picks is
    missing, an unplanted concept with any pick is extra; level 1 is stochastic and never fails."""
    stats = concept_picks(assignment, levels, answers)
    missing = [f"concept:{c}" for c, (lv, n) in stats.items() if lv >= 2 and n < 2]
    extra = [f"concept:{c}" for c, (lv, n) in stats.items() if lv == 0 and n > 0]
    if not missing and not extra:
        return None
    return FidelityRow(submission_id=submission_id, missing=missing, extra=extra)

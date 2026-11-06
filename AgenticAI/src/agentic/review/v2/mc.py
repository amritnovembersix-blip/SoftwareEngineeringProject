"""Multiple choice by distractor lookup (no LLM). SASS never scores: a quiz is evidence. A chosen wrong
answer (distractor) carries the standard and misconception it reveals, from the teacher's answer key."""
from agentic.lms.base import Assignment
from agentic.models import Finding, Observation, Verdict
from agentic.review.v2.scope import group_of
from agentic.standards.codes import concept_of

REVIEWER = "mc-lookup"
MAX_REASON_WORDS = 12
MAX_SUMMARY_WORDS = 40


def _support_level(picks: int, items: int) -> str:
    if picks * 3 >= items * 2:
        return "beginning"
    if picks * 3 >= items:
        return "developing"
    return "approaching"


def _confidence(picks: int, items: int) -> float:
    """Deterministic rule: more items on a standard means more evidence.
    No picks: min(1, 0.5 + 0.1 * items). With picks: min(1, picks / items + 0.1 * (items - 1))."""
    if picks == 0:
        return min(1.0, 0.5 + 0.1 * items)
    return min(1.0, picks / items + 0.1 * (items - 1))


def _summary(reasons: list[str]) -> str:
    """The distinct misconceptions, as many as fit in 40 words (always at least the first)."""
    out: list[str] = []
    for r in dict.fromkeys(reasons):
        if out and sum(len(x.split()) for x in out + [r]) > MAX_SUMMARY_WORDS:
            break
        out.append(r)
    return "; ".join(out)


def mc_lookup(assignment: Assignment, answers: list[dict],
              min_picks: int) -> tuple[list[Verdict], list[Observation], list[Finding]]:
    """Verdicts, item-level Observations and Findings from the chosen answers, counted by concept.

    Per concept (`concept_of`): `picks` = distractor picks on it; `items` = every item in the assignment,
    answered or not, whose own standard or any distractor is in it (only an internal ratio, never a score).
    growth_area at picks >= min_picks: every picked standard gets a growth_area verdict with the concept's
    support level, other standards of the concept get not_evident, and ONE finding goes on the most-picked
    standard (ties: lowest code) covering every pick in the concept. meets if no picks and every item whose
    own standard is in the concept was answered correctly: every asked standard gets meets. Otherwise
    not_evident. An answer with no matching item or choice, or a wrong pick whose distractor lacks a
    standard or a misconception, is skipped. The last answer per item wins."""
    items_of: dict[str, set[int]] = {}
    own_items: dict[str, set[int]] = {}
    standards: dict[str, set[str]] = {}
    for index, item in enumerate(assignment.items, 1):
        own = item["standard"]
        carried = {own, *(c["standard"] for c in item.get("choices", []) if c.get("standard"))}
        for std in carried:
            items_of.setdefault(concept_of(std), set()).add(index)
            standards.setdefault(concept_of(std), set()).add(std)
        own_items.setdefault(concept_of(own), set()).add(index)
    by_id = {it.get("id"): (i, it) for i, it in enumerate(assignment.items, 1)}
    last = {a.get("item_id"): a for a in answers}
    obs: list[Observation] = []
    correct_items: set[int] = set()
    for item_id, a in last.items():
        found = by_id.get(item_id)
        if found is None:
            continue
        index, item = found
        choice = next((c for c in item.get("choices", []) if c.get("key") == a.get("choice")), None)
        if choice is None:
            continue
        if choice.get("correct"):
            correct_items.add(index)
            continue
        words = str(choice.get("misconception") or "").split()
        if not words or not choice.get("standard"):
            continue
        obs.append(Observation(standard_code=choice["standard"], paragraph=index,
                               reason=" ".join(words[:MAX_REASON_WORDS]), severity=None, level="item"))
    verdicts: list[Verdict] = []
    findings: list[Finding] = []
    for concept in sorted(items_of):
        mine = [o for o in obs if concept_of(o.standard_code) == concept]
        picks, items = len(mine), len(items_of[concept])
        confidence = _confidence(picks, items)
        by_std = {std: sorted(o.paragraph for o in mine if o.standard_code == std)
                  for std in sorted(standards[concept])}
        if picks >= min_picks and picks > 0:
            level = _support_level(picks, items)
            for std, segments in by_std.items():
                if segments:
                    verdicts.append(Verdict(standard_code=std, verdict="growth_area", support_level=level,
                                            segments=segments, confidence=confidence, reviewer=REVIEWER))
                else:
                    verdicts.append(Verdict(standard_code=std, verdict="not_evident", segments=[],
                                            confidence=confidence, reviewer=REVIEWER))
            top = min(by_std, key=lambda s: (-len(by_std[s]), s))
            reasons = [o.reason for o in sorted(mine, key=lambda o: o.paragraph)]
            findings.append(Finding(standard_code=top, concept=group_of(top), description="",
                                    paragraphs=sorted(o.paragraph for o in mine), reasons=reasons,
                                    observation_count=picks, summary=_summary(reasons), severity=level,
                                    support_level=level, confidence=confidence))
            continue
        met = picks == 0 and bool(own_items.get(concept)) and own_items[concept] <= correct_items
        asked = {it["standard"] for i, it in enumerate(assignment.items, 1) if concept_of(it["standard"]) == concept}
        for std, segments in by_std.items():
            verdicts.append(Verdict(standard_code=std,
                                    verdict="meets" if met and std in asked else "not_evident",
                                    segments=segments, confidence=confidence, reviewer=REVIEWER))
    return verdicts, obs, findings

"""Review supervisor (docs/superpowers/specs/2026-11-17-review-supervisor-design.md): three checking steps for the
supervised profile, each one structured call through the ChatModel Target, with the rules enforced here in code.

- Planner: per assignment, which grade 3-5 writing and reading reviewers run (never sees student work).
- Critic: keeps or drops each growth area against its cited segments.
- Arbiter: one problem, one standard, within clusters of growth areas that cite the same segments.

Reasons are cut to 12 words and never copy student text; change records hold codes, segment numbers and reasons.
"""
from dataclasses import dataclass
from string import Template
from typing import ClassVar, Literal

from pydantic import BaseModel

from agentic.llm.base import ChatModel, DailyQuotaExceeded, Usage
from agentic.lms.base import Assignment
from agentic.models import Observation, StandardRef, Verdict
from agentic.tuning import ReviewerSpec

MAX_REASON_WORDS = 12
ATTEMPTS = 2  # a failed call or a reply that breaks the rules is tried once more, then the step changes nothing

_SYSTEM = ("You help review student work against Missouri Learning Standards. Follow the reply fields and rules "
           "exactly, and never quote or copy the student's words.")

_STRAND_TEXT = {"L": "grammar, usage, capitalization, punctuation and spelling",
                "W": "writing: arguments, explanations and narratives, their organization and development",
                "R": "reading: understanding and analysing texts",
                "RL": "reading literature", "RI": "reading informational text"}


def short_reason(text: str) -> str:
    return " ".join(text.split()[:MAX_REASON_WORDS])


def _strand_of(code: str) -> str:
    parts = code.split(".")
    return parts[1] if len(parts) > 1 else ""


# ---- planner ----

class PlanChoice(BaseModel):
    reviewer: str
    action: Literal["run", "skip"]
    reason: str


class PlannerOut(BaseModel):
    choices: list[PlanChoice]


@dataclass
class PlanDecision:
    run: list[str]
    skipped: dict[str, str]  # reviewer id -> reason
    usage: Usage | None  # None on a cache hit
    cached: bool
    invalid: bool = False  # no usable reply: everything runs, and the decision is not cached


class Planner:
    """One call per (tuning hash, assignment) per process. Only grade 3-5 reviewers whose strands are all W or R,
    and that cover none of the assignment's target strands, can be skipped; everything else runs. An RL or RI
    target counts as covering R, so the grade 3-5 reading reviewers run whenever the assignment is about reading."""

    _cache: ClassVar[dict[tuple[str, str], tuple[list[str], dict[str, str]]]] = {}

    def __init__(self, chat: ChatModel, template: str, tuning_hash: str):
        self._chat = chat
        self._template = Template(template)
        self._hash = tuning_hash

    @classmethod
    def clear_cache(cls) -> None:
        cls._cache.clear()

    @staticmethod
    def skippable(spec: ReviewerSpec, hi: int, assignment: Assignment) -> bool:
        targets = {_strand_of(code) for code in _targets(assignment)}
        if targets & {"RL", "RI"}:  # a reading target keeps the grade 3-5 reading roots (Justin, 2026-11-17)
            targets.add("R")
        return hi < 6 and all(x in ("W", "R") for x in spec.strands) and not targets & set(spec.strands)

    def plan(self, assignment: Assignment, candidates: list[tuple[ReviewerSpec, int, int]]) -> PlanDecision:
        ids = [spec.id for spec, _, _ in candidates]
        key = (self._hash, assignment.id)
        if key in self._cache:
            run, skipped = self._cache[key]
            return PlanDecision(run=list(run), skipped=dict(skipped), usage=None, cached=True)
        can_skip = {spec.id for spec, _, hi in candidates if self.skippable(spec, hi, assignment)}
        skipped: dict[str, str] = {}
        usage = _zero()
        valid = not can_skip  # nothing to decide counts as a decision
        if can_skip:
            user = self._template.substitute(assignment=_assignment_block(assignment),
                                             reviewers=_reviewer_block(candidates))
            for _ in range(ATTEMPTS):
                try:
                    out, used = self._chat.complete(_SYSTEM, user, PlannerOut)
                except DailyQuotaExceeded:
                    raise
                except Exception:  # noqa: BLE001 - a failed call: everything runs
                    continue
                usage = _add(usage, used)
                if all(c.reason.strip() for c in out.choices):
                    skipped = {c.reviewer: short_reason(c.reason) for c in out.choices
                               if c.action == "skip" and c.reviewer in can_skip}
                    valid = True
                    break
        run = [i for i in ids if i not in skipped]
        if valid:  # a failed plan is not cached: the next submission asks again
            self._cache[key] = (run, skipped)
        return PlanDecision(run=run, skipped=skipped, usage=usage, cached=False, invalid=not valid)


def _targets(a: Assignment) -> list[str]:
    return list(dict.fromkeys([*a.targets, *a.notes]))


def _assignment_block(a: Assignment) -> str:
    focus = "".join(f"\n- target {code}" for code in _targets(a))
    return (f"Title: {a.name}\nType: {a.submission_type.replace('_', ' ')}\nReading strand: {a.reading_strand or 'none'}"
            f"\nInstructions: {a.description or '(none)'}{focus}")


def _reviewer_block(candidates: list[tuple[ReviewerSpec, int, int]]) -> str:
    return "\n".join(f"- {spec.id} | grades {lo}-{hi} | {', '.join(spec.strands)} | "
                     + "; ".join(_STRAND_TEXT.get(x, x) for x in spec.strands)
                     for spec, lo, hi in candidates)


def _zero() -> Usage:
    return Usage(input_tokens=0, output_tokens=0, estimated=False)


def _add(a: Usage, b: Usage) -> Usage:
    """Sums two calls' usage; `calls` counts the chat calls made."""
    return Usage(input_tokens=a.input_tokens + b.input_tokens, output_tokens=a.output_tokens + b.output_tokens,
                 estimated=a.estimated or b.estimated, thinking_tokens=a.thinking_tokens + b.thinking_tokens,
                 calls=a.calls + 1)


# ---- shared by the critic and the arbiter ----

@dataclass
class StepResult:
    verdicts: list[Verdict]
    observations: list[Observation]
    changes: list[dict]  # {"standard", "step", "action", "reason", "segments"[, "into"]}: codes and reasons only
    usage: Usage | None  # None when the step made no call
    capped: bool = False
    invalid: bool = False


def numbered(segments: list[str]) -> str:
    return "\n".join(f"S{i} {seg}" for i, seg in enumerate(segments, 1))


def _growth_line(v: Verdict, refs: dict[str, StandardRef], reasons: dict[str, list[str]]) -> str:
    ref = refs.get(v.standard_code)
    text = (f"{ref.description} {ref.sub_expectation}" if ref and ref.sub_expectation
            else ref.description if ref else "")
    cited = ", ".join(f"S{n}" for n in v.segments)
    return (f"- {v.standard_code} | {text} | {v.support_level} | "
            f"{'; '.join(reasons.get(v.standard_code, [])) or '(none given)'} | {cited}")


def _reasons(observations: list[Observation]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for o in observations:
        out.setdefault(o.standard_code, []).append(o.reason)
    return out


def _demote(v: Verdict, verdict: str) -> Verdict:
    """A growth area the step removed: no level, evidence or confidence; a notable strength stays as it was."""
    return v.model_copy(update={"verdict": verdict, "support_level": None, "working_grade": None,
                                "segments": [], "confidence": None})


def _apply(verdicts: list[Verdict], observations: list[Observation], changed: dict[str, str]) -> tuple[list, list]:
    """`changed` maps a code to its new verdict; its observations go."""
    vs = [_demote(v, changed[v.standard_code]) if v.standard_code in changed else v for v in verdicts]
    return vs, [o for o in observations if o.standard_code not in changed]


# ---- critic ----

class CriticCall(BaseModel):
    standard: str
    action: Literal["keep", "drop"]
    outcome: Literal["met", "not_shown"] | None = None
    reason: str


class CriticOut(BaseModel):
    calls: list[CriticCall]


class Critic:
    """One call per submission over its growth areas that cite segments (up to `max_items`). A drop turns the
    growth area into meets (`met`) or not evident (`not_shown`). A reply that breaks the rules twice changes nothing."""

    def __init__(self, chat: ChatModel, template: str, max_items: int):
        self.chat = self._chat = chat
        self._template = Template(template)
        self._max = max_items

    def run(self, verdicts: list[Verdict], observations: list[Observation], segments: list[str],
            refs: dict[str, StandardRef]) -> StepResult:
        growth = [v for v in verdicts if v.verdict == "growth_area" and v.segments]
        growth.sort(key=lambda v: not _grade_6_plus(v.standard_code))  # stable: 6+ first, then as given
        sent, capped = growth[:self._max], len(growth) > self._max
        if not sent:
            return StepResult(verdicts, observations, [], None, capped)
        reasons = _reasons(observations)
        user = self._template.substitute(segments=numbered(segments),
                                         growth_areas="\n".join(_growth_line(v, refs, reasons) for v in sent))
        codes = {v.standard_code: v for v in sent}
        usage = _zero()
        for _ in range(ATTEMPTS):
            try:
                out, used = self._chat.complete(_SYSTEM, user, CriticOut)
            except DailyQuotaExceeded:
                raise  # waiting will not help; the run pauses
            except Exception:  # noqa: BLE001 - a failed call changes nothing (class only, never the message)
                continue
            usage = _add(usage, used)
            # a malformed item (a drop without an outcome, or no reason) is just that item kept unchecked;
            # an outcome on a keep is ignored
            drops = {c.standard: c for c in out.calls if c.standard in codes and c.action == "drop"
                     and c.outcome is not None and c.reason.strip()}
            changed = {code: ("meets" if c.outcome == "met" else "not_evident") for code, c in drops.items()}
            vs, obs = _apply(verdicts, observations, changed)
            changes = [{"standard": code, "step": "critic", "action": f"dropped:{c.outcome}",
                        "reason": short_reason(c.reason), "segments": list(codes[code].segments)}
                       for code, c in drops.items()]
            return StepResult(vs, obs, changes, usage, capped)
        return StepResult(verdicts, observations, [], usage, capped, invalid=True)


# ---- clusters and arbiter ----

def concept_line(code: str) -> str:
    """The standard's concept without its grade: "9-10.W.3.A.b" -> "W.3.A"."""
    return ".".join(code.split(".")[1:4])


def grade_of(code: str) -> str:
    return code.split(".")[0]


def _grade_6_plus(code: str) -> bool:
    g = grade_of(code)
    return g.split("-")[0].isdigit() and int(g.split("-")[0]) >= 6


def _conflict(a: set[str], b: set[str]) -> bool:
    """True when joining would put two codes on one concept line in one cluster: at two grades that is the
    progression chain (root cause's job); at one grade they are sibling skills the key counts separately."""
    lines = [concept_line(code) for code in a | b]
    return len(lines) != len(set(lines))


def clusters(verdicts: list[Verdict]) -> list[list[str]]:
    """Growth areas that cite overlapping segments, joined greedily in code order, never putting two codes on one
    concept line together. Only clusters of two or more, each sorted, in order of their first code."""
    growth = sorted((v for v in verdicts if v.verdict == "growth_area" and v.segments), key=lambda v: v.standard_code)
    comp = {v.standard_code: {v.standard_code} for v in growth}
    segs = {v.standard_code: set(v.segments) for v in growth}
    for i, a in enumerate(growth):
        for b in growth[i + 1:]:
            ca, cb = comp[a.standard_code], comp[b.standard_code]
            if ca is cb or not segs[a.standard_code] & segs[b.standard_code] or _conflict(ca, cb):
                continue
            merged = ca | cb
            for code in merged:
                comp[code] = merged
    seen, out = set(), []
    for c in comp.values():
        if id(c) not in seen and len(c) > 1:
            seen.add(id(c))
            out.append(sorted(c))
    return sorted(out)


class Problem(BaseModel):
    standard: str
    absorbs: list[str] = []
    reason: str


class ClusterCall(BaseModel):
    cluster: int
    problems: list[Problem]


class ArbiterOut(BaseModel):
    clusters: list[ClusterCall]


class Arbiter:
    """One call per submission over its clusters (up to `max_clusters`). Each cluster member ends up a problem's
    standard or absorbed into one; an absorbed growth area becomes not evident. A reply that breaks the rules
    twice changes nothing."""

    def __init__(self, chat: ChatModel, template: str, max_clusters: int):
        self.chat = self._chat = chat
        self._template = Template(template)
        self._max = max_clusters

    def run(self, verdicts: list[Verdict], observations: list[Observation], segments: list[str],
            refs: dict[str, StandardRef]) -> StepResult:
        found = clusters(verdicts)
        sent, capped = found[:self._max], len(found) > self._max
        if not sent:
            return StepResult(verdicts, observations, [], None, capped)
        by_code = {v.standard_code: v for v in verdicts}
        reasons = _reasons(observations)
        usage = _zero()
        absorbed: dict[str, tuple[str, str]] = {}
        pending = list(sent)  # clusters still without a valid answer; a retry asks about these only
        for _ in range(ATTEMPTS):
            block = "\n".join(f"Cluster {n}:\n" + "\n".join(_growth_line(by_code[c], refs, reasons) for c in members)
                              for n, members in enumerate(pending, 1))
            user = self._template.substitute(segments=numbered(segments), clusters=block)
            try:
                out, used = self._chat.complete(_SYSTEM, user, ArbiterOut)
            except DailyQuotaExceeded:
                raise  # waiting will not help; the run pauses
            except Exception:  # noqa: BLE001 - a failed call changes nothing (class only, never the message)
                continue
            usage = _add(usage, used)
            replies = {c.cluster: c for c in out.clusters}
            still = []
            for n, members in enumerate(pending, 1):
                got = _cluster_absorptions(replies.get(n), members)
                if got is None:
                    still.append(members)
                else:
                    absorbed.update(got)
            pending = still
            if not pending:
                break
        vs, obs = _apply(verdicts, observations, {code: "not_evident" for code in absorbed})
        changes = [{"standard": code, "step": "arbiter", "action": "absorbed", "into": into,
                    "reason": reason, "segments": list(by_code[code].segments)}
                   for code, (into, reason) in absorbed.items()]
        return StepResult(vs, obs, changes, usage if usage.calls else None, capped, invalid=bool(pending))


def _cluster_absorptions(call: "ClusterCall | None", members: list[str]) -> dict[str, tuple[str, str]] | None:
    """code -> (the standard it was absorbed into, reason) for one cluster, or None when its answer breaks a rule:
    every member exactly once, only this cluster's codes, a reason on every problem."""
    if call is None:
        return None
    named = [p.standard for p in call.problems] + [a for p in call.problems for a in p.absorbs]
    if sorted(named) != sorted(members) or any(not p.reason.strip() for p in call.problems):
        return None
    return {a: (p.standard, short_reason(p.reason)) for p in call.problems for a in p.absorbs}

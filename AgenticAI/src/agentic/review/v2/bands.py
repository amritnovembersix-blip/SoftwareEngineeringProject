"""BandReviewer: one LLM reviewer over one grade band, judged per standard (grades 3-5), or for grade 6
up by progression (every step in one call) or by a downward search (one step per group per round).

Guards record a Dropped entry and continue. Dropped reasons name the rule and a known code, group or
segment number only; they never copy model text (it may quote the student's work).
"""
from dataclasses import dataclass
from string import Template
from typing import Literal

from pydantic import BaseModel, Field

from agentic.llm.base import ChatModel, DailyQuotaExceeded, T, Usage, model_label
from agentic.lms.base import Assignment
from agentic.models import Dropped, Observation, StandardRef, Verdict, word_count
from agentic.review.strategies import _reason_problem
from agentic.review.v2.schemas import (
    PerStandardOut, ProgressionJudgment, ProgressionOut, SegmentEvidence, StepJudgment, StepOut,
)
from agentic.review.v2.scope import BandScope, Progression, group_of, progressions
from agentic.tuning import ReviewerSpec

VERDICTS = ("meets", "growth_area", "not_evident")
SUPPORT_LEVELS = ("approaching", "developing", "beginning")
MAX_NOTE_WORDS = 40
ATTEMPTS = 3  # a failed call or a non-matching reply is retried twice, then re-raised

_SYSTEM = ("You review student work against Missouri Learning Standards. "
           "Follow the instructions in the message and reply only in the requested structure.")


class BandResult(BaseModel):
    reviewer_id: str
    verdicts: list[Verdict] = Field(default_factory=list)
    observations: list[Observation] = Field(default_factory=list)
    dropped: list[Dropped] = Field(default_factory=list)
    usage: Usage
    calls: int = 0
    rounds: int = 0  # search mode: rounds asked (a follow-up belongs to its round); 0 for other modes
    prompt_version: str
    model: str | None = None  # the chat's model id, when it made a call
    standards_count: int = 0
    raw: dict = Field(default_factory=dict)  # the parsed reply(s), for the trace only


@dataclass
class _Run:
    """What one review() collects across its calls."""
    stage: str
    segments: int
    level: str
    strengths: bool
    input_tokens: int = 0
    output_tokens: int = 0
    estimated: bool = False
    thinking_tokens: int = 0
    calls: int = 0
    rounds: int = 0

    def __post_init__(self):
        self.dropped: list[Dropped] = []
        self.verdicts: list[Verdict] = []
        self.observations: list[Observation] = []

    def drop(self, reason: str) -> None:
        self.dropped.append(Dropped(stage=self.stage, reason=reason))

    def usage(self) -> Usage:
        return Usage(input_tokens=self.input_tokens, output_tokens=self.output_tokens,
                     estimated=self.estimated, thinking_tokens=self.thinking_tokens, calls=self.calls)


@dataclass(frozen=True)
class _Growth:
    """A search step judged growth_area: its index in the progression, level and valid evidence."""
    step: int
    level: str
    evidence: list[SegmentEvidence]


def _standard_text(s: StandardRef) -> str:
    return f"{s.description} {s.sub_expectation}" if s.sub_expectation else s.description


def _assignment_text(a: Assignment | None) -> str:
    if a is None or not a.description:
        return "(none)"
    notes = "".join(f"\n- {code}: {note}" for code, note in a.notes.items())
    return f"Title: {a.name}\n{a.description}" + (f"\nFocus by standard:{notes}" if notes else "")


class BandReviewer:
    def __init__(self, spec: ReviewerSpec, lo: int, hi: int, scope: BandScope, chat: ChatModel,
                 template: str, conditions: dict[str, list[str]] | None = None):
        self.spec = spec
        self.lo = lo
        self.hi = hi
        self._scope = scope
        self._chat = chat
        self._template = Template(template)
        self._conditions = conditions or {}

    def review(self, segments: list[str], segment_level: Literal["paragraph", "sentence", "answer"],
               assignment: Assignment | None, notable_strengths: bool) -> BandResult:
        standards = self._scope.standards()
        run = _Run(stage=f"band:{self.spec.id}", segments=len(segments), level=segment_level,
                   strengths=notable_strengths)
        raw: dict = {}
        if standards:
            body = {"per_standard": self._per_standard, "progression": self._progression,
                    "search": self._search}[self.spec.mode]
            raw = body(standards, segments, assignment, run)
        return BandResult(reviewer_id=self.spec.id, verdicts=run.verdicts, observations=run.observations,
                          dropped=run.dropped, usage=run.usage(), calls=run.calls, rounds=run.rounds,
                          prompt_version=self.spec.prompt, standards_count=len(standards), raw=raw,
                          model=model_label(self._chat) if run.calls else None)

    # ---- calls and prompts ----

    def _call(self, user: str, schema: type[T], run: _Run) -> T:
        for attempt in range(ATTEMPTS):
            run.calls += 1
            try:
                out, usage = self._chat.complete(_SYSTEM, user, schema)
            except DailyQuotaExceeded:
                raise  # waiting will not help; the run pauses
            except Exception:
                if attempt == ATTEMPTS - 1:
                    raise
                continue
            run.input_tokens += usage.input_tokens
            run.output_tokens += usage.output_tokens
            run.thinking_tokens += usage.thinking_tokens
            run.estimated = run.estimated or usage.estimated
            return out
        raise AssertionError("unreachable")

    def _render(self, listing: str, groups: set[str], segments: list[str],
                assignment: Assignment | None) -> str:
        conditions = "\n".join(f"- {g}: {'; '.join(self._conditions[g])}"
                               for g in sorted(groups) if g in self._conditions)
        return self._template.substitute(
            standards=listing, assignment=_assignment_text(assignment), conditions=conditions or "(none)",
            segments="\n".join(f"[S{i}] {text}" for i, text in enumerate(segments, start=1)))

    # ---- shared guards ----

    def _evidence(self, items: list[SegmentEvidence], key: str, run: _Run) -> list[SegmentEvidence]:
        kept: list[SegmentEvidence] = []
        for ev in items:
            if not 1 <= ev.segment <= run.segments:
                run.drop(f"bad_segment:{ev.segment}")
                continue
            problem = _reason_problem(ev.reason, key)
            if problem:
                run.drop(problem)
                continue
            kept.append(SegmentEvidence(segment=ev.segment, reason=ev.reason.strip()))
        return kept

    def _strength(self, flag: bool, note: str | None, met: bool, key: str,
                  run: _Run) -> tuple[bool, str | None]:
        if not (run.strengths and flag):
            return False, None
        if not met:
            run.drop(f"strength_not_met:{key}")
            return False, None
        if note is None or not note.strip():
            run.drop(f"strength_without_note:{key}")  # V7: a strength always carries its note
            return False, None
        if word_count(note) > MAX_NOTE_WORDS:
            run.drop(f"strength_note_too_long:{key}")  # dropped, never truncated
            return False, None
        return True, note.strip()

    def _support_level(self, level: str | None, key: str, run: _Run) -> str | None:
        if level is None:
            run.drop(f"no_support_level:{key}")
        elif level not in SUPPORT_LEVELS:
            run.drop(f"bad_support_level:{key}")
        else:
            return level
        return None

    def _step_levels(self, j: ProgressionJudgment, growth: list[int], group: str,
                     run: _Run) -> dict[int, str] | None:
        """grade -> support level for each growth step: its step_levels entry, else support_level.
        None (the group is dropped) when a step has neither."""
        given: dict[int, str] = {}
        for entry in j.step_levels:
            if entry.grade not in growth or entry.grade in given:  # not a growth step, or a repeat
                run.drop(f"bad_step_level:{group}")
                continue
            given[entry.grade] = entry.support_level
        fallback = j.support_level if j.support_level in SUPPORT_LEVELS else None
        levels = {}
        for grade in growth:
            levels[grade] = given.get(grade) or fallback
            if levels[grade] is None:
                run.drop(f"bad_support_level:{group}" if j.support_level is not None
                         else f"missing_step_level:{group}")
                return None
        return levels

    @staticmethod
    def _flag_inversion(levels: list[str], group: str, run: _Run) -> None:
        """`levels`: the growth steps' levels, lowest grade first. A level that falls as the grade rises
        is a flag, not a drop: the levels are kept as the model gave them."""
        ranks = [SUPPORT_LEVELS.index(lv) for lv in levels]
        if any(b < a for a, b in zip(ranks, ranks[1:])):
            run.drop(f"level_inversion:{group}")

    def _observe(self, code: str, evidence: list[SegmentEvidence], level: str, run: _Run) -> None:
        # Ruling P1: the shared Observation field carries the support level for nodes.aggregate.
        run.observations.extend(Observation(standard_code=code, paragraph=ev.segment, reason=ev.reason,
                                            severity=level, level=run.level) for ev in evidence)

    def _ask(self, items: list, key_of, render, judge, run: _Run, schema: type[BaseModel],
             field: str, name_unjudged: bool = False) -> dict:
        """One call for all items; one follow-up for the items the reply left out; the rest unjudged
        (`unjudged:<key>` with name_unjudged: keys are known codes or groups, never model text)."""
        seen: set[str] = set()
        out = self._call(render(items), schema, run)
        raw = out.model_dump()
        judge(getattr(out, field), {key_of(i) for i in items}, seen)
        missing = [i for i in items if key_of(i) not in seen]
        if missing:
            follow = self._call(render(missing), schema, run)
            raw["follow_up"] = follow.model_dump()
            judge(getattr(follow, field), {key_of(i) for i in missing}, seen)
            for i in missing:
                if key_of(i) not in seen:
                    run.drop(f"unjudged:{key_of(i)}" if name_unjudged else "unjudged")
        return raw

    @staticmethod
    def _admit(key: str, allowed: set[str], seen: set[str], unknown: str, run: _Run) -> bool:
        if key in seen:
            run.drop(f"duplicate_judgment:{key}")
            return False
        if key not in allowed:
            run.drop(unknown)  # never echo an unknown, model-supplied key (may contain student text)
            return False
        seen.add(key)
        return True

    # ---- per standard ----

    def _per_standard(self, standards: list[StandardRef], segments: list[str],
                      assignment: Assignment | None, run: _Run) -> dict:
        def render(refs: list[StandardRef]) -> str:
            listing = "\n".join(f"- {s.code} | {_standard_text(s)}" for s in refs)
            return self._render(listing, {group_of(s.code) for s in refs}, segments, assignment)

        def judge(judgments, allowed: set[str], seen: set[str]) -> None:
            for j in judgments:
                code = j.standard_code
                if not self._admit(code, allowed, seen, "unknown_standard", run):
                    continue
                if j.verdict not in VERDICTS:
                    run.drop(f"bad_verdict:{code}")
                    continue
                level = None
                if j.verdict == "growth_area":
                    level = self._support_level(j.support_level, code, run)
                    if level is None:
                        continue
                evidence = self._evidence(j.evidence, code, run)
                if level and not evidence:
                    run.drop(f"no_valid_evidence:{code}")
                    continue
                strength, note = self._strength(j.notable_strength, j.strength_note,
                                                j.verdict == "meets", code, run)
                run.verdicts.append(Verdict(
                    standard_code=code, verdict=j.verdict, support_level=level,
                    notable_strength=strength, strength_note=note,
                    segments=sorted({ev.segment for ev in evidence}), reviewer=self.spec.id))
                if level:
                    self._observe(code, evidence, level, run)

        return self._ask(standards, lambda s: s.code, render, judge, run, PerStandardOut, "judgments")

    # ---- progression ----

    def _progression(self, standards: list[StandardRef], segments: list[str],
                     assignment: Assignment | None, run: _Run) -> dict:
        by_group = {p.group: p for p in progressions(standards)}

        def render(progs: list[Progression]) -> str:
            # every code in a merged step shows the step's grade: that is the working grade to name
            listing = "\n\n".join(
                f"## {p.group}\n" + "\n".join(
                    f"- {r.code} (grade {grade}) | {r.sub_expectation or r.description}"
                    for grade, refs in p.steps for r in refs)
                for p in progs)
            return self._render(listing, {p.group for p in progs}, segments, assignment)

        def judge(judgments, allowed: set[str], seen: set[str]) -> None:
            for j in judgments:
                group = j.group
                if not self._admit(group, allowed, seen, "unknown_group", run):
                    continue
                p = by_group[group]
                if not j.evident:  # Ruling F1: no working grade, level, evidence or strength
                    run.verdicts.extend(Verdict(standard_code=r.code, verdict="not_evident",
                                                reviewer=self.spec.id)
                                        for _, refs in p.steps for r in refs)
                    continue
                wg = j.working_grade
                if wg is not None and wg not in [g for g, _ in p.steps]:
                    run.drop(f"bad_working_grade:{group}")
                    continue
                growth = [g for g, _ in p.steps if wg is None or g > wg]
                levels: dict[int, str] = {}
                if growth:
                    levels = self._step_levels(j, growth, group, run)
                    if levels is None:
                        continue
                evidence = self._evidence(j.evidence, group, run)
                if growth and not evidence:
                    run.drop(f"no_valid_evidence:{group}")
                    continue
                self._flag_inversion([levels[g] for g in growth], group, run)
                strength, note = self._strength(j.notable_strength, j.strength_note, wg is not None,
                                                group, run)
                segs = sorted({ev.segment for ev in evidence})
                for grade, refs in p.steps:
                    is_growth = grade in levels
                    top = strength and grade == wg  # the strength sits on the highest met step
                    for r in refs:
                        run.verdicts.append(Verdict(
                            standard_code=r.code, verdict="growth_area" if is_growth else "meets",
                            support_level=levels.get(grade), working_grade=wg,
                            notable_strength=top, strength_note=note if top else None,
                            segments=segs, reviewer=self.spec.id))
                        if is_growth:  # Ruling T6a: every derived growth area gets the evidence
                            self._observe(r.code, evidence, levels[grade], run)

        return self._ask(list(by_group.values()), lambda p: p.group, render, judge, run,
                         ProgressionOut, "progressions")

    # ---- downward search ----

    def _search(self, standards: list[StandardRef], segments: list[str],
                assignment: Assignment | None, run: _Run) -> dict:
        """Round 1 asks about every group's top step; each later round asks about the next lower step of
        the groups still unmet. A group stops at its first met step (its working grade), at not evident,
        at a dropped or unjudged reply, or after its lowest step (working grade null: below the band)."""
        by_group = {p.group: p for p in progressions(standards)}
        at = {g: len(p.steps) - 1 for g, p in by_group.items()}  # the step each group is asked; None: done
        growth: dict[str, list[_Growth]] = {g: [] for g in by_group}
        answered: set[str] = set()

        def render(groups: list[str]) -> str:
            # every code in a merged step shows the step's grade
            listing = "\n\n".join(
                f"## {g}\n" + "\n".join(f"- {r.code} (grade {grade}) | {r.sub_expectation or r.description}"
                                        for r in refs)
                for g in groups for grade, refs in [by_group[g].steps[at[g]]])
            return self._render(listing, set(groups), segments, assignment)

        def judge(judgments: list[StepJudgment], allowed: set[str], seen: set[str]) -> None:
            for j in judgments:
                if self._admit(j.group, allowed, seen, "unknown_group", run):
                    answered.add(j.group)
                    if not self._search_step(by_group[j.group], at[j.group], growth[j.group], j, run):
                        at[j.group] = None
                    else:
                        at[j.group] -= 1

        groups, rounds = list(by_group), []
        while groups:
            run.rounds += 1
            answered.clear()
            rounds.append(self._ask(groups, lambda g: g, render, judge, run, StepOut, "judgments",
                                    name_unjudged=True))
            for g in groups:
                if g not in answered:  # unjudged after its follow-up: keep the growth found above
                    self._search_close(by_group[g], growth[g], run)
                    at[g] = None
            groups = [g for g in groups if at[g] is not None]
        return {"rounds": rounds}

    def _search_step(self, p: Progression, i: int, found: list[_Growth], j: StepJudgment,
                     run: _Run) -> bool:
        """Judge one group's reply on step `i`. True: unmet with a lower step left, so ask that next.
        False: the group is finished and its verdicts are written."""
        g = p.group
        if j.verdict not in VERDICTS:
            run.drop(f"bad_verdict:{g}")
            return self._search_close(p, found, run)
        met = j.verdict == "meets"
        strength = self._strength(j.notable_strength, j.strength_note, met, g, run)
        if j.verdict == "not_evident":  # Ruling F1: no level, evidence or strength
            return self._search_close(p, found, run, absent_from=i)
        evidence = self._evidence(j.evidence, g, run)
        if met:
            return self._search_close(p, found, run, met_at=i, met_evidence=evidence, strength=strength)
        level = self._support_level(j.support_level, g, run)
        if level is None:
            return self._search_close(p, found, run)
        if not evidence:
            run.drop(f"no_valid_evidence:{g}")
            return self._search_close(p, found, run)
        found.append(_Growth(i, level, evidence))
        if i == 0:  # no lower step in the band: below the band
            return self._search_close(p, found, run)
        return True

    def _search_close(self, p: Progression, found: list[_Growth], run: _Run, met_at: int | None = None,
                      met_evidence: list[SegmentEvidence] | None = None,
                      strength: tuple[bool, str | None] = (False, None), absent_from: int | None = None) -> bool:
        """Write one finished group's verdicts: its growth steps; the met step and every step below it
        meet (those below with no evidence); not evident from step `absent_from` down. Steps never judged
        (after a dropped or unjudged reply) get no verdict. Returns False (the group is finished).

        Ruling S1-4: a null working grade means "below the band" only when the lowest step was judged a
        growth area. A search that stopped earlier with no met step (a dropped or unjudged reply, or not
        evident below a growth area) is marked `search_incomplete:<group>`. Not evident at the top step
        is a complete answer (every code not_evident) and is not marked."""
        wg = p.steps[met_at][0] if met_at is not None else None
        met_evidence = met_evidence or []
        grown = {x.step: x for x in found}
        for i, (_, refs) in enumerate(p.steps):
            if i in grown:
                kw = dict(verdict="growth_area", support_level=grown[i].level, working_grade=wg,
                          segments=sorted({ev.segment for ev in grown[i].evidence}))
            elif met_at is not None and i <= met_at:
                top = i == met_at and strength[0]  # the strength sits on the met step
                kw = dict(verdict="meets", working_grade=wg, notable_strength=top,
                          strength_note=strength[1] if top else None,
                          segments=sorted({ev.segment for ev in met_evidence}) if i == met_at else [])
            elif absent_from is not None and i <= absent_from:
                kw = dict(verdict="not_evident")
            else:
                continue
            for r in refs:
                run.verdicts.append(Verdict(standard_code=r.code, reviewer=self.spec.id, **kw))
                if i in grown:  # Ruling T6a: each growth code gets its round's evidence
                    self._observe(r.code, grown[i].evidence, grown[i].level, run)
        if met_at is None and 0 not in grown and (found or absent_from is None):
            run.drop(f"search_incomplete:{p.group}")
        self._flag_inversion([grown[i].level for i in sorted(grown)], p.group, run)
        return False

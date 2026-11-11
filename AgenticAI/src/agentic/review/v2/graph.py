"""v2 LangGraph workflow:

fetch -> route -> mc -> END
               -> segment -> plan -> band (one Send per reviewer) -> merge -> root_cause -> aggregate
                  -> verify -> END
"""
import time
from collections.abc import Iterator
from dataclasses import replace

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from agentic.decisions.base import DecisionModel
from agentic.llm.base import ChatModel, chat_key, model_label
from agentic.lms.base import LMSClient
from agentic.models import (
    Finding, MetStandard, ReviewResult, StandardRef, SubmissionRef, Verification, word_count,
)
from agentic.review.graph import _timed
from agentic.review.nodes import aggregate
from agentic.review.strategies import StandardsSource
from agentic.review.v2.bands import BandReviewer
from agentic.review.v2.mc import mc_lookup
from agentic.review.v2.options import ResolvedOptions
from agentic.review.v2.root_cause import RootCauseStep, needs_root_cause
from agentic.review.v2.scope import BandScope
from agentic.review.v2.segment import segment
from agentic.review.v2.state import ReviewStateV2
from agentic.review.verify import verify
from agentic.settings.base import settings_hash
from agentic.tuning import Profile, plan_reviewers

ROOT_CAUSE = "root_cause"
VERIFY = "verify"
ROOT_CAUSE_PROMPT = "root_cause.v1"  # the root-cause step's prompt; not a band reviewer, so not in bands.toml
_READING = ("RL", "RI")
_TOP_GRADE = 12
_MAX_MARKS = 30  # code + "growth area in" + 30 marks + "and N more." = 37 words


class _Fixed:
    """A reviewer's standards, looked up once in `plan` (BandReviewer asks its scope for them)."""

    def __init__(self, refs: list[StandardRef]):
        self._refs = refs

    def standards(self) -> list[StandardRef]:
        return list(self._refs)


def _strands(strands: tuple[str, ...], reading_strand: str | None) -> list[str]:
    """A module's reading strand (RL or RI) keeps only that reading strand; none keeps both."""
    if reading_strand is None:
        return list(strands)
    return [x for x in strands if x not in _READING or x == reading_strand]


def _model_of(chat: ChatModel, key: str) -> dict[str, str]:
    name = model_label(chat)
    return {key: name} if name else {}


def _growth_findings(state) -> list[Finding]:
    """One finding per growth-area verdict, from its observations (nodes.aggregate), with the support
    level copied onto it. Pure, so root_cause and aggregate see the same findings."""
    growth = {v.standard_code for v in state["verdicts"] if v.verdict == "growth_area"}
    refs = [s for s in state["standards"] if s.code in growth]
    findings, _ = aggregate([o for o in state["observations"] if o.standard_code in growth], refs, [])
    return [f.model_copy(update={"summary": _summary(f), "support_level": f.severity}) for f in findings]


def _summary(f: Finding) -> str:
    """A template summary of 1-40 words: the concept, else the code, then the cited segments
    (the first _MAX_MARKS of them when there are more)."""
    marks = [f"S{p}" for p in f.paragraphs]
    for head in (f.concept or f.standard_code, f.standard_code):
        summary = f"{head}: growth area in {', '.join(marks)}."
        if word_count(summary) <= 40:
            return summary
    shown = marks[:_MAX_MARKS]
    return f"{f.standard_code}: growth area in {', '.join(shown)} and {len(marks) - len(shown)} more."


def build_v2_graph(lms: LMSClient, standards: StandardsSource, chat: ChatModel,
                   decision: DecisionModel | None, conditions: dict[str, list[str]],
                   templates: dict[str, str], options: ResolvedOptions, profile: Profile,
                   miss_threshold: float = 0.5, chats: dict[str, ChatModel] | None = None):
    """`profile` carries the resolved prompt of each reviewer; `templates` maps each prompt ref to its
    text. `chats` maps a reviewer's `chat_key(model, reasoning)` to its chat; others use `chat`. Dependencies are closed
    over; per-run objects (the band reviewers) live in the state."""
    chats = chats or {}
    strengths = bool(options.settings.get("notable_strengths_enabled"))

    def fetch(state):
        ref = state["submission_ref"]
        course = lms.get_course(ref.course_id)
        sub = lms.get_submission(ref.course_id, ref.assignment_id, ref.user_id)
        assignment = lms.get_assignment(ref.course_id, ref.assignment_id)
        return {"course_grade": course.grade_level,
                "submission_ref": ref.model_copy(update={  # the attempt actually reviewed: the LMS serves the latest
                    "submission_id": sub.id, "attempt": sub.attempt, "submitted_at": sub.submitted_at}),
                "assignment": assignment, "submission_type": assignment.submission_type, "submission": sub}

    def route(state) -> str:
        return "mc" if state["submission_type"] == "multiple_choice" else "segment"

    def mc(state):
        min_picks = int(options.settings.get("mc_finding_min_picks", 2))
        verdicts, observations, findings = mc_lookup(state["assignment"], state["submission"].answers, min_picks)
        catalog = {s.code: s for s in standards.in_range(3, _TOP_GRADE)}
        return {"verdicts": verdicts, "observations": observations, "findings": findings,
                "standards": [catalog[v.standard_code] for v in verdicts if v.standard_code in catalog],
                "met_standards": [], "links": [], "usage": {}, "models": {}, "prompt_versions": {},
                "llm_calls": 0,
                "not_evident": [v.standard_code for v in verdicts if v.verdict == "not_evident"],
                "verification": Verification(status="disabled"), "trace": {"reviewers": {}}}

    def segment_node(state):
        segments, level = segment(state["submission"], state["assignment"])
        return {"segments": segments, "segment_level": level}

    def plan(state):
        reading = state["assignment"].reading_strand
        bands, refs = [], {}
        for spec, lo, hi in plan_reviewers(profile, state["course_grade"]):
            mine = BandScope(standards, lo, hi, _strands(spec.strands, reading)).standards()
            refs.update((s.code, s) for s in mine)
            own = chats.get(chat_key(spec.model, spec.reasoning), chat)
            bands.append(BandReviewer(spec, lo, hi, _Fixed(mine), own, templates[spec.prompt], conditions))
        return {"bands": bands, "standards": sorted(refs.values(), key=lambda s: s.code)}

    def fan_out(state):
        sends = [Send("band", {"band": b, "segments": state["segments"],
                               "segment_level": state["segment_level"], "assignment": state["assignment"]})
                 for b in state["bands"]]
        return sends or "merge"

    def band(state):
        start = time.perf_counter()
        b = state["band"]
        res = b.review(state["segments"], state["segment_level"], state["assignment"], strengths)
        seconds = time.perf_counter() - start
        return {"band_results": [(res, seconds)], "timings": {f"band:{b.spec.id}": seconds}}

    def merge(state):
        order = [b.spec.id for b in state["bands"]]
        results = sorted(state.get("band_results", []), key=lambda r: order.index(r[0].reviewer_id))
        owner: dict[str, str] = {}
        verdicts, observations, dropped = [], [], []
        usage, models, trace, prompts, calls = {}, {}, {}, {}, 0
        for res, seconds in results:
            for v in res.verdicts:
                if v.standard_code in owner:
                    raise ValueError(f"standard {v.standard_code} judged by both {owner[v.standard_code]} "
                                     f"and {res.reviewer_id}: check bands.toml")
                owner[v.standard_code] = res.reviewer_id
            verdicts += res.verdicts
            observations += res.observations
            dropped += res.dropped
            usage[res.reviewer_id] = res.usage
            if res.model:
                models[res.reviewer_id] = res.model
            prompts[res.reviewer_id] = res.prompt_version
            calls += res.calls
            trace[res.reviewer_id] = {
                "prompt": res.prompt_version, "standards_count": res.standards_count,
                "tokens": res.usage.input_tokens + res.usage.output_tokens, "seconds": seconds,
                "rounds": res.rounds, "reply": res.raw}
        return {"verdicts": verdicts, "observations": observations, "dropped": dropped,
                "usage": usage, "models": models,
                "prompt_versions": prompts, "llm_calls": calls, "trace": {"reviewers": trace},
                "links": [], "details": {}}

    def root_cause(state):
        if not options.root_cause:
            return {}
        findings = _growth_findings(state)
        update = {"prompt_versions": {**state["prompt_versions"], ROOT_CAUSE: ROOT_CAUSE_PROMPT}}
        if not needs_root_cause(findings, state["verdicts"]):
            return update
        step = RootCauseStep(chat, templates[ROOT_CAUSE_PROMPT], conditions)
        links, details, dropped, usage = step.run(findings, state["segments"],
                                                  {s.code: s for s in state["standards"]})
        return {**update, "links": links, "details": details, "dropped": dropped,
                "usage": {**state["usage"], ROOT_CAUSE: usage.model_copy(update={"calls": 1})}, "llm_calls": state["llm_calls"] + 1,
                "models": {**state["models"], **_model_of(chat, ROOT_CAUSE)}}

    def aggregate_node(state):
        details = state.get("details", {})
        findings = []
        for f in _growth_findings(state):
            d = details.get(f.standard_code)
            findings.append(f if d is None else f.model_copy(
                update={"condition": d.condition, "pattern": d.pattern, "skill_state": d.skill_state}))
        refs = {s.code: s for s in state["standards"]}
        met = [MetStandard(standard_code=v.standard_code, concept=refs[v.standard_code].concept)
               for v in state["verdicts"] if v.notable_strength and v.standard_code in refs]
        return {"findings": findings, "met_standards": met,
                "not_evident": [v.standard_code for v in state["verdicts"] if v.verdict == "not_evident"]}

    def verify_node(state):
        judged = {f.standard_code for f in state["findings"]} | {m.standard_code for m in state["met_standards"]}
        refs = [s for s in state["standards"] if s.code in judged]
        findings, met, verification = verify(
            state["segments"], state["course_grade"], refs, state["findings"], state["met_standards"],
            decision if options.verify else None, miss_threshold, assignment=state["assignment"])
        findings = [f.model_copy(update={"confidence": f.p_correct}) for f in findings]
        growth = {f.standard_code: f.p_correct for f in findings}
        strength = {m.standard_code: m.p_met for m in met}
        verdicts = []
        for v in state["verdicts"]:
            if v.verdict == "growth_area" and v.standard_code in growth:
                v = v.model_copy(update={"confidence": growth[v.standard_code]})
            elif v.notable_strength and v.standard_code in strength:
                v = v.model_copy(update={"confidence": strength[v.standard_code]})
            verdicts.append(v)
        models = state["models"]
        if verification.model:  # the decision model actually ran
            models = {**models, VERIFY: verification.model}
        return {"findings": findings, "met_standards": met, "verdicts": verdicts, "verification": verification,
                "models": models}

    g = StateGraph(ReviewStateV2)
    for name, fn in [("fetch", fetch), ("mc", mc), ("segment", segment_node), ("plan", plan),
                     ("merge", merge), ("root_cause", root_cause), ("aggregate", aggregate_node),
                     ("verify", verify_node)]:
        g.add_node(name, _timed(name, fn))
    g.add_node("band", band)  # times itself, per reviewer
    g.add_edge(START, "fetch")
    g.add_conditional_edges("fetch", route, ["mc", "segment"])
    g.add_edge("mc", END)
    g.add_edge("segment", "plan")
    g.add_conditional_edges("plan", fan_out, ["band", "merge"])
    g.add_edge("band", "merge")
    for a, b in [("merge", "root_cause"), ("root_cause", "aggregate"), ("aggregate", "verify")]:
        g.add_edge(a, b)
    g.add_edge("verify", END)
    return g.compile()


def effective_profile(profile: Profile, options: ResolvedOptions) -> Profile:
    """The profile with each reviewer's prompt and reasoning level as resolved (run overrides applied)."""
    return Profile(name=profile.name,
                   reviewers=[replace(r, prompt=options.prompts.get(r.id, r.prompt),
                                      reasoning=options.reasoning.get(r.id, r.reasoning))
                           for r in profile.reviewers])


class ReviewerV2:
    """Same interface as review.graph.Reviewer: run(ref) -> ReviewResult and stream(ref)."""

    def __init__(self, graph, options: ResolvedOptions, tuning_hash: str, models: dict | None = None,
                 strands: list[str] | None = None):
        self.options = options
        self.tuning_hash = tuning_hash
        self.settings_hash = settings_hash(options.settings)  # the snapshot this reviewer runs with
        self.models = models
        self.strands = strands or []
        self.targets: list[str] = []  # v2 reviews every standard from grade 3 up; no targets
        self.assignment_context = True
        self._graph = graph
        self.last_result: ReviewResult | None = None

    def stream(self, ref: SubmissionRef) -> Iterator[tuple[str, float]]:
        self.last_result = None
        final: dict = {}
        for mode, chunk in self._graph.stream(
            {"submission_ref": ref, "dropped": [], "timings": {}, "band_results": []},
            stream_mode=["updates", "values"],
        ):
            if mode == "values":
                final = chunk
            else:
                for node, update in chunk.items():
                    yield node, sum((update or {}).get("timings", {}).values())
        st = final
        self.last_result = ReviewResult(
            submission_ref=st["submission_ref"], course_grade=st["course_grade"], strategy="full",
            standards=st["standards"], observations=st["observations"], findings=st["findings"],
            met_standards=st["met_standards"], not_evident=st.get("not_evident", []),
            dropped=st["dropped"], verification=st["verification"], timings=st["timings"],
            llm_calls=st["llm_calls"], trace=st["trace"], pipeline="v2",
            submission_type=st["submission_type"], verdicts=st["verdicts"], links=st["links"],
            usage=st["usage"], models=st.get("models", {}), prompt_versions=st["prompt_versions"],
            settings_hash=self.settings_hash, tuning_hash=self.tuning_hash,
        )

    def run(self, ref: SubmissionRef) -> ReviewResult:
        for _ in self.stream(ref):
            pass
        return self.last_result

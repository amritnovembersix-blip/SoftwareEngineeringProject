"""Strategy pattern: StandardsStrategy (Strategy), FullListStrategy / RagStrategy (ConcreteStrategies).

Guards record a Dropped entry and continue. Dropped reasons name the rule and the
code/paragraph only; they never copy LLM reason or issue text (it may quote the essay).
"""
from dataclasses import dataclass, field
from typing import Literal, Protocol

from pydantic import BaseModel

from agentic.llm.base import ChatModel, Embedder
from agentic.lms.base import Assignment
from agentic.models import Dropped, Observation, StandardRef, word_count
from agentic.review.prompts import analyze_prompt, assess_prompt, judge_prompt

MAX_REASON_WORDS = 12
VERDICTS = ("meets", "weakness", "not_evident")
SEVERITIES = ("approaching", "developing", "beginning")  # least to most severe


class StandardsSource(Protocol):
    def at_grade(self, grade: int) -> list[StandardRef]: ...
    def search(self, query_embedding: list[float], grade: int, k: int) -> list[StandardRef]: ...


@dataclass
class StrategyResult:
    standards: list[StandardRef]
    observations: list[Observation]
    dropped: list[Dropped]
    llm_calls: int
    trace: dict = field(default_factory=dict)
    not_evident: list[str] = field(default_factory=list)
    met_codes: list[str] | None = None  # None: every standard without a finding counts as met (RAG)


class StandardsStrategy(Protocol):
    name: Literal["full", "rag"]

    def run(self, paragraphs: list[str], course_grade: int,
            assignment: Assignment | None = None) -> StrategyResult: ...


class Evidence(BaseModel):
    paragraph: int
    reason: str


class StandardAssessment(BaseModel):
    standard_code: str
    verdict: str  # meets | weakness | not_evident (validated by the strategy's guards)
    severity: str | None = None  # required for weakness: approaching | developing | beginning
    evidence: list[Evidence] = []


class AssessmentOut(BaseModel):
    assessments: list[StandardAssessment]


class IssueOut(BaseModel):
    paragraph: int
    issue: str


class AnalysisOut(BaseModel):
    issues: list[IssueOut]


class Judgment(BaseModel):
    issue_id: int
    standard_code: str | None = None
    reason: str | None = None


class JudgmentOut(BaseModel):
    judgments: list[Judgment]


def _reason_problem(reason: str | None, code: str) -> str | None:
    if reason is None or word_count(reason) == 0:
        return f"reason_blank:{code}"
    if word_count(reason) > MAX_REASON_WORDS:
        return f"reason_too_long:{code}"
    return None


class FullListStrategy:
    name: Literal["full", "rag"] = "full"

    def __init__(self, chat: ChatModel, standards: StandardsSource):
        self._chat = chat
        self._standards = standards

    def run(self, paragraphs: list[str], course_grade: int,
            assignment: Assignment | None = None) -> StrategyResult:
        standards = self._standards.at_grade(course_grade)
        allowed = {s.code for s in standards}
        system, user = assess_prompt(paragraphs, standards, assignment)
        out = self._chat.structured(system, user, AssessmentOut)

        observations: list[Observation] = []
        dropped: list[Dropped] = []

        def drop(reason: str) -> None:
            dropped.append(Dropped(stage="assess", reason=reason))

        seen: set[str] = set()
        met_codes: list[str] = []
        not_evident: list[str] = []
        for a in out.assessments:
            code = a.standard_code
            if code not in allowed:
                drop("unknown_standard")
                continue
            if code in seen:
                drop(f"duplicate_assessment:{code}")
                continue
            seen.add(code)
            if a.verdict == "meets":
                met_codes.append(code)
                continue
            if a.verdict == "not_evident":
                not_evident.append(code)
                continue
            if a.verdict != "weakness":
                drop(f"bad_verdict:{code}")
                continue
            if a.severity not in SEVERITIES:
                drop(f"bad_severity:{code}")
                continue
            valid = 0
            for ev in a.evidence:
                if not 1 <= ev.paragraph <= len(paragraphs):
                    drop(f"bad_paragraph:{ev.paragraph}")
                    continue
                problem = _reason_problem(ev.reason, code)
                if problem:
                    drop(problem)
                    continue
                observations.append(Observation(standard_code=code, paragraph=ev.paragraph,
                                                reason=ev.reason.strip(), severity=a.severity))
                valid += 1
            if valid == 0:
                drop(f"no_valid_evidence:{code}")
        for s in standards:
            if s.code not in seen:
                drop(f"not_assessed:{s.code}")
        return StrategyResult(standards, observations, dropped, llm_calls=1,
                              not_evident=not_evident, met_codes=met_codes)


class RagStrategy:
    name: Literal["full", "rag"] = "rag"

    def __init__(self, chat: ChatModel, embedder: Embedder, standards: StandardsSource,
                 k: int = 3, max_issues: int = 8):
        self._chat = chat
        self._embedder = embedder
        self._standards = standards
        self._k = k
        self._max_issues = max_issues

    def run(self, paragraphs: list[str], course_grade: int,
            assignment: Assignment | None = None) -> StrategyResult:
        grade_set = self._standards.at_grade(course_grade)
        dropped: list[Dropped] = []
        system, user = analyze_prompt(paragraphs, self._max_issues, assignment)
        issues = self._chat.structured(system, user, AnalysisOut).issues
        if len(issues) > self._max_issues:
            issues = issues[: self._max_issues]
            dropped.append(Dropped(stage="analyze", reason="too_many_issues"))
        if not issues:
            return StrategyResult(grade_set, [], dropped, llm_calls=1,
                                  trace={"candidates": {}, "issue_count": 0})

        vectors = self._embedder.embed([i.issue for i in issues], "query")
        candidates = {n: self._standards.search(v, course_grade, self._k)
                      for n, v in enumerate(vectors, start=1)}
        trace = {"candidates": {n: [s.code for s in c] for n, c in candidates.items()},
                 "issue_count": len(issues)}

        system, user = judge_prompt(issues, candidates, assignment)
        out = self._chat.structured(system, user, JudgmentOut)
        observations: list[Observation] = []

        def drop(reason: str) -> None:
            dropped.append(Dropped(stage="judge", reason=reason))

        known = {s.code for s in grade_set}
        judged: set[int] = set()
        for j in out.judgments:
            if j.issue_id not in candidates:
                drop(f"unknown_issue:{j.issue_id}")
                continue
            if j.issue_id in judged:
                drop(f"duplicate_judgment:{j.issue_id}")
                continue
            judged.add(j.issue_id)
            if j.standard_code is None:
                continue
            code = j.standard_code
            if code not in {s.code for s in candidates[j.issue_id]}:
                # never echo an unknown, LLM-supplied code (may contain essay text)
                drop(f"not_a_candidate:{code}" if code in known else "unknown_standard")
                continue
            para = issues[j.issue_id - 1].paragraph
            if not 1 <= para <= len(paragraphs):
                drop(f"bad_paragraph:{para}")
                continue
            problem = _reason_problem(j.reason, code)
            if problem:
                drop(problem)
                continue
            observations.append(Observation(standard_code=code, paragraph=para,
                                            reason=(j.reason or "").strip()))
        return StrategyResult(grade_set, observations, dropped, llm_calls=2, trace=trace)

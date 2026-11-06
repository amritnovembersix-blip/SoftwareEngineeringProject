"""Root-cause linking: one LLM call links a grade 6+ growth area to a grade 3-5 growth area that shows
up in the same segment, and adds condition / pattern / skill state detail.

Failures never stop a review: an exception gives empty results and one Dropped entry. Dropped reasons
are short codes; they never copy model text (it may quote the student's work).
"""
from string import Template

from agentic.llm.base import ChatModel, DailyQuotaExceeded, Usage
from agentic.models import Dropped, Finding, FindingLink, StandardRef, Verdict, word_count
from agentic.review.v2.schemas import RootCauseOut, RootDetail
from agentic.review.v2.scope import group_of

STAGE = "root_cause"
MAX_RATIONALE_WORDS = 40
PATTERNS = ("consistent", "occasional")
SKILL_STATES = ("missing", "misapplied")

_SYSTEM = ("You help a teacher see why a student keeps missing the same standards. "
           "Follow the instructions in the message and reply only in the requested structure.")


def _grade(code: str) -> int:
    """First segment of a code; `9-10` and `11-12` count as their lower grade."""
    return int(code.split(".")[0].split("-")[0])


def _standard_text(s: StandardRef) -> str:
    return f"{s.description} {s.sub_expectation}" if s.sub_expectation else s.description


def _participants(findings: list[Finding]) -> list[Finding]:
    """Findings in a cross-band shared segment: grade 6+ with a grade 3-5 finding, and the reverse."""
    high = [f for f in findings if _grade(f.standard_code) >= 6]
    low = [f for f in findings if 3 <= _grade(f.standard_code) <= 5]
    keep = {id(f) for h in high for lo in low if set(h.paragraphs) & set(lo.paragraphs) for f in (h, lo)}
    return [f for f in findings if id(f) in keep]


def _cross_band(top: Finding, root: Finding) -> bool:
    """Ruling F3 (spec A1): a link traces a grade 6+ growth area to a grade 3-5 root."""
    return _grade(top.standard_code) >= 6 and 3 <= _grade(root.standard_code) <= 5


def needs_root_cause(findings: list[Finding], verdicts: list[Verdict]) -> bool:
    growth = {v.standard_code for v in verdicts if v.verdict == "growth_area"}
    return bool(_participants([f for f in findings if f.standard_code in growth]))


class RootCauseStep:
    def __init__(self, chat: ChatModel, template: str, conditions: dict[str, list[str]]):
        self._chat = chat
        self._template = Template(template)
        self._conditions = conditions

    def run(self, findings: list[Finding], segments: list[str], standards: dict[str, StandardRef]
            ) -> tuple[list[FindingLink], dict[str, RootDetail], list[Dropped], Usage]:
        none = Usage(input_tokens=0, output_tokens=0, estimated=False)
        part = _participants(findings)
        if not part:
            return [], {}, [], none
        try:
            out, usage = self._chat.complete(_SYSTEM, self._render(part, segments, standards),
                                             RootCauseOut)
        except DailyQuotaExceeded:
            raise  # waiting will not help; the run pauses
        except Exception as e:
            return [], {}, [Dropped(stage=STAGE, reason=type(e).__name__)], none
        dropped: list[Dropped] = []
        links = self._links(out, part, dropped)
        details = self._details(out, part, dropped)
        return links, details, dropped, usage

    def _render(self, part: list[Finding], segments: list[str], standards: dict[str, StandardRef]) -> str:
        def line(f: Finding) -> str:
            ref = standards.get(f.standard_code)
            return " | ".join([f"- {f.standard_code}", *([_standard_text(ref)] if ref else []), f.summary])

        listing = "\n".join(line(f) for f in part)
        groups = sorted({group_of(f.standard_code) for f in part})
        conditions = "\n".join(f"- {g}: {'; '.join(self._conditions[g])}"
                               for g in groups if g in self._conditions)
        cited = sorted({p for f in part for p in f.paragraphs if 1 <= p <= len(segments)})
        return self._template.substitute(
            standards=listing, conditions=conditions or "(none)", assignment="(none)",
            segments="\n".join(f"[S{i}] {segments[i - 1]}" for i in cited))

    @staticmethod
    def _links(out: RootCauseOut, part: list[Finding], dropped: list[Dropped]) -> list[FindingLink]:
        by_code = {f.standard_code: f for f in part}
        links: list[FindingLink] = []
        seen: set[tuple[str, str]] = set()
        for link in out.links:
            top, root = by_code.get(link.finding_code), by_code.get(link.root_code)
            if (link.finding_code, link.root_code) in seen:
                reason = "duplicate_link"
            elif top is None or root is None or not _cross_band(top, root):
                reason = "invented_link"
            elif not set(top.paragraphs) & set(root.paragraphs):
                reason = "no_shared_segment"
            elif not 1 <= word_count(link.rationale) <= MAX_RATIONALE_WORDS:
                reason = "bad_rationale"
            else:
                seen.add((link.finding_code, link.root_code))
                links.append(FindingLink(finding_code=link.finding_code, root_code=link.root_code,
                                         rationale=link.rationale.strip()))
                continue
            dropped.append(Dropped(stage=STAGE, reason=reason))
        return links

    def _details(self, out: RootCauseOut, part: list[Finding], dropped: list[Dropped]
                 ) -> dict[str, RootDetail]:
        allowed = {f.standard_code for f in part}
        details: dict[str, RootDetail] = {}

        def drop(reason: str) -> None:
            dropped.append(Dropped(stage=STAGE, reason=reason))

        for d in out.details:
            code = d.standard_code
            if code not in allowed:
                drop("unknown_detail")
                continue
            if code in details:
                drop("duplicate_detail")
                continue
            condition, pattern, state = d.condition, d.pattern, d.skill_state
            if condition is not None and condition not in self._conditions.get(group_of(code), []):
                drop("unknown_condition")
                condition = None
            if pattern is not None and pattern not in PATTERNS:
                drop("bad_pattern")
                pattern = None
            if state is not None and state not in SKILL_STATES:
                drop("bad_skill_state")
                state = None
            details[code] = RootDetail(standard_code=code, condition=condition, pattern=pattern,
                                       skill_state=state)
        return details

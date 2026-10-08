"""Review graph nodes: html_to_paragraphs and aggregate."""
import re
from html.parser import HTMLParser

from agentic.review.verify import verify
from agentic.models import Finding, MetStandard, Observation, StandardRef, word_count


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


class _ParagraphParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.paragraphs: list[str] = []
        self._buf: list[str] | None = None

    def _close(self) -> None:
        if self._buf is not None:
            text = _clean("".join(self._buf))
            if text:
                self.paragraphs.append(text)
            self._buf = None

    def handle_starttag(self, tag, attrs):
        if tag == "p":
            self._close()
            self._buf = []
        elif self._buf is not None:
            self._buf.append(" ")  # other tags act as whitespace

    def handle_endtag(self, tag):
        if tag == "p":
            self._close()
        elif self._buf is not None:
            self._buf.append(" ")

    def handle_data(self, data):
        if self._buf is not None:
            self._buf.append(data)


def html_to_paragraphs(body: str) -> list[str]:
    parser = _ParagraphParser()
    parser.feed(body)
    parser.close()
    parser._close()
    if re.search(r"<p[\s>/]", body, re.IGNORECASE):
        return parser.paragraphs
    chunks = (_clean(c) for c in re.split(r"\n\s*\n", body))
    return [c for c in chunks if c]


_SEVERITY_RANK = {"approaching": 1, "developing": 2, "beginning": 3}


def aggregate(
    observations: list[Observation], standards: list[StandardRef], met_codes: list[str] | None = None
) -> tuple[list[Finding], list[MetStandard]]:
    """Group observations into one Finding per standard (no LLM, no essay text).

    met_codes: the standards judged as meeting. None means every standard without a finding (RAG).
    """
    findings: list[Finding] = []
    met: list[MetStandard] = []
    for std in standards:
        obs = [o for o in observations if o.standard_code == std.code]
        if not obs:
            if met_codes is None or std.code in met_codes:
                met.append(MetStandard(standard_code=std.code, concept=std.concept))
            continue
        severities = [o.severity for o in obs if o.severity in _SEVERITY_RANK]
        paragraphs = sorted({o.paragraph for o in obs})
        marks = ", ".join("¶" + str(p) for p in paragraphs)
        summary = f"{std.concept}: weakness noted in {marks}."
        if word_count(summary) > 40:
            summary = f"{std.code}: weakness noted in {marks}."
        findings.append(
            Finding(
                standard_code=std.code,
                concept=std.concept,
                description=std.description,
                paragraphs=paragraphs,
                reasons=[o.reason for o in obs],
                observation_count=len(obs),
                summary=summary,
                severity=max(severities, key=_SEVERITY_RANK.get) if severities else None,
            )
        )
    return findings, met


# --- graph node factories: dependencies are closed over, never imported as concrete Adapters ---

def make_fetch(lms, use_context: bool = True):
    def fetch(state):
        ref = state["submission_ref"]
        course = lms.get_course(ref.course_id)
        sub = lms.get_submission(ref.course_id, ref.assignment_id, ref.user_id)
        assignment = lms.get_assignment(ref.course_id, ref.assignment_id) if use_context else None
        return {
            "course_grade": course.grade_level,
            "submission_ref": ref.model_copy(update={"submitted_at": sub.submitted_at}),
            "body": sub.body,
            "assignment": assignment,
        }
    return fetch


def make_number_paragraphs():
    def number_paragraphs(state):
        # HTML body -> ordered paragraphs; 1-based numbering is applied downstream.
        return {"paragraphs": html_to_paragraphs(state["body"])}
    return number_paragraphs


def make_select_and_assess(strategy):
    def select_and_assess(state):
        res = strategy.run(state["paragraphs"], state["course_grade"], state.get("assignment"))
        return {
            "strategy": strategy.name,
            "standards": res.standards,
            "observations": res.observations,
            "dropped": res.dropped,
            "llm_calls": res.llm_calls,
            "trace": res.trace,
            "not_evident": res.not_evident,
            "met_codes": res.met_codes,
        }
    return select_and_assess


def make_aggregate():
    def aggregate_node(state):
        findings, met = aggregate(state["observations"], state["standards"], state.get("met_codes"))
        return {"findings": findings, "met_standards": met}
    return aggregate_node


def make_verify(decision, miss_threshold):
    def verify_node(state):
        findings, met, verification = verify(
            state["paragraphs"], state["course_grade"], state["standards"],
            state["findings"], state["met_standards"], decision, miss_threshold,
            assignment=state.get("assignment"),
        )
        return {"findings": findings, "met_standards": met, "verification": verification}
    return verify_node

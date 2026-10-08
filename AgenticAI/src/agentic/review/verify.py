"""Jev verification: adds probabilities to a review, never removes anything."""
from agentic.decisions.base import DecisionModel, NoulQuestion
from agentic.lms.base import Assignment
from agentic.models import Finding, MetStandard, StandardRef, Verification


def build_request(
    paragraphs: list[str],
    grade: int,
    standards: list[StandardRef],
    findings: list[Finding],
    met: list[MetStandard],
    assignment: Assignment | None = None,
) -> tuple[dict, dict[str, NoulQuestion]]:
    state = {
        "grade": grade,
        "paragraphs": {str(i): p for i, p in enumerate(paragraphs, start=1)},
        "standards": {s.code: {"concept": s.concept, "description": s.description} for s in standards},
        "findings": {f.standard_code: {"paragraphs": f.paragraphs, "reasons": f.reasons} for f in findings},
    }
    context = {}
    if assignment is not None and assignment.description:
        state["assignment"] = {"name": assignment.name, "description": assignment.description}
        if assignment.notes:
            state["assignment"]["notes"] = dict(assignment.notes)
        context = {"context": ("`assignment` shows what the student was asked; use it only to decide which "
                               "parts of the standard were called for, never to judge instruction-following.")}
    questions: dict[str, NoulQuestion] = {}
    for f in findings:
        c = f.standard_code
        questions[f"finding:{c}"] = NoulQuestion(
            instructions={
                "question": (
                    f"Does the essay in `paragraphs` really show the weakness described in "
                    f"`findings.{c}` for standard `standards.{c}`?"
                ),
                **context,
            },
            criteria={
                "true": "The weakness is clearly present at the cited paragraphs.",
                "false": "The essay meets this standard there.",
            },
        )
    for m in met:
        c = m.standard_code
        questions[f"met:{c}"] = NoulQuestion(
            instructions={
                "question": f"Does the essay in `paragraphs` meet standard `standards.{c}` at grade `grade`?",
                **context,
            },
            criteria={
                "true": "The essay meets the standard.",
                "false": "A clear weakness exists against the standard.",
            },
        )
    return state, questions


def _prob(probabilities: dict, key: str) -> float | None:
    v = probabilities.get(key)
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v) if 0.0 <= v <= 1.0 else None


def verify(
    paragraphs: list[str],
    grade: int,
    standards: list[StandardRef],
    findings: list[Finding],
    met: list[MetStandard],
    decision: DecisionModel | None,
    miss_threshold: float,
    assignment: Assignment | None = None,
) -> tuple[list[Finding], list[MetStandard], Verification]:
    if decision is None:
        return findings, met, Verification(status="disabled")
    if not findings and not met:
        return findings, met, Verification(status="ok")
    state, questions = build_request(paragraphs, grade, standards, findings, met, assignment)
    try:
        result = decision.noul(state, questions)
        probs = result.probabilities
        new_findings = [
            f.model_copy(update={"p_correct": _prob(probs, f"finding:{f.standard_code}")}) for f in findings
        ]
        new_met = []
        for m in met:
            p = _prob(probs, f"met:{m.standard_code}")
            new_met.append(
                m.model_copy(update={"p_met": p, "possible_miss": p is not None and p < miss_threshold})
            )
        verification = Verification(status="ok", model=result.model, input_tokens=result.input_tokens)
    except Exception:  # verification is an add-on; never lose the review
        return findings, met, Verification(status="unavailable")
    return new_findings, new_met, verification

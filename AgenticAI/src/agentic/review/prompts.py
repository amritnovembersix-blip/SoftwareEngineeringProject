"""Prompt builders: plain functions returning (system, user).

The assignment (title + teacher's prompt) is context only: it decides which standards, and which parts
of each, were called for. Quality is judged only against the standard's own wording.
"""
from agentic.lms.base import Assignment
from agentic.models import StandardRef

_COMMON = (
    "You are reviewing student writing against Missouri Learning Standards. "
    "Cite paragraph numbers (¶1, ¶2, ...). Each reason is at most 12 words and describes "
    "the weakness without quoting the essay. Report only real weaknesses; "
    "meeting all standards is a valid outcome."
)

RULE = (
    "Use the assignment only to decide which standards, and which parts of each standard, "
    "the assignment called for. Judge quality only against the standard's own wording. "
    "Never report a weakness for not following the assignment's instructions. If a "
    "standard (or part of it) was not called for, or the essay gives no evidence for it, "
    "answer not_evident."
)

_VERDICTS = (
    " For every listed standard return standard_code, verdict and evidence. verdict is one of: "
    "meets (the essay shows the standard), weakness (the essay attempts it and clearly falls short), "
    "or not_evident (the essay gives no evidence either way). For weakness also give severity: "
    "approaching (occasional), developing (frequent) or beginning (pervasive), and at least one "
    "evidence item {paragraph, reason}. A single essay rarely shows every standard; prefer "
    "not_evident over weakness when the essay simply does not address a standard."
)


def assignment_block(assignment: Assignment | None) -> str:
    if assignment is None or not assignment.description:
        return ""
    focus = ""
    if assignment.notes:
        focus = "Assignment focus by standard:\n" + "\n".join(
            f"- {code}: {note}" for code, note in assignment.notes.items()) + "\n"
    return f"Assignment (context only):\nTitle: {assignment.name}\n{assignment.description}\n{focus}\n"


def _system(base: str, block: str) -> str:
    return base + (" " + RULE if block else "")


def numbered_essay(paragraphs: list[str]) -> str:
    return "\n".join(f"¶{i} {p}" for i, p in enumerate(paragraphs, start=1))


def assess_prompt(paragraphs: list[str], standards: list[StandardRef],
                  assignment: Assignment | None = None) -> tuple[str, str]:
    block = assignment_block(assignment)
    listing = "\n".join(f"- {s.code} | {s.concept}: {s.description}" for s in standards)
    user = f"{block}Standards:\n{listing}\n\nEssay:\n{numbered_essay(paragraphs)}"
    return _system(_COMMON + _VERDICTS, block), user


def analyze_prompt(paragraphs: list[str], max_issues: int,
                   assignment: Assignment | None = None) -> tuple[str, str]:
    block = assignment_block(assignment)
    base = (
        _COMMON
        + f" List up to {max_issues} writing weaknesses as {{paragraph, issue}}. "
        "Issues are about writing skill, not topic, and are at most 25 words each."
    )
    return _system(base, block), f"{block}Essay:\n{numbered_essay(paragraphs)}"


def judge_prompt(issues, candidates: dict[int, list[StandardRef]],
                 assignment: Assignment | None = None) -> tuple[str, str]:
    block = assignment_block(assignment)
    base = (
        _COMMON
        + " For each issue choose the single best matching standard. Choose only from the "
        "listed candidate codes, or null if none fits. Return {issue_id, standard_code, reason}."
    )
    blocks = []
    for i, issue in enumerate(issues, start=1):
        cands = "\n".join(f"  - {s.code} | {s.concept}: {s.description}" for s in candidates.get(i, []))
        blocks.append(f"Issue {i} (¶{issue.paragraph}): {issue.issue}\nCandidates:\n{cands}")
    return _system(base, block), block + "\n\n".join(blocks)

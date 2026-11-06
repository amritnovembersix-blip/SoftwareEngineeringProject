"""Split a submission into the segments the band reviewers cite ([S1], [S2], ...)."""
import re
from typing import Literal

from agentic.lms.base import Assignment, Submission
from agentic.review.nodes import html_to_paragraphs


# a sentence ends at . ! or ? (and any closing quote) followed by space and a capital letter or an opening quote
_STOP = re.compile(r"[.!?][\"')\]”’]*\s+(?=[\"'(“‘]?[A-Z])")
_ABBREVIATIONS = {"mr.", "mrs.", "ms.", "dr.", "prof.", "jr.", "sr.", "st.", "vs.", "e.g.", "i.e."}


def split_sentences(text: str) -> list[str]:
    """Split one paragraph into sentences (no new dependency; the original text is kept)."""
    out, start = [], 0
    for m in _STOP.finditer(text):
        word = text[start:m.start() + 1].split()[-1].lower()
        if word in _ABBREVIATIONS or re.fullmatch(r"[a-z]\.", word):  # Mr. Smith, J. K. Rowling
            continue
        out.append(text[start:m.end()].strip())
        start = m.end()
    out.append(text[start:].strip())
    return [t for t in out if t]


def segment(submission: Submission, assignment: Assignment
            ) -> tuple[list[str], Literal["paragraph", "sentence", "answer"]]:
    """Essay or paragraph: its paragraphs, or its sentences when there is only one paragraph (so a
    citation can point at something). Short answer: one segment per answer, with the item's prompt as
    context."""
    if assignment.submission_type != "short_answer":
        paragraphs = html_to_paragraphs(submission.body)
        if len(paragraphs) == 1:
            return split_sentences(paragraphs[0]), "sentence"
        return paragraphs, "paragraph"
    items = {it.get("id"): it for it in assignment.items}
    out = []
    for a in submission.answers:
        if "text" not in a:
            continue
        item = items.get(a.get("item_id"), {})
        prompt = item.get("prompt") or item.get("stem") or ""
        out.append(f"{a.get('item_id')}: {prompt} — answer: {a['text']}")
    return out, "answer"

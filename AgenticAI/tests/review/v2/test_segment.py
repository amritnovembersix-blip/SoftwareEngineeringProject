"""segment(): paragraphs, sentences for one-paragraph work, one per answer for short answer."""
from datetime import datetime

from agentic.lms.base import Assignment, Submission
from agentic.review.v2.segment import segment


def _sub(body="", answers=None) -> Submission:
    return Submission(id="s", user_id="u", assignment_id="a", attempt=1, body=body,
                      submitted_at=datetime(2026, 1, 1), answers=answers or [])


def _asg(kind="paragraph", **kw) -> Assignment:
    return Assignment(id="a", name="A", submission_type=kind, **kw)


def test_one_paragraph_splits_into_sentences():
    body = "<p>The fox ran. It found a key! Did it open the door? Yes, it did.</p>"
    segs, level = segment(_sub(body), _asg())
    assert level == "sentence"
    assert segs == ["The fox ran.", "It found a key!", "Did it open the door?", "Yes, it did."]


def test_one_paragraph_essay_also_splits():
    segs, level = segment(_sub("One idea. Another idea."), _asg("essay"))
    assert (segs, level) == (["One idea.", "Another idea."], "sentence")


def test_abbreviations_do_not_split():
    body = "Mr. Smith and Dr. Jones met Mrs. Lee, e.g. at noon, i.e. late. They left."
    segs, level = segment(_sub(body), _asg())
    assert segs == ["Mr. Smith and Dr. Jones met Mrs. Lee, e.g. at noon, i.e. late.", "They left."]
    assert level == "sentence"


def test_a_quote_after_the_stop_starts_a_new_sentence():
    segs, _ = segment(_sub('He said no. "Why not?" she asked.'), _asg())
    assert segs == ['He said no.', '"Why not?" she asked.']


def test_a_lowercase_after_the_stop_does_not_split():
    segs, _ = segment(_sub("It cost 3.5 dollars. then it rose."), _asg())
    assert segs == ["It cost 3.5 dollars. then it rose."]


def test_one_sentence_paragraph_is_one_segment():
    segs, level = segment(_sub("<p>Just one sentence here.</p>"), _asg())
    assert (segs, level) == (["Just one sentence here."], "sentence")


def test_multi_paragraph_essay_keeps_paragraphs():
    body = "<p>First one. Two sentences.</p><p>Second paragraph.</p>"
    segs, level = segment(_sub(body), _asg("essay"))
    assert (segs, level) == (["First one. Two sentences.", "Second paragraph."], "paragraph")


def test_no_empty_segments_and_text_is_kept():
    segs, _ = segment(_sub("  Hello there.   Next one.  "), _asg())
    assert segs == ["Hello there.", "Next one."] and all(segs)


def test_empty_body_has_no_segments():
    assert segment(_sub(""), _asg())[0] == []


def test_short_answer_is_unchanged():
    a = _asg("short_answer", items=[{"id": "q1", "prompt": "Why? Because. Really."}])
    sub = _sub(answers=[{"item_id": "q1", "text": "It is. Truly. Yes."}])
    assert segment(sub, a) == (["q1: Why? Because. Really. — answer: It is. Truly. Yes."], "answer")

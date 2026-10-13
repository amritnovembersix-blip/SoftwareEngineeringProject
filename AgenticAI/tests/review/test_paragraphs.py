from agentic.review.nodes import html_to_paragraphs


def test_html_to_paragraphs_messy_html():
    body = "<p>&nbsp;</p><p>One <em>two</em>&amp; three</p><br><p> Four\n five </p>"
    assert html_to_paragraphs(body) == ["One two & three", "Four five"]
    assert html_to_paragraphs("A.\n\nB.") == ["A.", "B."]

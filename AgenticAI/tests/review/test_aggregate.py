from agentic.models import Observation, StandardRef, word_count
from agentic.review.nodes import aggregate


def std(code, concept="Concept " + "x"):
    return StandardRef(code=code, concept=concept, description="d " + code, grade_min=9, grade_max=10)


def test_aggregate_groups_and_template_summary():
    stds = [std("A", "Thesis"), std("B", "Evidence")]
    obs = [
        Observation(standard_code="B", paragraph=4, reason="r1"),
        Observation(standard_code="A", paragraph=4, reason="r2"),
        Observation(standard_code="B", paragraph=2, reason="r3"),
        Observation(standard_code="B", paragraph=2, reason="r4"),
    ]
    findings, _ = aggregate(obs, stds)
    assert [f.standard_code for f in findings] == ["A", "B"]
    b = findings[1]
    assert b.paragraphs == [2, 4]
    assert b.reasons == ["r1", "r3", "r4"]
    assert b.observation_count == 3
    assert b.summary == "Evidence: weakness noted in ¶2, ¶4."
    assert b.concept == "Evidence" and b.description == "d B"


def test_aggregate_met_standards_are_the_rest():
    stds = [std("A", "Thesis"), std("B", "Evidence"), std("C", "Tone")]
    obs = [Observation(standard_code="B", paragraph=1, reason="r")]
    findings, met = aggregate(obs, stds)
    assert [f.standard_code for f in findings] == ["B"]
    assert [m.standard_code for m in met] == ["A", "C"]
    assert met[0].concept == "Thesis"
    assert met[0].p_met is None and met[0].possible_miss is False


def test_summary_within_40_words():
    long_concept = " ".join(["word"] * 60)
    findings, _ = aggregate(
        [Observation(standard_code="A", paragraph=1, reason="r")], [std("A", long_concept)]
    )
    assert word_count(findings[0].summary) <= 40
    assert findings[0].summary.startswith("A:")


def test_finding_severity_is_most_severe_and_met_codes_limit_met():
    from agentic.models import Observation, StandardRef
    from agentic.review.nodes import aggregate

    stds = [StandardRef(code=c, concept=c, description=c, grade_min=9, grade_max=10) for c in ("A", "B", "C")]
    obs = [Observation(standard_code="A", paragraph=1, reason="r", severity="approaching"),
           Observation(standard_code="A", paragraph=2, reason="r", severity="beginning")]
    findings, met = aggregate(obs, stds, met_codes=["B"])
    assert findings[0].severity == "beginning"
    assert [m.standard_code for m in met] == ["B"]  # C was not_evident: neither finding nor met

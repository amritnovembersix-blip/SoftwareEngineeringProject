from agentic.models import word_count


def test_word_count():
    assert word_count("  a\tb\nc  ") == 3
    assert word_count(" \t\n") == 0
    assert word_count("one two three four five six seven eight nine ten eleven twelve\n") == 12


import pytest
from pydantic import ValidationError

from agentic.models import (Finding, FindingLink, Observation, ReviewResult, SubmissionRef, Verdict,
                            Verification)


def test_growth_area_requires_support_level():
    with pytest.raises(ValidationError):
        Verdict(standard_code="3.L.1.A.a", verdict="growth_area", reviewer="3-5-language")
    ok = Verdict(standard_code="3.L.1.A.a", verdict="growth_area", support_level="developing",
                 reviewer="3-5-language")
    assert ok.support_level == "developing"


def test_support_level_only_with_growth_area():
    with pytest.raises(ValidationError):
        Verdict(standard_code="3.L.1.A.a", verdict="meets", support_level="beginning", reviewer="r")
    assert Verdict(standard_code="3.L.1.A.a", verdict="not_evident", reviewer="r").support_level is None


def test_strength_note_rules():
    note40 = " ".join(["word"] * 40)
    v = Verdict(standard_code="x", verdict="meets", notable_strength=True, strength_note=note40, reviewer="r")
    assert v.strength_note == note40
    with pytest.raises(ValidationError):
        Verdict(standard_code="x", verdict="meets", notable_strength=True, strength_note=note40 + " more",
                reviewer="r")
    with pytest.raises(ValidationError):  # a note needs notable_strength
        Verdict(standard_code="x", verdict="meets", strength_note="fine", reviewer="r")
    with pytest.raises(ValidationError):  # empty note
        Verdict(standard_code="x", verdict="meets", notable_strength=True, strength_note="  ", reviewer="r")
    with pytest.raises(ValidationError):  # V7: a strength needs its note
        Verdict(standard_code="x", verdict="meets", notable_strength=True, reviewer="r")


def test_finding_link_rationale_limit():
    FindingLink(finding_code="a", root_code="b", rationale="one two three")
    with pytest.raises(ValidationError):
        FindingLink(finding_code="a", root_code="b", rationale=" ".join(["w"] * 41))
    with pytest.raises(ValidationError):
        FindingLink(finding_code="a", root_code="b", rationale="")


def test_mvp_models_backward_compatible():
    o = Observation(standard_code="a", paragraph=1, reason="r")
    assert o.level == "paragraph"
    f = Finding(standard_code="a", concept="c", description="d", paragraphs=[1], reasons=["r"],
                observation_count=1, summary="s")
    assert f.support_level is None and f.pattern is None and f.skill_state is None
    res = ReviewResult(
        submission_ref=SubmissionRef(course_id="c", assignment_id="a", user_id="u", submission_id="s", attempt=1),
        course_grade=9, strategy="full", standards=[], observations=[], findings=[], met_standards=[],
        dropped=[], verification=Verification(status="disabled"))
    assert res.pipeline == "mvp" and res.submission_type == "essay"
    assert res.verdicts == [] and res.links == [] and res.usage == {} and res.prompt_versions == {}
    assert res.models == {}
    assert res.settings_hash is None and res.tuning_hash is None

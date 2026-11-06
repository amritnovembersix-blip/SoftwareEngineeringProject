from datetime import datetime, timedelta

from agentic.analytics.models import VerdictRow
from agentic.analytics.timeline import build_timeline, concept_code

T0 = datetime(2026, 9, 1, 12, 0)


def row(standard="3.L.1.A.a", verdict="meets", level=None, sub="s1", day=0, student="u1",
        overruled=False, strength=False, conf=None, cond=None):
    return VerdictRow(
        student_id=student, classroom_id="c1", submission_id=sub,
        due_at=T0 + timedelta(days=day), submitted_at=T0 + timedelta(days=day, hours=1),
        submission_type="essay", standard_code=standard, verdict=verdict, support_level=level,
        notable_strength=strength, confidence=conf, finding_condition=cond, overruled=overruled)


def test_concept_code():
    assert concept_code("3.L.1.A.e") == "3.L.1.A"
    assert concept_code("9-10.RL.1.A") == "9-10.RL.1.A"


def test_meets_plus_developing_is_two():
    t = build_timeline([row(verdict="meets"), row("3.L.1.A.b", "growth_area", "developing")])
    assert [p.level for p in t[("u1", "3.L.1.A")]] == [2]


def test_all_meets_is_zero():
    t = build_timeline([row(), row("3.L.1.A.b")])
    assert t[("u1", "3.L.1.A")][0].level == 0


def test_only_not_evident_gives_no_point():
    assert build_timeline([row(verdict="not_evident")]) == {}


def test_overruled_beginning_plus_meets_is_zero():
    t = build_timeline([row(), row("3.L.1.A.b", "growth_area", "beginning", overruled=True, conf=0.2, cond="x")])
    p = t[("u1", "3.L.1.A")][0]
    assert p.level == 0 and p.confidence is None and p.conditions == ()


def test_two_standards_same_concept_one_point_max_level_min_conf():
    t = build_timeline([
        row("3.L.1.A.a", "growth_area", "approaching", conf=0.9, cond="b"),
        row("3.L.1.A.b", "growth_area", "beginning", conf=0.6, cond="a", strength=True),
        row("3.L.1.A.c", "growth_area", "beginning", conf=0.7, cond="a"),
    ])
    pts = t[("u1", "3.L.1.A")]
    assert len(pts) == 1
    p = pts[0]
    assert (p.level, p.confidence, p.strength, p.conditions) == (3, 0.6, True, ("a", "b"))


def test_ordering_by_due_date():
    t = build_timeline([row(sub="late", day=5), row(sub="early", day=1), row(sub="mid", day=3)])
    assert [p.submission_id for p in t[("u1", "3.L.1.A")]] == ["early", "mid", "late"]


def test_separate_students_and_concepts():
    t = build_timeline([row(student="u2"), row("3.W.1.A.a"), row()])
    assert set(t) == {("u1", "3.L.1.A"), ("u2", "3.L.1.A"), ("u1", "3.W.1.A")}


def test_implied_meets_are_not_evidence():
    """Meeting grade 8 implies grades 6-7, but nothing was observed there, so no point is recorded."""
    base = row("6.W.3.A.a", "meets")
    implied = VerdictRow(**{**base.__dict__, "implied": True})
    assert build_timeline([implied]) == {}
    assert [p.level for p in build_timeline([implied, row("6.W.3.A.b", "meets")])[("u1", "6.W.3.A")]] == [0]

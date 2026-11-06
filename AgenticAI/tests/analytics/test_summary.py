import random
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from agentic.analytics.models import EvidencePoint, LinkRow
from agentic.analytics.status import AnalyticsSettings, ConceptStatus, concept_status
from agentic.analytics.summary import (ClassWeek, Gap, PairSuggestion, ReteachGroup,
                                       common_gaps, reteach_groups, suggest_pairs,
                                       week_start, weekly_distribution)

CHI = ZoneInfo("America/Chicago")
S = AnalyticsSettings(attention_hits=2, attention_window=3, secure_hits=3, secure_window=4,
                      trend_window=3, review_flip_rate=0.10, group_min_size=2, max_tutees=2)
C = "9-10.W.3.A"


def pt(student, concept, sub, due, level, strength=False, conditions=()):
    return EvidencePoint(student, "c1", concept, sub, due, level, strength, None, "essay",
                         tuple(conditions))


def st(student, concept, status, level=2, classroom="c1"):
    return ConceptStatus(student, classroom, concept, status, level, "2/3", (level,), "steady")


def test_week_start_boundaries_in_chicago():
    # Sunday 23:59 Chicago is Monday 04:59 UTC (CDT): still the week of the prior Monday
    sunday = datetime(2026, 9, 6, 23, 59, tzinfo=CHI)
    assert week_start(sunday) == date(2026, 8, 31)
    assert week_start(sunday.astimezone(timezone.utc)) == date(2026, 8, 31)
    monday = datetime(2026, 9, 7, 0, 0, tzinfo=CHI)
    assert week_start(monday) == date(2026, 9, 7)


def test_week_start_naive_rejected():
    with pytest.raises(ValueError):
        week_start(datetime(2026, 9, 6, 12, 0))


def test_weekly_distribution_counts_and_skips():
    d1 = datetime(2026, 9, 1, 12, tzinfo=CHI)   # week of Aug 31
    d2 = d1 + timedelta(days=7)
    d3 = d1 + timedelta(days=14)
    tl = {
        ("a", C): [pt("a", C, "a1", d1, 3), pt("a", C, "a2", d2, 3), pt("a", C, "a3", d3, 0)],
        # b first appears in week 2: skipped in week 1
        ("b", C): [pt("b", C, "b1", d2, 0)],
    }
    out = weekly_distribution(tl, S, "c1")
    assert [w.week_start for w in out] == [date(2026, 8, 31), date(2026, 9, 7), date(2026, 9, 14)]
    w1, w2, w3 = out
    assert w1.counts == (0, 0, 0, 0) and w1.too_little == 1   # a has one point
    assert w2.counts == (0, 0, 0, 1) and w2.too_little == 1   # a at level 3; b too little
    assert w3.too_little == 1 and w3.counts == (0, 0, 0, 1)  # a: [3,3,0] median up 3; b still one point
    assert all(isinstance(w, ClassWeek) and w.classroom_id == "c1" and w.concept_code == C
               for w in out)


def test_weekly_distribution_uses_only_points_due_by_week_end():
    # due Sunday 23:59 Chicago stays in its week; Monday 00:00 belongs to the next week
    sun = datetime(2026, 9, 6, 23, 59, tzinfo=CHI)
    mon = datetime(2026, 9, 7, 0, 0, tzinfo=CHI)
    tl = {("a", C): [pt("a", C, "a1", sun, 2), pt("a", C, "a2", mon, 2)]}
    w1, w2 = weekly_distribution(tl, S, "c1")
    assert w1.week_start == date(2026, 8, 31) and w1.too_little == 1
    assert w2.week_start == date(2026, 9, 7) and w2.too_little == 0 and w2.counts[2] == 1


def test_weekly_distribution_concept_starts_at_first_point():
    d1 = datetime(2026, 9, 1, 12, tzinfo=CHI)
    d2 = d1 + timedelta(days=7)
    tl = {("a", "3.L.1.A"): [pt("a", "3.L.1.A", "a1", d1, 1)],
          ("a", C): [pt("a", C, "a2", d2, 1)]}
    out = weekly_distribution(tl, S, "c1")
    assert [(w.concept_code, w.week_start) for w in out] == [
        ("3.L.1.A", date(2026, 8, 31)), ("3.L.1.A", date(2026, 9, 7)), (C, date(2026, 9, 7))]


def test_weekly_distribution_empty():
    assert weekly_distribution({}, S, "c1") == []


def test_common_gaps_order_and_fields():
    d = datetime(2026, 9, 1, tzinfo=CHI)
    tl = {("a", "3.L.1.A"): [pt("a", "3.L.1.A", "s1", d, 2, conditions=("run-on",))],
          ("b", "3.L.1.A"): [pt("b", "3.L.1.A", "s1", d, 3, conditions=("run-on", "tense"))],
          ("c", C): [pt("c", C, "s1", d, 2, conditions=("thesis",))],
          ("d", C): [pt("d", C, "s1", d, 1, conditions=("zzz",)),
                     pt("d", C, "s2", d + timedelta(days=1), 0, conditions=())]}
    statuses = [st("b", "3.L.1.A", "needs_attention", 3), st("a", "3.L.1.A", "needs_attention", 2),
                st("c", C, "needs_attention", 2), st("d", C, "secure", 0)]
    gaps = common_gaps(statuses, tl)
    assert gaps == [Gap("3.L.1.A", ("a", "b"), (2, 3), "run-on"),
                    Gap(C, ("c",), (2,), "thesis")]


def test_common_gaps_no_condition_and_tie_alphabetical():
    d = datetime(2026, 9, 1, tzinfo=CHI)
    tl = {("a", "3.L.1.A"): [pt("a", "3.L.1.A", "s1", d, 2, conditions=("beta",))],
          ("b", "3.L.1.A"): [pt("b", "3.L.1.A", "s1", d, 2, conditions=("alpha",))],
          ("c", C): [pt("c", C, "s1", d, 2)]}
    statuses = [st("a", "3.L.1.A", "needs_attention"), st("b", "3.L.1.A", "needs_attention"),
                st("c", C, "needs_attention")]
    gaps = {g.concept_code: g for g in common_gaps(statuses, tl)}
    assert gaps["3.L.1.A"].top_condition == "alpha"
    assert gaps[C].top_condition is None


def _timeline_for(students, concept=C, sub="s1"):
    d = datetime(2026, 9, 1, tzinfo=CHI)
    return {(x, concept): [pt(x, concept, sub, d, 2)] for x in students}


def test_reteach_groups_shared_root_and_single_dropped():
    tl = _timeline_for(["a", "b", "c"])
    statuses = [st(x, C, "needs_attention") for x in "abc"]
    links = [LinkRow("a", "s1", C, "3.L.1.A"), LinkRow("b", "s1", C, "3.L.1.A"),
             LinkRow("b", "s1", C, "5.L.1.A"), LinkRow("c", "s1", C, "5.L.1.A")]
    assert reteach_groups(statuses, links, S, tl) == [ReteachGroup("3.L.1.A", C, ("a", "b"))]


def test_reteach_groups_no_link_groups_by_own_concept():
    tl = _timeline_for(["a", "b"])
    statuses = [st("b", C, "needs_attention"), st("a", C, "needs_attention")]
    assert reteach_groups(statuses, [], S, tl) == [ReteachGroup(C, C, ("a", "b"))]


def test_reteach_groups_only_window_submissions_and_matching_concept():
    d = datetime(2026, 9, 1, tzinfo=CHI)
    tl = {("a", C): [pt("a", C, f"s{i}", d + timedelta(days=i), 2) for i in range(5)],
          ("b", C): [pt("b", C, "s4", d, 2)]}
    statuses = [st("a", C, "needs_attention"), st("b", C, "needs_attention"),
                st("c", C, "secure")]
    links = [LinkRow("a", "s0", C, "3.L.1.A"),      # outside the window of 3: ignored
             LinkRow("a", "s4", C, "5.L.1.A"),
             LinkRow("a", "s4", "9-10.L.1.A", "3.L.1.A"),   # other grade concept: ignored
             LinkRow("b", "s4", C, "5.L.1.A"),
             LinkRow("c", "s4", C, "5.L.1.A")]      # c is not needs_attention
    assert reteach_groups(statuses, links, S, tl) == [ReteachGroup("5.L.1.A", C, ("a", "b"))]


def test_reteach_root_tie_broken_by_concept_code():
    tl = _timeline_for(["a", "b"])
    statuses = [st("a", C, "needs_attention"), st("b", C, "needs_attention")]
    links = [LinkRow("a", "s1", C, "4.L.1.B"), LinkRow("a", "s1", C, "4.L.1.A"),
             LinkRow("b", "s1", C, "4.L.1.A")]
    assert reteach_groups(statuses, links, S, tl)[0].root_concept == "4.L.1.A"


def test_suggest_pairs_capacity_and_no_self():
    statuses = [st("t1", C, "secure", 0)] + [st(x, C, "needs_attention") for x in ("n3", "n1", "n2")]
    assert suggest_pairs(statuses, S) == [PairSuggestion(C, "t1", "n1"),
                                          PairSuggestion(C, "t1", "n2")]


def test_suggest_pairs_multiple_tutors_spill_over():
    statuses = ([st(x, C, "secure", 0) for x in ("t2", "t1")]
                + [st(x, C, "needs_attention") for x in ("n1", "n2", "n3")])
    assert [(p.tutor_id, p.tutee_id) for p in suggest_pairs(statuses, S)] == [
        ("t1", "n1"), ("t1", "n2"), ("t2", "n3")]


def test_suggest_pairs_per_concept():
    statuses = [st("t1", C, "secure", 0), st("n1", "3.L.1.A", "needs_attention")]
    assert suggest_pairs(statuses, S) == []


def test_deterministic_under_shuffling():
    d = datetime(2026, 9, 1, tzinfo=CHI)
    students = list("abcdefgh")
    tl, statuses, links = {}, [], []
    for i, x in enumerate(students):
        pts_ = [pt(x, C, f"{x}{j}", d + timedelta(days=7 * j), 2 if i % 2 else 0,
                   strength=True, conditions=("run-on",)) for j in range(4)]
        tl[(x, C)] = pts_
        statuses.append(concept_status(pts_, S))
        links.append(LinkRow(x, f"{x}3", C, "3.L.1.A" if i % 4 else "4.L.1.A"))
    base = (weekly_distribution(tl, S, "c1"), common_gaps(statuses, tl),
            reteach_groups(statuses, links, S, tl), suggest_pairs(statuses, S))
    assert base[1] and base[2] and base[3]
    rng = random.Random(7)
    for _ in range(5):
        items = list(tl.items())
        rng.shuffle(items)
        sts = statuses[:]
        rng.shuffle(sts)
        lks = links[:]
        rng.shuffle(lks)
        again = (weekly_distribution(dict(items), S, "c1"), common_gaps(sts, dict(items)),
                 reteach_groups(sts, lks, S, dict(items)), suggest_pairs(sts, S))
        assert again == base

from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from agentic.analytics.models import EvidencePoint
from agentic.analytics.status import (AnalyticsSettings, concept_status, from_effective,
                                      noise_run_length)

T0 = datetime(2026, 9, 1, 12, 0)
S = AnalyticsSettings(attention_hits=2, attention_window=3, secure_hits=3, secure_window=4,
                      trend_window=3, review_flip_rate=0.10, group_min_size=2, max_tutees=2)


def pts(levels, strengths=()):
    return [EvidencePoint("u1", "c1", "3.L.1.A", f"s{i}", T0 + timedelta(days=i), lv,
                          i in strengths, None, "essay", ()) for i, lv in enumerate(levels)]


def st(levels, s=S, strengths=()):
    return concept_status(pts(levels, strengths), s)


def test_from_effective():
    s = from_effective({"attention_hits": 2, "attention_window": 3, "secure_hits": 3,
                        "secure_window": 4, "trend_window": 3, "review_flip_rate": 0.1,
                        "group_min_size": 2, "max_tutees": 2})
    assert s == S and isinstance(s.review_flip_rate, float)


def test_noise_run_length():
    assert noise_run_length(0.10) == 2
    assert noise_run_length(0.30) == 3


def test_zero_points_is_none():
    assert concept_status([], S) is None


def test_one_point_too_little():
    r = st([0])
    assert r.status == "too_little_evidence" and r.trend == "unclear" and r.basis == "0/1"


def test_one_developing_point_default_is_too_little():
    assert st([2]).status == "too_little_evidence"


def test_one_point_attention_hits_one_is_needs_attention():
    assert st([2], replace(S, attention_hits=1)).status == "needs_attention"


def test_needs_attention_boundary():
    r = st([0, 2, 2])
    assert r.status == "needs_attention" and r.basis == "2/3"
    assert st([2, 0, 0, 2]).status != "needs_attention"  # only last 3 count: 0,0,2


def test_secure_needs_strength():
    assert st([0, 0, 0, 0]).status == "emerging"
    r = st([0, 0, 0, 0], strengths=(2,))
    assert r.status == "secure" and r.basis == "4/4"


def test_secure_boundary():
    assert st([1, 0, 0, 0], strengths=(1,)).status == "secure"
    assert st([1, 1, 0, 0], strengths=(1,)).status == "emerging"


def test_secure_window_longer_than_points():
    r = st([0, 0, 0], strengths=(0,))
    assert r.status == "secure" and r.basis == "3/3"


def test_emerging_basis():
    r = st([1, 1])
    assert r.status == "emerging" and r.basis == "0/2" and r.points == (1, 1)


def test_current_level_median_upper_middle():
    assert st([3, 0]).current_level == 3
    assert st([0, 2, 3]).current_level == 2
    assert st([1, 1, 3, 0]).current_level == 1  # last 3: 1,3,0 -> median 1


def test_trend_unclear_short():
    assert st([3, 2, 1]).trend == "unclear"


def test_trend_run_rule():
    assert st([3, 3, 3, 2]).trend == "steady"
    assert st([3, 3, 3, 2, 1]).trend == "improving"
    assert st([0, 0, 0, 1, 2]).trend == "declining"


def test_trend_equal_means_steady():
    assert st([2, 2, 2, 2]).trend == "steady"


def test_trend_run_must_agree_with_mean():
    # mean rose (last3 [3,2,1]=2 vs before [0,0,3]=1) but the trailing run is a 2-step fall
    assert st([0, 0, 3, 3, 2, 1]).trend == "steady"


def test_one_zero_point_with_strength_and_secure_hits_one_is_secure():
    assert st([0], replace(S, secure_hits=1), strengths=(0,)).status == "secure"


def test_higher_flip_rate_needs_longer_run():
    s = replace(S, review_flip_rate=0.30)
    assert st([3, 3, 3, 2, 1], s).trend == "steady"
    assert st([3, 3, 3, 2, 1, 0], s).trend == "improving"


def test_ids_carried():
    r = st([1, 1])
    assert (r.student_id, r.classroom_id, r.concept_code) == ("u1", "c1", "3.L.1.A")


def test_bad_flip_rate():
    with pytest.raises(ValueError):
        noise_run_length(1.0)


def test_trend_counts_plateaus_from_several_assignments_a_week():
    assert st([3, 3, 2, 2, 1, 1]).trend == "improving"
    assert st([1, 1, 2, 2, 3, 3]).trend == "declining"


def test_one_odd_point_is_not_a_trend():
    assert st([0, 0, 0, 2, 0]).trend == "steady"
    assert st([2, 2, 2, 0, 2]).trend == "steady"

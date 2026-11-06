"""Concept status, current level and trend from an evidence timeline. Pure: no I/O."""
import math
from dataclasses import dataclass
from typing import Literal

from agentic.analytics.models import EvidencePoint

Status = Literal["needs_attention", "secure", "emerging", "too_little_evidence"]
Trend = Literal["improving", "declining", "steady", "unclear"]


@dataclass(frozen=True)
class AnalyticsSettings:
    attention_hits: int
    attention_window: int
    secure_hits: int
    secure_window: int
    trend_window: int
    review_flip_rate: float
    group_min_size: int
    max_tutees: int


def from_effective(d: dict) -> AnalyticsSettings:
    return AnalyticsSettings(
        attention_hits=int(d["attention_hits"]), attention_window=int(d["attention_window"]),
        secure_hits=int(d["secure_hits"]), secure_window=int(d["secure_window"]),
        trend_window=int(d["trend_window"]), review_flip_rate=float(d["review_flip_rate"]),
        group_min_size=int(d["group_min_size"]), max_tutees=int(d["max_tutees"]))


@dataclass(frozen=True)
class ConceptStatus:
    student_id: str
    classroom_id: str
    concept_code: str
    status: Status
    current_level: int
    basis: str  # a count string such as "2/3"; never a percentage
    points: tuple[int, ...]
    trend: Trend


def noise_run_length(flip_rate: float) -> int:
    """Recent points that must agree before a change is unlikely (p < 0.05) to be the review changing
    its mind: flip_rate ** n < 0.05."""
    if not 0 < flip_rate < 1:
        raise ValueError("flip_rate must be between 0 and 1 (exclusive)")
    return math.ceil(math.log(0.05) / math.log(flip_rate))


def _median_up(levels: list[int]) -> int:
    return sorted(levels)[len(levels) // 2]  # even count: the upper middle (more support)


def _trend(levels: list[int], s: AnalyticsSettings) -> Trend:
    w = s.trend_window
    if len(levels) < w + 1:
        return "unclear"
    last = levels[-w:]
    before = levels[-2 * w:-w]
    delta = sum(last) / len(last) - sum(before) / len(before)
    if delta == 0:
        return "steady"
    # the last `need` points must each beat the earlier average: a plateau (3, 3, 2, 2, 1, 1) still counts
    baseline = sum(before) / len(before)
    recent = levels[-noise_run_length(s.review_flip_rate):]
    if delta < 0 and all(x < baseline for x in recent):
        return "improving"
    if delta > 0 and all(x > baseline for x in recent):
        return "declining"
    return "steady"


def concept_status(points: list[EvidencePoint], s: AnalyticsSettings) -> ConceptStatus | None:
    if not points:
        return None
    levels = [p.level for p in points]
    first = points[0]
    attn = points[-s.attention_window:]
    attn_hits = sum(1 for p in attn if p.level >= 2)
    sec = points[-s.secure_window:]
    sec_zeros = sum(1 for p in sec if p.level == 0)
    if attn_hits >= s.attention_hits:
        status: Status = "needs_attention"
        basis = f"{attn_hits}/{len(attn)}"
    elif sec_zeros >= s.secure_hits and any(p.strength for p in sec):
        status = "secure"
        basis = f"{sec_zeros}/{len(sec)}"
    else:
        status = "too_little_evidence" if len(points) < 2 else "emerging"
        basis = f"{attn_hits}/{len(attn)}"
    return ConceptStatus(
        student_id=first.student_id, classroom_id=first.classroom_id,
        concept_code=first.concept_code, status=status,
        current_level=_median_up([p.level for p in attn]), basis=basis,
        points=tuple(levels), trend=_trend(levels, s))

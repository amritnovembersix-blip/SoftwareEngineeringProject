"""Tuning config: band profiles, conditions, pricing and prompt files (agentic/config, agentic/prompts)."""
import hashlib
import json
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from agentic.llm.base import check_reasoning

# Catalog bands above grade 8 span two grades; a course is reviewed up to the top of its band.
_BAND_TOP = {9: 10, 10: 10, 11: 12, 12: 12}


@dataclass(frozen=True)
class ReviewerSpec:
    id: str
    grades: tuple[int, int | Literal["course"]]
    strands: tuple[str, ...]
    mode: Literal["per_standard", "progression", "search"]
    prompt: str  # "<name>.v<N>"
    model: str | None = None
    reasoning: str | None = None  # default | low | medium | high; None = the global LLM_REASONING


@dataclass(frozen=True)
class Profile:
    name: str
    reviewers: list[ReviewerSpec] = field(default_factory=list)


_STRANDS = {"L", "W", "R", "RL", "RI"}


def _read_toml(path: Path) -> dict:
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        raise ValueError(f"{Path(path).name}: not valid TOML ({e})") from None


def _is_number(v: object) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _reviewer(where: str, r: dict) -> ReviewerSpec:
    """`where` is e.g. "bands.toml profiles.full"; every error names it and the reviewer."""
    for key in ("id", "grades", "strands", "mode", "prompt"):
        if key not in r:
            raise ValueError(f"{where}: missing key {key!r}")
    at = f"{where}.{r['id']}"
    grades = r["grades"]
    if not (isinstance(grades, list) and len(grades) == 2):
        raise ValueError(f"{at}: grades must be [first, last], got {grades!r}")
    lo, hi = grades
    if not isinstance(lo, int) or isinstance(lo, bool) or not (
            hi == "course" or (isinstance(hi, int) and not isinstance(hi, bool))):
        raise ValueError(f"{at}: grades must be [int, int or \"course\"], got {grades!r}")
    if hi != "course" and lo > hi:
        raise ValueError(f"{at}: grades first {lo} is above last {hi}")
    strands = r["strands"]
    if not (isinstance(strands, list) and strands):
        raise ValueError(f"{at}: strands must be a non-empty list")
    unknown = [x for x in strands if x not in _STRANDS]
    if unknown:
        raise ValueError(f"{at}: unknown strands {unknown}; choose from {sorted(_STRANDS)}")
    if r["mode"] not in ("per_standard", "progression", "search"):
        raise ValueError(f"{at}: mode must be per_standard, progression or search, got {r['mode']!r}")
    if not isinstance(r["prompt"], str):
        raise ValueError(f"{at}: prompt must be a string like \"per_standard.v1\"")
    reasoning = r.get("reasoning")
    if reasoning is not None:
        check_reasoning(reasoning, at)
    return ReviewerSpec(id=r["id"], grades=(lo, hi), strands=tuple(strands), mode=r["mode"],
                        prompt=r["prompt"], model=r.get("model") or None, reasoning=reasoning)


def load_profile(config_dir: Path, name: str) -> Profile:
    profiles = _read_toml(Path(config_dir) / "bands.toml").get("profiles", {})
    if name not in profiles:
        raise ValueError(f"bands.toml: unknown profile {name!r}; choose one of {', '.join(profiles)}")
    where = f"bands.toml profiles.{name}"
    return Profile(name=name, reviewers=[_reviewer(where, r) for r in profiles[name].get("reviewers", [])])


def load_conditions(config_dir: Path) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for k, v in _read_toml(Path(config_dir) / "conditions.toml").items():
        if not (isinstance(v, list) and v and all(isinstance(x, str) for x in v)):
            raise ValueError(f"conditions.toml {k!r}: must be a non-empty list of strings")
        out[k] = list(v)
    return out


def load_pricing(config_dir: Path) -> dict[str, tuple[float, float]]:
    out: dict[str, tuple[float, float]] = {}
    for k, v in _read_toml(Path(config_dir) / "pricing.toml").items():
        if not (isinstance(v, list) and len(v) == 2 and all(_is_number(x) for x in v)):
            raise ValueError(f"pricing.toml {k!r}: must be [input, output] USD per 1M tokens")
        out[k] = (float(v[0]), float(v[1]))
    return out


def load_prompt(prompts_dir: Path, ref: str) -> str:
    path = Path(prompts_dir) / f"{ref}.md"
    if not path.exists():
        raise FileNotFoundError(f"prompt file not found for {ref!r}: {path}")
    return path.read_text(encoding="utf-8")


def check_mode_prompt(reviewer: str, mode: str, prompt: str) -> None:
    """Ruling S1-5: a reviewer's prompt must fit its reply shape. Mode search needs progression.v3 or
    later (one step per group); mode progression needs progression.v1 or v2 (every step at once)."""
    m = re.fullmatch(r"(.+)\.v(\d+)", prompt)
    name, version = (m.group(1), int(m.group(2))) if m else (prompt, 0)
    if mode == "search" and not (name == "progression" and version >= 3):
        raise ValueError(f"reviewer {reviewer!r}: mode search needs prompt progression.v3 or later, got {prompt!r}")
    if mode == "progression" and not (name == "progression" and version in (1, 2)):
        raise ValueError(f"reviewer {reviewer!r}: mode progression needs prompt progression.v1 or "
                         f"progression.v2, got {prompt!r}")


def validate_tuning(config_dir: Path, prompts_dir: Path, profile_name: str) -> Profile:
    """Fail fast at startup: load the profile, conditions and pricing, check every prompt exists and
    fits its reviewer's mode."""
    profile = load_profile(config_dir, profile_name)
    load_conditions(config_dir)
    load_pricing(config_dir)
    for spec in profile.reviewers:
        check_mode_prompt(spec.id, spec.mode, spec.prompt)
        load_prompt(prompts_dir, spec.prompt)
    return profile


def tuning_hash(profile: Profile, conditions: dict[str, list[str]], prompt_texts: dict[str, str]) -> str:
    """sha256 of the canonical JSON of everything that shapes a review, to stamp on its results."""
    def reviewer(r: ReviewerSpec) -> dict:
        row = {"id": r.id, "grades": list(r.grades), "strands": r.strands, "mode": r.mode,
               "prompt": r.prompt, "model": r.model}
        if r.reasoning not in (None, "default"):  # default adds nothing: hashes from before stay valid
            row["reasoning"] = r.reasoning
        return row

    canon = {
        "profile": {"name": profile.name, "reviewers": [reviewer(r) for r in profile.reviewers]},
        "conditions": conditions,
        "prompts": prompt_texts,
    }
    return hashlib.sha256(json.dumps(canon, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def plan_reviewers(profile: Profile, course_grade: int) -> list[tuple[ReviewerSpec, int, int]]:
    """Resolve each reviewer's grade range for a course: "course" becomes the band top, reviewers
    starting above the course grade are dropped, and the 3-5 band is capped at the course grade."""
    plan: list[tuple[ReviewerSpec, int, int]] = []
    for spec in profile.reviewers:
        lo, hi = spec.grades
        if lo > course_grade:
            continue
        hi = _BAND_TOP.get(course_grade, course_grade) if hi == "course" else min(hi, course_grade)
        plan.append((spec, lo, hi))
    return plan

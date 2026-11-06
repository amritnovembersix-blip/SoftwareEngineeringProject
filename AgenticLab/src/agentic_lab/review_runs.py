"""Lab review runs: review one submission with chosen options, save the run, build display tables, diff two runs.

Pure logic for the Streamlit "Review one" and "Compare runs" tabs (no Streamlit import). A run goes through
`review_one` and persists nothing to Postgres. A saved run file is local and holds the whole result including
the trace, which may quote the student: the display tables never carry submission text or trace replies.
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pydantic import BaseModel

from agentic.config import Settings
from agentic.coursedata import CourseDir, lms_assignment_id
from agentic.dataset import read_rows
from agentic.models import ReviewResult
from agentic.review.v2.options import ReviewOptions
from agentic.review.v2.run import review_one
from agentic_lab.answer_key import ExpectedRow, answer_key_path
from agentic_lab.eval_v2 import _cost

_WORDS = {"meets": "meets", "growth_area": "growth area", "not_evident": "not evident"}


class SavedRun(BaseModel):
    saved_at: datetime
    course: str  # course version
    assignment: str  # file assignment id
    student: str  # full hash
    options: ReviewOptions
    result: ReviewResult


def run_review(s: Settings, course_version: str, assignment_file_id: str, student_hash: str,
               options: ReviewOptions) -> ReviewResult:
    """Review one student's latest attempt through `review_one`. `student_hash` may be a roster prefix."""
    s = s.model_copy(update={"lms_source": "coursedir"})
    cd = CourseDir(s.data_dir, course_version)
    user_id = cd.resolve_student(student_hash)
    cd.assignment(assignment_file_id)  # LookupError for an unknown assignment
    return review_one(s, f"classroom_{course_version}", lms_assignment_id(assignment_file_id, course_version),
                      user_id, options)


def runs_dir_for(s: Settings) -> Path:
    """`local/reviews` beside the reports folder (so REPORTS_DIR moves both)."""
    return Path(s.reports_dir).parent / "reviews"


def save_run(result: ReviewResult, options: ReviewOptions, runs_dir: Path) -> Path:
    """Write the run (trace included) to `<runs_dir>/<UTC timestamp>.json` (git-ignored under `local/`); two quick saves never collide."""
    runs_dir = Path(runs_dir)
    runs_dir.mkdir(parents=True, exist_ok=True)
    ref = result.submission_ref
    file_id, _, version = ref.assignment_id.partition("@")
    now = datetime.now(timezone.utc)
    run = SavedRun(saved_at=now, course=version, assignment=file_id, student=ref.user_id,
                   options=options, result=result)
    while True:
        path = runs_dir / f"{now.strftime('%Y%m%dT%H%M%S%fZ')}.json"
        try:
            with open(path, "x", encoding="utf-8") as f:
                f.write(run.model_dump_json(indent=2))
            return path
        except FileExistsError:
            now += timedelta(microseconds=1)


def list_runs(runs_dir: Path) -> list[Path]:
    """Saved run files, newest first."""
    return sorted(Path(runs_dir).glob("*.json"), reverse=True)


def load_run(path: Path) -> SavedRun:
    return SavedRun.model_validate_json(Path(path).read_text(encoding="utf-8"))


# ---- what the Review one tab offers ----

def course_versions(s: Settings) -> list[str]:
    root = Path(s.data_dir) / "courses"
    return sorted(p.name for p in root.iterdir() if (p / "course.json").exists()) if root.is_dir() else []


def assignment_choices(s: Settings, version: str) -> list[tuple[str, str]]:
    """(file assignment id, title) in curriculum order."""
    return [(a.id, a.title) for a in CourseDir(s.data_dir, version).assignments()]


def student_choices(s: Settings, version: str, assignment_file_id: str) -> list[tuple[str, str]]:
    """(student hash, caption) for the students who submitted; empty when the course has no submissions."""
    hashes = dict.fromkeys(r.student_hash for r in CourseDir(s.data_dir, version).submissions(assignment_file_id))
    return [(h, h[:8]) for h in hashes]


def expected_for(s: Settings, course_version: str, assignment_file_id: str, student_hash: str) -> set[str] | None:
    """The growth-area codes the answer key plants for this student's submission; None when there is no key."""
    path = answer_key_path(s.data_dir, course_version) / "expected" / f"{assignment_file_id}.jsonl"
    if not path.exists():
        return None
    user_id = CourseDir(s.data_dir, course_version).resolve_student(student_hash)
    rows = [r for r in read_rows(path, ExpectedRow) if r.student_hash == user_id]
    return {w.standard for w in rows[-1].weaknesses} if rows else set()


# ---- display tables ----

def _usage_rows(result: ReviewResult) -> list[dict]:
    traced = result.trace.get("reviewers", {})
    rows = []
    for reviewer, u in result.usage.items():
        seconds = traced.get(reviewer, {}).get("seconds")
        if seconds is None:
            seconds = result.timings.get(f"band:{reviewer}", result.timings.get(reviewer))
        rows.append({"reviewer": reviewer, "prompt": result.prompt_versions.get(reviewer),
                     "model": result.models.get(reviewer),
                     "input tokens": u.input_tokens, "output tokens": u.output_tokens,
                     "thinking tokens": u.thinking_tokens,
                     "estimated": u.estimated, "seconds": None if seconds is None else round(seconds, 2)})
    return rows


def view(result: ReviewResult, s: Settings, profile: str | None = None,
         expected: set[str] | None = None) -> dict:
    """Display tables for one run: codes, counts, levels and short model notes only.

    `profile` names the band profile the run used (default: the settings' profile), for pricing each
    reviewer's model. `expected` (the key's planted codes) adds a planted column and the missed list."""
    findings = {f.standard_code: f for f in result.findings}
    reviewers = list(dict.fromkeys(v.reviewer for v in result.verdicts))
    verdicts = [{"reviewer": r, **{_WORDS[k]: sum(1 for v in result.verdicts if v.reviewer == r and v.verdict == k)
                                   for k in _WORDS}} for r in reviewers]
    growth = []
    for v in result.verdicts:
        if v.verdict != "growth_area":
            continue
        f = findings.get(v.standard_code)
        row = {"code": v.standard_code, "reviewer": v.reviewer, "support level": v.support_level,
               "confidence": v.confidence, "condition": f.condition if f else None,
               "pattern": f.pattern if f else None, "skill state": f.skill_state if f else None,
               "segments": ", ".join(str(n) for n in v.segments), "summary": f.summary if f else None}
        if expected is not None:
            row["planted"] = "planted" if v.standard_code in expected else "not planted"
        growth.append(row)
    cost = _cost(s, [result], profile or s.bands_profile, 1)
    out = {
        "verdicts": verdicts,
        "growth_areas": growth,
        "links": [{"growth area": l.finding_code, "root": l.root_code, "rationale": l.rationale}
                  for l in result.links],
        "strengths": [{"code": v.standard_code, "note": v.strength_note, "confidence": v.confidence}
                      for v in result.verdicts if v.notable_strength],
        "usage": _usage_rows(result),
        "totals": {"calls": result.llm_calls, "input_tokens": cost.input_tokens,
                   "output_tokens": cost.output_tokens, "thinking_tokens": cost.thinking_tokens,
                   "usd": cost.usd,
                   "unpriced_models": cost.unpriced_models, "verification": result.verification.status},
    }
    if expected is not None:
        reported = {g["code"] for g in growth}
        out["missed"] = sorted(expected - reported)
    return out


# ---- compare ----

def _tokens(result: ReviewResult) -> int:
    return (sum(u.input_tokens + u.output_tokens for u in result.usage.values())
            + (result.verification.input_tokens or 0))


def _call(result: ReviewResult) -> dict[str, tuple[str, str | None]]:
    return {v.standard_code: (_WORDS[v.verdict], v.support_level) for v in result.verdicts}


def diff_runs(a: ReviewResult, a_options: ReviewOptions, b: ReviewResult, b_options: ReviewOptions) -> dict:
    """What differs between two runs: verdicts by standard, growth areas and links in only one, cost, options."""
    ca, cb = _call(a), _call(b)
    verdicts = [{"code": code, "verdict A": (ca.get(code) or (None, None))[0],
                 "support level A": (ca.get(code) or (None, None))[1],
                 "verdict B": (cb.get(code) or (None, None))[0],
                 "support level B": (cb.get(code) or (None, None))[1]}
                for code in sorted(ca.keys() | cb.keys()) if ca.get(code) != cb.get(code)]
    ga = {code for code, (v, _) in ca.items() if v == "growth area"}
    gb = {code for code, (v, _) in cb.items() if v == "growth area"}
    la = {(l.finding_code, l.root_code) for l in a.links}
    lb = {(l.finding_code, l.root_code) for l in b.links}

    def links(pairs: set) -> list[dict]:
        return [{"growth area": f, "root": r} for f, r in sorted(pairs)]

    oa, ob = a_options.model_dump(), b_options.model_dump()
    return {"verdicts": verdicts, "only_a": sorted(ga - gb), "only_b": sorted(gb - ga),
            "links_only_a": links(la - lb), "links_only_b": links(lb - la),
            "tokens": {"a": _tokens(a), "b": _tokens(b)}, "calls": {"a": a.llm_calls, "b": b.llm_calls},
            "options": {k: [oa[k], ob[k]] for k in oa if oa[k] != ob[k]}}

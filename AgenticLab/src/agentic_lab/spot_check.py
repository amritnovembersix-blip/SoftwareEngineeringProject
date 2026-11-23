"""Lab only: spot-check the reviewer's unplanted flags by hand, during or after `evaluate --course`.

`evaluate --course` appends one line per reviewed submission to a flag log: ids, codes, levels, cited segment
numbers and the reviewer's short reasons, never essay text. `build_spot_check` samples flags from that log
(spread across students and reviewers) and writes a Markdown file a person can mark. That file quotes the
synthetic essays (read from the local data files), so it stays in the git-ignored reports folder.
"""
import json
import random
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from agentic import dataset
from agentic.coursedata import CourseDir, file_assignment_id
from agentic.lms.coursedir_client import CourseDirLMSClient
from agentic.models import ReviewResult
from agentic.review.v2.segment import segment
from agentic.standards import loader

from agentic_lab.answer_key import StudentKey, answer_key_path

ANSWERS = ("Real", "Real problem, wrong standard", "False")
REMOVED_ANSWERS = ("Right to remove", "Wrong: it was a real problem here", "Unsure")
_CHANGE_KEYS = ("standard", "step", "action", "into", "reason", "segments")


class FlagLog:
    """Appends one JSON line per reviewed submission; each write is flushed, so a reader sees it mid-run."""

    def __init__(self, path: Path, version: str):
        self.path = Path(path)
        self.version = version
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def __call__(self, res: ReviewResult, file_id: str, student: str, planted: dict[str, int] | None,
                 any_of: list[dict] | None = None) -> None:
        reasons = defaultdict(list)
        for o in res.observations:
            reasons[o.standard_code].append(o.reason)
        flags = [{"standard": v.standard_code, "support_level": v.support_level, "reviewer": v.reviewer,
                  "segments": list(v.segments), "reasons": reasons.get(v.standard_code, [])}
                 for v in res.verdicts if v.verdict == "growth_area"]
        row = {"version": self.version, "assignment": file_id, "student": student,
               "submission_id": res.submission_ref.submission_id, "attempt": res.submission_ref.attempt,
               "planted": planted, "any_of": any_of or [], "flags": flags,
               "removed": [{k: c[k] for k in _CHANGE_KEYS if k in c}  # supervised: what a step took away
                           for c in res.trace.get("supervisor", {}).get("changes", [])]}
        with self.path.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(row) + "\n")


def read_log(path: Path) -> list[dict]:
    """Every complete line; a half-written last line (the run is still going) is skipped."""
    rows = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def latest_log(reports_dir: Path) -> Path:
    logs = sorted(Path(reports_dir).glob("flags-*.jsonl"))
    if not logs:
        raise FileNotFoundError(f"no flags-*.jsonl in {reports_dir}; run evaluate --course first")
    return logs[-1]


@dataclass(frozen=True)
class Pick:
    record: dict
    flag: dict


def sample(rows: list[dict], n: int, seed: int = 7, removed: bool = False) -> list[Pick]:
    """Unplanted flags (submissions with an answer key), round-robin across reviewers, and within a reviewer
    across students, so no single student or reviewer dominates. With `removed`, the flags a supervised step took
    away instead, round-robin across steps. Deterministic for a seed."""
    rng = random.Random(seed)
    by_reviewer: dict[str, list[Pick]] = defaultdict(list)
    for r in rows:
        if removed:
            for c in r.get("removed", []):
                by_reviewer[c["step"]].append(Pick(r, c))
            continue
        if r.get("planted") is None:
            continue
        for f in r["flags"]:
            if f["standard"] not in r["planted"]:
                by_reviewer[f["reviewer"]].append(Pick(r, f))
    for picks in by_reviewer.values():
        rng.shuffle(picks)
    out, used_students = [], defaultdict(int)
    reviewers = sorted(by_reviewer)
    while len(out) < n and any(by_reviewer.values()):
        for rev in reviewers:
            picks = by_reviewer[rev]
            if not picks or len(out) >= n:
                continue
            best = min(range(len(picks)), key=lambda i: used_students[picks[i].record["student"]])
            p = picks.pop(best)
            used_students[p.record["student"]] += 1
            out.append(p)
    return out


def _standards(path: Path) -> dict[str, str]:
    rows = loader.normalize_rows(loader.read_xlsx(path))
    return {r.code: (r.sub_expectation or r.description or "").strip() for r in rows}


def _profile(data_dir: Path, version: str, student: str) -> str:
    keys = dataset.read_rows(answer_key_path(data_dir, version) / "students.jsonl", StudentKey)
    return next((k.profile for k in keys if k.student_hash == student), "unknown")


def render(picks: list[Pick], data_dir: Path, standards_file: Path, log_path: Path, removed: bool = False) -> str:
    text = _standards(standards_file)
    if removed:
        out = ["# Spot check: flags the supervisor removed", "",
               f"From `{log_path.name}`. Each item is a growth area a reviewer flagged and a later step took away "
               "(the critic dropped it, or the arbiter absorbed it into another standard). Read the cited "
               "segment(s) and mark one:", "",
               "- **Right to remove:** the problem isn't there, or it's the same problem as the standard it was "
               "absorbed into.",
               "- **Wrong: it was a real problem here:** the segment shows this problem under this standard.",
               "- **Unsure.**", "",
               "Local only: this file quotes the synthetic essays. Don't commit it.", ""]
    else:
        out = ["# Spot check: flags the answer key didn't plant", "",
               f"From `{log_path.name}`. For each item, read the cited segment(s) in the student's response and mark one:",
               "", "- **Real:** the segment shows this problem, and this standard is the right one for it.",
               "- **Real problem, wrong standard:** the problem is there, but it belongs under another standard "
               "(often one listed under *Planted*).",
               "- **False:** the problem isn't in the segment.", "",
               "Local only: this file quotes the synthetic essays. Don't commit it.", ""]
    for i, p in enumerate(picks, 1):
        r, f = p.record, p.flag
        version = r["version"]
        cd = CourseDir(data_dir, version)
        a = cd.assignment(r["assignment"])
        lms = CourseDirLMSClient(data_dir, version)
        lms_id = next(s.assignment_id for s in lms.list_submissions(dataset.course_id(version))
                      if file_assignment_id(s.assignment_id)[0] == r["assignment"])
        assignment = lms.get_assignment(dataset.course_id(version), lms_id)
        sub = lms.get_submission(dataset.course_id(version), lms_id, r["student"])
        segments, kind = segment(sub, assignment)
        cited = set(f["segments"])
        same_seg = [o for o in r["flags"] if o is not f and cited & set(o.get("segments", []))]
        out += [f"## {i}. `{f['standard']}`: {a.title}", "",
                f"- **Assignment:** `{r['assignment']}`, week {a.week}, {a.type.replace('_', ' ')}",
                f"- **Prompt:** {a.prompt}"]
        out += [f"- **Student:** `{r['student'][:10]}` ({_profile(data_dir, version, r['student'])})",
                "- **Planted for this submission:** " + (", ".join(
                    f"`{c}` (level {lv}): {text.get(c, '?')[:90]}" for c, lv in sorted((r["planted"] or {}).items()))
                    or "nothing (on track)"),
                f"- **Flagged standard** `{f['standard']}`: {text.get(f['standard'], '?')}"]
        if removed:
            what = (f"absorbed into `{f['into']}` ({text.get(f['into'], '?')[:90]})" if f.get("into")
                    else f["action"].replace("dropped:", "dropped as ").replace("_", " "))
            out.append(f"- **Removed by the {f['step']}:** {what}; reason: {f['reason']}")
        else:
            out.append(f"- **Reviewer** `{f['reviewer']}`, support level {f['support_level']}; reason: "
                       + ("; ".join(f["reasons"]) or "(none given)"))
        if same_seg:
            out.append("- **Also flagged on the same segment:** " + ", ".join(f"`{o['standard']}`" for o in same_seg))
        out += ["", f"**The response** ({kind}s numbered as the reviewer saw them; ◀ marks the cited one):", ""]
        for n, seg in enumerate(segments, 1):
            mark = "  ◀ **cited**" if n in cited else ""
            out.append(f"> **S{n}** {seg}{mark}" if n in cited else f"> S{n} {seg}")
            out.append(">")
        if not cited:
            out.append("> *(the reviewer cited no segment)*")
        if removed:
            out += ["", "**Was removing it right?** " + "  /  ".join(f"☐ {a}" for a in REMOVED_ANSWERS),
                    "**Note:** ____", ""]
        else:
            out += ["", "**Your answer:** " + "  /  ".join(f"☐ {a}" for a in ANSWERS),
                    "**If wrong standard, which one?** ____    **Note:** ____", ""]
    return "\n".join(out)


def build_spot_check(log_path: Path, data_dir: Path, standards_file: Path, out_path: Path,
                     n: int = 15, seed: int = 7, removed: bool = False) -> tuple[Path, int, int]:
    """Writes the Markdown; returns (path, items written, submissions in the log so far). With `removed`, samples
    the flags a supervised step took away instead of the unplanted flags that were kept."""
    rows = read_log(log_path)
    picks = sample(rows, n, seed, removed=removed)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(render(picks, data_dir, standards_file, log_path, removed=removed), encoding="utf-8")
    return out_path, len(picks), len(rows)

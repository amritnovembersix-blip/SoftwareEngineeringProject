"""Cross-model fidelity audit (lab only): re-run generate-course's checker prompt with a different model on a
stratified sample of written submissions, and report how far it agrees with the original checker.

The audit only reports; it never changes the dataset. It is a second opinion, not ground truth. The report and
its JSON hold codes, family names, ids and counts only: never submission text or model reasoning.
"""
import json
import random
import re
import time
from collections import Counter
from collections.abc import Iterable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path

from pydantic import ValidationError

from agentic.coursedata import AssignmentFile, CourseDir
from agentic.dataset import read_rows
from agentic.llm.base import ChatModel
from agentic_lab.answer_key import FidelityRow, StudentKey, TrajectoryRow, answer_key_path
from agentic_lab.synth.evidence import evidenced
from agentic_lab.synth.families import load_families
from agentic_lab.synth.generate_course import (
    FidelityOut, _check_key, _submission_text, build_checker_prompt, fidelity_verdict, planned_checks)

BANDS = {0: "on-track", 1: "Approaching", 2: "Developing", 3: "Beginning"}
WRITTEN = ("paragraph", "short_answer", "essay")
_CODE = re.compile(r"^\d+(-\d+)?\.[A-Z]+(\.[0-9A-Z]+)+$", re.I)
_CHARS_PER_TOKEN = 4  # rough prompt-size estimate when the Adapter exposes no usage
_OUTPUT_TOKENS = 150  # rough size of one FidelityOut reply


# --- sample ------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Candidate:
    """One written submission the audit can check (ids and plan facts only, no text)."""
    version: str
    arm: str
    assignment_id: str
    assignment_type: str
    module: str
    kind: str
    week: int
    student_hash: str
    submission_id: str
    profile: str
    band: str
    has_quirk: bool
    original_fail: bool = False  # has a row in fidelity.jsonl

    def strata(self) -> list[tuple[str, str]]:
        return [("arm", self.arm), ("profile", self.profile), ("band", self.band),
                ("quirk", "yes" if self.has_quirk else "no"), ("module", self.module),
                ("type", self.assignment_type), ("kind", self.kind),
                ("original", "fail" if self.original_fail else "pass")]


@dataclass
class CoverageRow:
    dimension: str
    value: str
    population: int
    sampled: int
    required: bool


@dataclass
class Sample:
    chosen: list[Candidate]
    coverage: list[CoverageRow]
    population: int


@dataclass
class _Course:
    """A course folder's plan facts, read once."""
    cd: CourseDir
    grade: int
    arm: str
    strands: dict[str, str]
    assignments: dict[str, AssignmentFile]
    keys: dict[str, StudentKey]
    trajectory: dict[tuple[str, int], dict[str, int]]
    fidelity: dict[str, FidelityRow]


def _course(courses: dict[str, "_Course"], data_dir: Path, version: str) -> "_Course":
    if version not in courses:
        courses[version] = _load(data_dir, version)
    return courses[version]


def _load(data_dir: Path, version: str) -> _Course:
    cd = CourseDir(data_dir, version)
    course = cd.course()
    key_dir = answer_key_path(data_dir, version)
    trajectory: dict[tuple[str, int], dict[str, int]] = {}
    for t in read_rows(key_dir / "trajectory.jsonl", TrajectoryRow):
        trajectory.setdefault((t.student_hash, t.week), {})[t.standard] = t.level
    fid = key_dir / "fidelity.jsonl"
    return _Course(cd=cd, grade=course.grade, arm=course.arm,
                   strands={m.id: m.strand for m in cd.curriculum().modules},
                   assignments={a.id: a for a in cd.assignments()},
                   keys={k.student_hash: k for k in read_rows(key_dir / "students.jsonl", StudentKey)},
                   trajectory=trajectory,
                   fidelity={r.submission_id: r for r in read_rows(fid, FidelityRow)} if fid.exists() else {})


def _plan_for(course: _Course, families, assignment: AssignmentFile, h: str):
    """(levels, ev, active families, quirks) exactly as generate_course planned this submission."""
    levels = course.trajectory.get((h, assignment.week), {})
    ev = evidenced(assignment, course.strands[assignment.module], levels)
    active, quirks = planned_checks(course.keys[h], ev, families)
    return levels, ev, active, quirks


def _candidates(data_dir: Path, versions: Iterable[str], courses: dict[str, _Course]) -> list[Candidate]:
    families = load_families(Path(data_dir) / "skill_families.json")
    out = []
    for v in versions:
        c = _course(courses, data_dir, v)
        for a in c.assignments.values():
            if a.type not in WRITTEN:
                continue
            for row in c.cd.submissions(a.id):
                _, ev, _, quirks = _plan_for(c, families, a, row.student_hash)
                out.append(Candidate(version=v, arm=c.arm, assignment_id=a.id, assignment_type=a.type,
                                     module=a.module, kind=a.kind, week=a.week, student_hash=row.student_hash,
                                     submission_id=row.submission_id, profile=c.keys[row.student_hash].profile,
                                     band=BANDS[max(ev.values(), default=0)], has_quirk=bool(quirks),
                                     original_fail=row.submission_id in c.fidelity))
    return out


def _required(courses: Mapping[str, _Course]) -> set[tuple[str, str]]:
    req = {("band", b) for b in BANDS.values()} | {("type", t) for t in WRITTEN}
    req |= {("quirk", "yes"), ("kind", "intervention"), ("original", "fail")}  # failures too, or agreement on them is untested
    for c in courses.values():
        req.add(("arm", c.arm))
        req |= {("profile", k.profile) for k in c.keys.values()}
        req |= {("module", m) for m in c.strands}
    return req


def draw_sample(data_dir: Path, versions: Iterable[str], n: int = 40, seed: int = 7,
                _courses: dict[str, _Course] | None = None) -> Sample:
    """A deterministic stratified sample of about `n` written submissions. Every required stratum (each answer-key
    profile, each most-severe band, quirks, each arm, module and written type, an intervention assignment) gets
    at least one submission, even past `n`; remaining slots go round-robin across all observed strata."""
    courses = {} if _courses is None else _courses
    versions = list(versions)
    pool = _candidates(data_dir, versions, courses)
    random.Random(seed).shuffle(pool)
    required = _required(courses)
    chosen: list[Candidate] = []
    taken: set[tuple[str, str]] = set()  # (version, submission id)

    def take(c: Candidate) -> None:
        chosen.append(c)
        taken.add((c.version, c.submission_id))

    # Required strata first: greedy set cover, ties broken by the seeded order.
    uncovered = {s for s in required if any(s in c.strata() for c in pool)}
    while uncovered:
        best = max((c for c in pool if (c.version, c.submission_id) not in taken),
                   key=lambda c: len(uncovered & set(c.strata())))
        take(best)
        uncovered -= set(best.strata())
    # Then round-robin across every observed stratum.
    strata = sorted({s for c in pool for s in c.strata()})
    while len(chosen) < min(n, len(pool)):
        added = False
        for s in strata:
            if len(chosen) >= n:
                break
            nxt = next((c for c in pool if (c.version, c.submission_id) not in taken and s in c.strata()), None)
            if nxt:
                take(nxt)
                added = True
        if not added:
            break
    coverage = [CoverageRow(dimension=d, value=v,
                            population=sum((d, v) in c.strata() for c in pool),
                            sampled=sum((d, v) in c.strata() for c in chosen),
                            required=(d, v) in required)
                for d, v in sorted({s for c in pool for s in c.strata()} | required)]
    return Sample(chosen=chosen, coverage=coverage, population=len(pool))


# --- check -------------------------------------------------------------------------------------------------

@dataclass
class CheckPair:
    name: str
    kind: str  # "family" or "quirk"
    original_present: bool
    audit_present: bool


@dataclass
class AuditRecord:
    submission_id: str
    version: str
    arm: str
    assignment_id: str
    profile: str
    band: str
    checks: list[CheckPair] = field(default_factory=list)
    original_pass: bool = True
    audit_pass: bool = True
    audit_extra: list[str] = field(default_factory=list)
    usable: bool = True
    possibly_unjudged: bool = False  # see `possibly_unjudged`


def possibly_unjudged(orig: FidelityRow | None, expected: list[str]) -> bool:
    """True when the original row has every expected check missing and no extra. That is also what
    generate_course writes when its last checker reply was malformed, so the row may not be a real verdict."""
    if orig is None or orig.extra or not expected:
        return False
    return {_check_key(x) for x in orig.missing} == {_check_key(x) for x in expected}


def audit_verdict(check: FidelityOut, expected: list[str], levels: Mapping[str, int]):
    """(missing, extra) under the generator's own predicate (`fidelity_verdict`)."""
    return fidelity_verdict(check, expected, levels)


def _prompt_parts(data_dir: Path, c: Candidate, standards: Mapping[str, str], courses: dict[str, _Course]):
    course = _course(courses, data_dir, c.version)
    families = load_families(Path(data_dir) / "skill_families.json")
    assignment = course.assignments[c.assignment_id]
    levels, ev, active, quirks = _plan_for(course, families, assignment, c.student_hash)
    row = next(r for r in course.cd.submissions(c.assignment_id) if r.submission_id == c.submission_id)
    body = row.answers if assignment.type == "short_answer" else row.paragraphs
    prompt = build_checker_prompt(ev, active, quirks, assignment, _submission_text(assignment, body),
                                  standards, course.grade)
    return prompt, levels, list(active), quirks, course


def checker_prompt(data_dir: Path, c: Candidate, standards: Mapping[str, str]) -> tuple[str, str]:
    """The exact (system, user) checker prompt generate_course built for this submission."""
    return _prompt_parts(data_dir, c, standards, {})[0]


@dataclass
class AuditResult:
    model: str
    writer_model: str
    sample: Sample
    records: list[AuditRecord]
    calls: int
    wall_seconds: float
    prompt_chars: int
    summary: dict


def _check_one(chat: ChatModel, data_dir: Path, c: Candidate, standards, courses) -> tuple[AuditRecord, int]:
    prompt, levels, fams, quirks, course = _prompt_parts(data_dir, c, standards, courses)
    expected = fams + quirks
    orig = course.fidelity.get(c.submission_id)
    orig_missing = {_check_key(x) for x in (orig.missing if orig else [])}
    rec = AuditRecord(submission_id=c.submission_id, version=c.version, arm=c.arm, assignment_id=c.assignment_id,
                      profile=c.profile, band=c.band, original_pass=orig is None,
                      possibly_unjudged=possibly_unjudged(orig, expected))
    chars = len(prompt[0]) + len(prompt[1])
    try:
        reply = chat.structured(*prompt, FidelityOut)
    except ValidationError:  # its message may quote the reply: dropped, counted as unusable
        rec.usable = False
        return rec, chars
    missing, extra = audit_verdict(reply, expected, levels)
    missing_keys = {_check_key(x) for x in missing}
    rec.checks = [CheckPair(n, "family" if n in fams else "quirk", _check_key(n) not in orig_missing,
                            _check_key(n) not in missing_keys) for n in expected]
    rec.audit_pass = not missing and not extra
    rec.audit_extra = extra
    return rec, chars


def run_audit(chat: ChatModel, data_dir: Path, versions: Iterable[str], n: int = 40, seed: int = 7,
              standards: Mapping[str, str] | None = None, model: str = "", workers: int = 1,
              writer_model: str = "") -> AuditResult:
    """Sample, re-check each submission with `chat`, and summarize. Provider errors propagate (the CLI prints
    class + status only); an unusable (ValidationError) reply is counted and skipped."""
    if standards is None:
        from agentic_lab.standards_v2 import REVIEWABLE_XLSX
        from agentic_lab.synth.generate_course import read_standard_texts
        standards = read_standard_texts(REVIEWABLE_XLSX)
    data_dir = Path(data_dir)
    courses: dict[str, _Course] = {}
    sample = draw_sample(data_dir, versions, n, seed, _courses=courses)
    start = time.monotonic()
    with ThreadPoolExecutor(max_workers=max(1, workers), thread_name_prefix="audit-fidelity") as pool:
        futures = [pool.submit(_check_one, chat, data_dir, c, standards, courses) for c in sample.chosen]
        results = [f.result() for f in futures]
    wall = time.monotonic() - start
    records = [r for r, _ in results]
    result = AuditResult(model=model, writer_model=writer_model, sample=sample, records=records, calls=len(results), wall_seconds=wall,
                         prompt_chars=sum(ch for _, ch in results), summary=summarize(records))
    return result


# --- agreement ---------------------------------------------------------------------------------------------

def _agreement(pairs: list[tuple[bool, bool]]) -> dict:
    """2x2 counts for (original, audit) booleans, percent agreement and Cohen's kappa (None when undefined)."""
    bp = sum(o and a for o, a in pairs)
    ba = sum(not o and not a for o, a in pairs)
    oo = sum(o and not a for o, a in pairs)
    ao = sum(a and not o for o, a in pairs)
    n = len(pairs)
    po = (bp + ba) / n if n else None
    kappa = None
    if n:
        pe = ((bp + oo) * (bp + ao) + (ba + ao) * (ba + oo)) / (n * n)
        kappa = (po - pe) / (1 - pe) if pe < 1 else None
    return {"n": n, "both_present": bp, "both_absent": ba, "original_only": oo, "audit_only": ao,
            "percent_agreement": po, "kappa": kappa}


def _pass_agreement(records: list[AuditRecord]) -> dict:
    a = _agreement([(r.original_pass, r.audit_pass) for r in records])
    return {"n": a["n"], "both_pass": a["both_present"], "both_fail": a["both_absent"],
            "original_pass_audit_fail": a["original_only"], "original_fail_audit_pass": a["audit_only"],
            "percent_agreement": a["percent_agreement"], "kappa": a["kappa"]}


def _label(p: CheckPair) -> str:
    return p.name if p.kind == "family" else f"quirk:{p.name}"


def _check_agreement(records: list[AuditRecord]) -> tuple[dict, dict]:
    """(per check, totals) over the records' check pairs."""
    pairs = [p for r in records for p in r.checks]
    per_check = {lab: _agreement([(p.original_present, p.audit_present) for p in pairs if _label(p) == lab])
                 for lab in sorted({_label(p) for p in pairs})}
    totals = {k: _agreement([(p.original_present, p.audit_present) for p in pairs if p.kind == k])
              for k in ("family", "quirk")}
    totals["all"] = _agreement([(p.original_present, p.audit_present) for p in pairs])
    return per_check, totals


def summarize(records: list[AuditRecord]) -> dict:
    """Agreement numbers over the usable records (codes, names and counts only), also without the possibly
    unjudged original rows."""
    usable = [r for r in records if r.usable]
    per_check, totals = _check_agreement(usable)
    judged = [r for r in usable if not r.possibly_unjudged]
    w_per_check, w_totals = _check_agreement(judged)
    per_band = {}
    for b in BANDS.values():
        rs = [r for r in usable if r.band == b]
        per_band[b] = {"checks": _agreement([(p.original_present, p.audit_present) for r in rs for p in r.checks]),
                       "submissions": _pass_agreement(rs)}
    codes: Counter[str] = Counter()
    spelling: dict[str, str] = {}
    non_code = 0
    for r in usable:
        for x in r.audit_extra:
            if _CODE.match(x):
                spelling.setdefault(x.lower(), x)
                codes[x.lower()] += 1
            else:
                non_code += 1  # free text from the model: counted, never shown
    return {
        "usable": len(usable), "unusable": len(records) - len(usable),
        "possibly_unjudged": len(usable) - len(judged),
        "per_check": per_check, "totals": totals, "per_band": per_band,
        "submissions": _pass_agreement(usable),
        "without_unjudged": {"per_check": w_per_check, "totals": w_totals, "submissions": _pass_agreement(judged)},
        "extras": {"submissions_with_extra": sum(bool(r.audit_extra) for r in usable),
                   "top_codes": [[spelling[k], v] for k, v in codes.most_common(10)],
                   "non_code_entries": non_code},
    }


# --- report ------------------------------------------------------------------------------------------------

def _pct(x) -> str:
    return "n/a" if x is None else f"{x:.0%}"


def _kappa(x) -> str:
    return "n/a" if x is None else f"{x:.2f}"


def report_data(result: AuditResult) -> dict:
    tokens_in = result.prompt_chars // _CHARS_PER_TOKEN
    return {
        "model": result.model, "writer_model": result.writer_model, "n_sampled": len(result.sample.chosen), "n_usable": result.summary["usable"],
        "n_unusable": result.summary["unusable"], "population": result.sample.population,
        "calls": result.calls, "wall_seconds": round(result.wall_seconds, 1),
        "usage": {"source": "estimate from prompt size (the ChatModel Adapter exposes no token usage)",
                  "input_tokens_est": tokens_in, "output_tokens_est": _OUTPUT_TOKENS * result.calls},
        "coverage": [asdict(r) for r in result.sample.coverage],
        "sample": [{"version": c.version, "submission_id": c.submission_id, "assignment_id": c.assignment_id,
                    "profile": c.profile, "band": c.band} for c in result.sample.chosen],
        "n_possibly_unjudged": result.summary["possibly_unjudged"],
        **{k: v for k, v in result.summary.items() if k not in ("usable", "unusable", "possibly_unjudged")},
    }


def _agree_table(rows: dict[str, dict]) -> list[str]:
    out = ["| check | n | both present | both absent | original only | audit only | agreement | kappa |",
           "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for name, a in rows.items():
        out.append(f"| {name} | {a['n']} | {a['both_present']} | {a['both_absent']} | {a['original_only']} | "
                   f"{a['audit_only']} | {_pct(a['percent_agreement'])} | {_kappa(a['kappa'])} |")
    return out


def format_markdown(d: dict) -> str:
    u = d["usage"]
    lines = [
        "# Cross-model fidelity audit", "",
        "## How to read this", "",
        "The audit re-runs generate-course's checker prompt with a different model on a stratified sample and "
        "compares its verdicts with the original checker's. It is a second opinion, not ground truth: where the "
        "two disagree, either may be wrong. The dataset is not changed. Only codes, family names, ids and counts "
        "appear here.", "",
        "## Run", "",
        f"- Audit model: {d['model']}; writer model (original checker): {d['writer_model'] or 'unknown'}",
        f"- Sampled: {d['n_sampled']} of {d['population']} written submissions; usable replies: {d['n_usable']} "
        f"(unusable: {d['n_unusable']})",
        f"- Calls: {d['calls']}; wall time: {d['wall_seconds']} s",
        f"- Possibly unjudged original rows: {d['n_possibly_unjudged']} (every expected check missing and no "
        "extra; generate-course also writes this when its last checker reply was malformed, so these may not be "
        "real verdicts). Agreement is shown with them and, further down, without them.",
        f"- Tokens: about {u['input_tokens_est']} in, {u['output_tokens_est']} out. This is an {u['source']}; "
        "multiply by your model's prices for a cost estimate.", "",
        "## Coverage", "",
        "| dimension | value | population | sampled | required |", "|---|---|---:|---:|---|",
        *[f"| {r['dimension']} | {r['value']} | {r['population']} | {r['sampled']} | "
          f"{'yes' if r['required'] else ''} |" for r in d["coverage"]], "",
        "## Agreement per check (original checker vs audit)", "",
        *_agree_table(d["per_check"]), "",
        "### Totals", "",
        *_agree_table(d["totals"]), "",
        "## Agreement per most-severe band", "",
        "| band | checks n | check agreement | check kappa | submissions n | pass agreement |",
        "|---|---:|---:|---:|---:|---:|",
        *[f"| {b} | {x['checks']['n']} | {_pct(x['checks']['percent_agreement'])} | "
          f"{_kappa(x['checks']['kappa'])} | {x['submissions']['n']} | "
          f"{_pct(x['submissions']['percent_agreement'])} |" for b, x in d["per_band"].items()], "",
        "## Pass/fail per submission (same predicate as generate-course)", "",
    ]
    s = d["submissions"]
    lines += [f"- Both pass: {s['both_pass']}; both fail: {s['both_fail']}; original pass, audit fail: "
              f"{s['original_pass_audit_fail']}; original fail, audit pass: {s['original_fail_audit_pass']}",
              f"- Agreement: {_pct(s['percent_agreement'])}; kappa: {_kappa(s['kappa'])}", "",
              "## Extras (unplanned severe weaknesses the audit lists)", ""]
    e = d["extras"]
    lines.append(f"- Submissions with any extra code: {e['submissions_with_extra']}")
    if e["non_code_entries"]:
        lines.append(f"- Entries that are not standards codes (not shown): {e['non_code_entries']}")
    lines += [f"- {code}: {n}" for code, n in e["top_codes"]] or ["- (none)"]
    lines.append("")
    w = d["without_unjudged"]
    ws = w["submissions"]
    lines += ["## Without the possibly unjudged rows", "",
              f"Excludes {d['n_possibly_unjudged']} submissions whose original row may be a malformed check.", "",
              *_agree_table(w["per_check"]), "", "### Totals", "", *_agree_table(w["totals"]), "",
              f"- Pass/fail: both pass {ws['both_pass']}; both fail {ws['both_fail']}; original pass, audit fail "
              f"{ws['original_pass_audit_fail']}; original fail, audit pass {ws['original_fail_audit_pass']}; "
              f"agreement {_pct(ws['percent_agreement'])}; kappa {_kappa(ws['kappa'])}"]
    return "\n".join(lines) + "\n"


def write_report(result: AuditResult, md_path: Path) -> tuple[Path, Path]:
    """Write the Markdown report and a JSON with the same numbers next to it."""
    md_path = Path(md_path).with_suffix(".md")
    md_path.parent.mkdir(parents=True, exist_ok=True)
    d = report_data(result)
    md_path.write_text(format_markdown(d), encoding="utf-8")
    js = md_path.with_suffix(".json")
    js.write_text(json.dumps(d, indent=2), encoding="utf-8")
    return md_path, js

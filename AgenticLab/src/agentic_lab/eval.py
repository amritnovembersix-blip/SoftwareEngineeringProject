"""Evaluation: precision/recall vs labels, strategy comparison, calibration of the Jev probabilities.

Reports hold standard codes, counts and probabilities only; never essay text.
"""
import time
from collections.abc import Callable
from typing import Literal

from pydantic import BaseModel

from agentic.dataset import EssayRow
from agentic_lab.labels import LabelRow
from agentic.models import Finding, ReviewResult, SubmissionRef
from agentic.review.graph import Reviewer


def concept_of(code: str) -> str:
    return ".".join(code.split(".")[:4])


class Counts(BaseModel):
    tp: int
    fp: int
    fn: int

    @property
    def precision(self) -> float:
        d = self.tp + self.fp
        return self.tp / d if d else 0.0

    @property
    def recall(self) -> float:
        d = self.tp + self.fn
        return self.tp / d if d else 0.0


def score(found: set[str], expected: set[str], level: Literal["exact", "concept"]) -> Counts:
    if level == "concept":
        found = {concept_of(c) for c in found}
        expected = {concept_of(c) for c in expected}
    return Counts(tp=len(found & expected), fp=len(found - expected), fn=len(expected - found))


def split_labels(row: LabelRow, allowed_codes: set[str]) -> tuple[set[str], list[str]]:
    codes = [u.standard for u in row.underdeveloped]
    return {c for c in codes if c in allowed_codes}, [c for c in codes if c not in allowed_codes]


def brier(pairs: list[tuple[float, bool]]) -> float | None:
    if not pairs:
        return None
    return sum((p - (1.0 if t else 0.0)) ** 2 for p, t in pairs) / len(pairs)


class Bin(BaseModel):
    lo: float
    hi: float
    count: int
    mean_p: float
    frac_true: float


def reliability(pairs: list[tuple[float, bool]], bins: int = 5) -> list[Bin]:
    groups: list[list[tuple[float, bool]]] = [[] for _ in range(bins)]
    for p, t in pairs:
        groups[min(int(p * bins), bins - 1)].append((p, t))
    out = []
    for i, g in enumerate(groups):
        n = len(g)
        out.append(Bin(lo=i / bins, hi=(i + 1) / bins, count=n,
                       mean_p=sum(p for p, _ in g) / n if n else 0.0,
                       frac_true=sum(1 for _, t in g if t) / n if n else 0.0))
    return out


class ThresholdRow(BaseModel):
    cutoff: float
    precision: float
    recall: float


def threshold_table(per_student: list[tuple[list[Finding], set[str]]],
                    cutoffs: tuple[float, ...] = (0.3, 0.5, 0.7)) -> list[ThresholdRow]:
    rows = []
    for cutoff in cutoffs:
        tp = fp = fn = 0
        for findings, expected in per_student:
            kept = {f.standard_code for f in findings if f.p_correct is None or f.p_correct >= cutoff}
            c = score(kept, expected, "concept")
            tp, fp, fn = tp + c.tp, fp + c.fp, fn + c.fn
        total = Counts(tp=tp, fp=fp, fn=fn)
        rows.append(ThresholdRow(cutoff=cutoff, precision=total.precision, recall=total.recall))
    return rows


def miss_recall(per_student: list[tuple[ReviewResult, set[str]]]) -> float | None:
    missed = flagged = 0
    for result, expected in per_student:
        found = {f.standard_code for f in result.findings}
        possible = {m.standard_code for m in result.met_standards if m.possible_miss}
        gone = expected - found
        missed += len(gone)
        flagged += len(gone & possible)
    return flagged / missed if missed else None


LEVELS = ("Approaching", "Developing", "Beginning")


def recall_by_level(rows: list[tuple[set[str], dict[str, str]]]) -> dict[str, float | None]:
    """rows: (found codes, {expected code: labeled level}). Exact-code recall per labeled level."""
    hit = {lvl: 0 for lvl in LEVELS}
    total = {lvl: 0 for lvl in LEVELS}
    for found, levels in rows:
        for code, lvl in levels.items():
            if lvl in total:
                total[lvl] += 1
                hit[lvl] += code in found
    return {lvl: (hit[lvl] / total[lvl] if total[lvl] else None) for lvl in LEVELS}


def not_evident_counts(per_result: list[list[str]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for codes in per_result:
        for c in codes:
            counts[c] = counts.get(c, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))


class StudentRow(BaseModel):
    student: str  # hash prefix only
    expected: list[str]
    found: list[str]
    finding_p: list[float | None]
    possible_misses: list[str]
    llm_calls: int
    seconds: float


class Calibration(BaseModel):
    brier: float | None
    bins: list[Bin]


class StrategyReport(BaseModel):
    exact: Counts
    concept: Counts
    false_alarm_rate: float | None
    llm_calls: int
    seconds: float
    finding_calibration: Calibration
    met_calibration: Calibration
    thresholds: list[ThresholdRow]
    miss_recall: float | None
    not_evident: dict[str, int] = {}
    recall_by_level: dict[str, float | None] = {}
    per_student: list[StudentRow]


class EvalReport(BaseModel):
    students: int
    strands: list[str] = ["W"]
    out_of_scope: list[str]
    strategies: dict[str, StrategyReport]


def _calibration(pairs: list[tuple[float, bool]]) -> Calibration:
    return Calibration(brier=brier(pairs), bins=reliability(pairs))


def run_evaluation(reviewers: dict[str, Reviewer], labels: list[LabelRow], essays: list[EssayRow],
                   allowed_codes: set[str], students: int | None = None) -> EvalReport:
    by_hash = {r.student_hash: r for r in labels}
    selected = essays[:students] if students is not None else essays
    expected_by: dict[str, set[str]] = {}
    out_of_scope: set[str] = set()
    for e in selected:
        row = by_hash.get(e.student_hash)
        if row is None:
            raise ValueError(f"no label for student {e.student_hash[:8]}")
        inside, outside = split_labels(row, allowed_codes)
        expected_by[e.student_hash] = inside
        out_of_scope.update(outside)

    strategies: dict[str, StrategyReport] = {}
    for name, reviewer in reviewers.items():
        pairs: list[tuple[ReviewResult, set[str]]] = []
        rows: list[StudentRow] = []
        f_pairs: list[tuple[float, bool]] = []
        m_pairs: list[tuple[float, bool]] = []
        exact = [0, 0, 0]
        conc = [0, 0, 0]
        clean_findings = clean_students = calls = 0
        total_seconds = 0.0
        level_rows: list[tuple[set[str], dict[str, str]]] = []
        ne_lists: list[list[str]] = []
        for e in selected:
            ref = SubmissionRef(course_id=e.course_id, assignment_id=e.assignment_id,
                                user_id=e.student_hash, submission_id=e.submission_id,
                                attempt=e.attempt, submitted_at=e.submitted_at)
            start = time.perf_counter()
            result = reviewer.run(ref)
            secs = time.perf_counter() - start
            expected = expected_by[e.student_hash]
            pairs.append((result, expected))
            found = {f.standard_code for f in result.findings}
            for acc, level in ((exact, "exact"), (conc, "concept")):
                c = score(found, expected, level)
                acc[0] += c.tp
                acc[1] += c.fp
                acc[2] += c.fn
            expected_concepts = {concept_of(c) for c in expected}
            f_pairs += [(f.p_correct, concept_of(f.standard_code) in expected_concepts)
                        for f in result.findings if f.p_correct is not None]
            m_pairs += [(m.p_met, m.standard_code not in expected)
                        for m in result.met_standards if m.p_met is not None]
            levels = {u.standard: u.level_label for u in by_hash[e.student_hash].underdeveloped
                      if u.standard in expected}
            level_rows.append((found, levels))
            ne_lists.append(result.not_evident)
            if by_hash[e.student_hash].meets_all_standards:
                clean_students += 1
                clean_findings += len(result.findings)
            calls += result.llm_calls
            total_seconds += secs
            rows.append(StudentRow(
                student=e.student_hash[:8], expected=sorted(expected), found=sorted(found),
                finding_p=[f.p_correct for f in result.findings],
                possible_misses=sorted(m.standard_code for m in result.met_standards if m.possible_miss),
                llm_calls=result.llm_calls, seconds=secs))
        strategies[name] = StrategyReport(
            exact=Counts(tp=exact[0], fp=exact[1], fn=exact[2]),
            concept=Counts(tp=conc[0], fp=conc[1], fn=conc[2]),
            false_alarm_rate=clean_findings / clean_students if clean_students else None,
            llm_calls=calls, seconds=total_seconds,
            finding_calibration=_calibration(f_pairs), met_calibration=_calibration(m_pairs),
            thresholds=threshold_table([(r.findings, exp) for r, exp in pairs]),
            miss_recall=miss_recall(pairs), not_evident=not_evident_counts(ne_lists),
            recall_by_level=recall_by_level(level_rows), per_student=rows)
    strands = next(iter(reviewers.values())).strands if reviewers else ["W"]
    return EvalReport(students=len(selected), strands=strands, out_of_scope=sorted(out_of_scope),
                      strategies=strategies)


def _fmt(v: float | None, spec: str = ".2f") -> str:
    return "n/a" if v is None else format(v, spec)


def format_report(report: EvalReport) -> str:
    names = list(report.strategies)
    width = 12
    lines = [f"Evaluation over {report.students} students",
             f"Strands reviewed: {', '.join(report.strands)}",
             f"Out-of-scope labels: {len(report.out_of_scope)}"
             + (f" ({', '.join(report.out_of_scope)})" if report.out_of_scope else ""),
             "", f"{'':28}" + "".join(f"{n:>{width}}" for n in names)]

    def row(label: str, fn) -> None:
        lines.append(f"{label:28}" + "".join(f"{fn(report.strategies[n]):>{width}}" for n in names))

    row("exact precision", lambda s: _fmt(s.exact.precision))
    row("exact recall", lambda s: _fmt(s.exact.recall))
    row("concept precision", lambda s: _fmt(s.concept.precision))
    row("concept recall", lambda s: _fmt(s.concept.recall))
    row("false alarms / clean student", lambda s: _fmt(s.false_alarm_rate))
    row("miss recall", lambda s: _fmt(s.miss_recall))
    for lvl in LEVELS:
        row(f"recall ({lvl})", lambda s, lvl=lvl: _fmt(s.recall_by_level.get(lvl)))
    row("not-evident verdicts", lambda s: str(sum(s.not_evident.values())))
    row("Brier (finding)", lambda s: _fmt(s.finding_calibration.brier, ".3f"))
    row("Brier (met)", lambda s: _fmt(s.met_calibration.brier, ".3f"))
    row("LLM calls", lambda s: str(s.llm_calls))
    row("seconds", lambda s: _fmt(s.seconds, ".1f"))
    for n in names:
        lines += ["", f"Thresholds ({n}): cutoff precision recall"]
        lines += [f"  {t.cutoff:.2f}  {t.precision:.2f}  {t.recall:.2f}" for t in report.strategies[n].thresholds]
        for label, cal in (("Finding", report.strategies[n].finding_calibration),
                           ("Met", report.strategies[n].met_calibration)):
            lines.append(f"{label} reliability ({n}): bin count mean_p frac_true")
            lines += [f"  {b.lo:.1f}-{b.hi:.1f}  {b.count}  {b.mean_p:.2f}  {b.frac_true:.2f}"
                      for b in cal.bins]
        ne = report.strategies[n].not_evident
        lines.append(f"Not evident ({n}): " + (", ".join(f"{c} x{k}" for c, k in ne.items()) or "-"))
        lines.append(f"Per-student ({n}): student | expected | found | possible misses | LLM calls")
        lines += [f"  {r.student} | {','.join(r.expected) or '-'} | {','.join(r.found) or '-'} | "
                  f"{','.join(r.possible_misses) or '-'} | {r.llm_calls}"
                  for r in report.strategies[n].per_student]
    return "\n".join(lines)


def summary_metrics() -> list[tuple[str, "Callable[[StrategyReport], str]"]]:
    """The summary rows shared by the Markdown report and the demo's Compare tab."""
    return [
        ("exact precision", lambda s: _fmt(s.exact.precision)),
        ("exact recall", lambda s: _fmt(s.exact.recall)),
        ("concept precision", lambda s: _fmt(s.concept.precision)),
        ("concept recall", lambda s: _fmt(s.concept.recall)),
        ("false alarms / clean student", lambda s: _fmt(s.false_alarm_rate)),
        ("miss recall", lambda s: _fmt(s.miss_recall)),
        *[(f"recall ({lvl})", lambda s, lvl=lvl: _fmt(s.recall_by_level.get(lvl))) for lvl in LEVELS],
        ("not-evident verdicts", lambda s: str(sum(s.not_evident.values()))),
        ("Brier (finding)", lambda s: _fmt(s.finding_calibration.brier, ".3f")),
        ("Brier (met)", lambda s: _fmt(s.met_calibration.brier, ".3f")),
        ("LLM calls", lambda s: str(s.llm_calls)),
        ("seconds", lambda s: _fmt(s.seconds, ".1f")),
    ]


def format_report_markdown(report: EvalReport) -> str:
    """The same content as format_report, as Markdown (headings and tables) for review."""
    names = list(report.strategies)

    def table(header: list[str], rows: list[list[str]]) -> list[str]:
        return (["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
                + ["| " + " | ".join(r) + " |" for r in rows])

    out = [f"# Evaluation over {report.students} students", "",
           f"- **Strands reviewed:** {', '.join(report.strands)}",
           f"- **Out-of-scope labels:** {len(report.out_of_scope)}"
           + (f" ({', '.join(report.out_of_scope)})" if report.out_of_scope else ""), "",
           "## Summary", ""]
    rows = [[label] + [fn(report.strategies[n]) for n in names] for label, fn in summary_metrics()]
    out += table(["Metric", *names], rows)
    for n in names:
        sr = report.strategies[n]
        out += ["", f"## {n}", "", "### Thresholds", ""]
        out += table(["Cutoff", "Precision", "Recall"],
                     [[f"{t.cutoff:.2f}", f"{t.precision:.2f}", f"{t.recall:.2f}"] for t in sr.thresholds])
        for label, cal in (("Finding", sr.finding_calibration), ("Met", sr.met_calibration)):
            out += ["", f"### {label} reliability", ""]
            out += table(["Bin", "Count", "Mean p", "Fraction true"],
                         [[f"{b.lo:.1f}–{b.hi:.1f}", str(b.count), f"{b.mean_p:.2f}", f"{b.frac_true:.2f}"]
                          for b in cal.bins])
        out += ["", "### Not evident", "",
                ", ".join(f"{c} ×{k}" for c, k in sr.not_evident.items()) or "-", "",
                "### Per student", ""]
        out += table(["Student", "Expected", "Found", "Possible misses", "LLM calls"],
                     [[r.student, ", ".join(r.expected) or "-", ", ".join(r.found) or "-",
                       ", ".join(r.possible_misses) or "-", str(r.llm_calls)] for r in sr.per_student])
    return "\n".join(out) + "\n"

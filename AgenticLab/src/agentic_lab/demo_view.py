"""Pure helpers for the Streamlit demo (no UI, no Adapters)."""
from dataclasses import dataclass
from pathlib import Path

from agentic_lab.eval import EvalReport, StrategyReport, concept_of, summary_metrics


@dataclass(frozen=True)
class Comparison:
    hits: list[str]
    misses: list[str]
    extras: list[str]


def compare(found_codes: set[str], expected_codes: set[str]) -> Comparison:
    found = {concept_of(c) for c in found_codes}
    expected = {concept_of(c) for c in expected_codes}
    return Comparison(hits=sorted(found & expected), misses=sorted(expected - found),
                      extras=sorted(found - expected))


def comparison_lines(cmp: Comparison) -> list[str]:
    """Plain-text strip lines (render with st.text: markdown turns a leading "+" into a bullet)."""
    lines = ([f"✓ {c}" for c in cmp.hits] + [f"✗ {c}" for c in cmp.misses]
             + [f"+ {c}" for c in cmp.extras])
    return lines or ["Nothing expected, nothing found."]


def student_options(labels) -> list[tuple[str, str]]:
    return [(r.student_hash,
             f"{r.student_hash[:8]} · {'meets all' if r.meets_all_standards else 'has weaknesses'}")
            for r in labels]


# --- Compare reports tab (saved eval-*.json files; no model calls) ---------------------------

def list_eval_reports(reports_dir) -> list[Path]:
    """Saved evaluation JSON files, newest first (file names carry a sortable timestamp)."""
    d = Path(reports_dir)
    return sorted(d.glob("eval-*.json"), reverse=True) if d.exists() else []


def _stamp(path: Path) -> str:
    s = path.stem.removeprefix("eval-")  # 20261005T213816
    return f"{s[4:6]}-{s[6:8]} {s[9:11]}:{s[11:13]}" if len(s) >= 13 else s


def load_runs(paths) -> tuple[dict[str, StrategyReport], list[str]]:
    """One column per (report, strategy run), labeled '<MM-DD HH:MM> · <run>'. Unreadable files are skipped."""
    runs: dict[str, StrategyReport] = {}
    skipped: list[str] = []
    for p in paths:
        try:
            report = EvalReport.model_validate_json(Path(p).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            skipped.append(Path(p).name)
            continue
        for name, sr in report.strategies.items():
            runs[f"{_stamp(Path(p))} · {name}"] = sr
    return runs, skipped


def summary_rows(runs: dict[str, StrategyReport]) -> list[dict]:
    return [{"Metric": label, **{col: fn(sr) for col, sr in runs.items()}} for label, fn in summary_metrics()]


def student_diff(a: StrategyReport, b: StrategyReport) -> list[dict]:
    """Per student: expected, found in A and B, and what B added/removed relative to A."""
    a_by = {r.student: r for r in a.per_student}
    b_by = {r.student: r for r in b.per_student}
    rows = []
    for student in sorted(set(a_by) | set(b_by)):
        ra, rb = a_by.get(student), b_by.get(student)
        expected = (ra or rb).expected
        fa, fb = set(ra.found) if ra else set(), set(rb.found) if rb else set()
        rows.append({"Student": student, "Clean": not expected, "Expected": ", ".join(expected),
                     "Found A": ", ".join(sorted(fa)), "Found B": ", ".join(sorted(fb)),
                     "Added": ", ".join(sorted(fb - fa)), "Removed": ", ".join(sorted(fa - fb))})
    return rows


def calibration_rows(runs: dict[str, StrategyReport]) -> list[dict]:
    """Finding-check reliability: fraction actually correct per probability bin, one column per run."""
    bins: dict[str, dict] = {}
    for col, sr in runs.items():
        for b in sr.finding_calibration.bins:
            if b.count:
                label = f"{b.lo:.1f}–{b.hi:.1f}"
                bins.setdefault(label, {"Bin": label})[col] = b.frac_true
    return [bins[k] for k in sorted(bins)]


def not_evident_rows(runs: dict[str, StrategyReport]) -> list[dict]:
    codes = sorted({c for sr in runs.values() for c in sr.not_evident})
    return [{"Standard": c, **{col: sr.not_evident.get(c, 0) for col, sr in runs.items()}} for c in codes]

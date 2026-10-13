"""Review reports: JSON file (no essay or issue text) and a plain-text summary."""
import json
from datetime import datetime
from pathlib import Path

from agentic.models import ReviewResult


def write_review_report(result: ReviewResult, reports_dir: Path, now: datetime | None = None,
                        models: dict | None = None, strands: list[str] | None = None) -> Path:
    now = now or datetime.now()
    reports_dir = Path(reports_dir)
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / (f"{result.submission_ref.submission_id}-{result.strategy}-"
                          f"{now.strftime('%Y%m%dT%H%M%S')}.json")
    body = result.model_dump(mode="json")
    body["models"] = models if models is not None else {
        "chat": None, "embedder": None, "decision": result.verification.model}
    if strands is not None:
        body["strands"] = strands
    path.write_text(json.dumps(body, indent=2), encoding="utf-8")
    return path


def print_summary(result: ReviewResult) -> str:
    lines = [f"Strategy: {result.strategy}   Verification: {result.verification.status}",
             f"Findings ({len(result.findings)}):"]
    for f in result.findings:
        p = "n/a" if f.p_correct is None else f"{f.p_correct:.2f}"
        paras = ",".join(str(n) for n in f.paragraphs)
        lines.append(f"  {f.standard_code} ({f.concept}) P(correct)={p} paragraphs={paras}: {f.summary}")
    misses = [m for m in result.met_standards if m.possible_miss]
    lines.append(f"Possible misses ({len(misses)}):")
    for m in misses:
        p = "n/a" if m.p_met is None else f"{m.p_met:.2f}"
        lines.append(f"  {m.standard_code} ({m.concept}) P(met)={p}")
    return "\n".join(lines)

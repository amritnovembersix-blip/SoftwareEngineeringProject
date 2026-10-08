"""Generate synthetic essays from labels. Only this module and the evaluator read labels."""
import json
import os
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field

from agentic import dataset
from agentic.dataset import AssignmentInfo, EssayRow
from agentic_lab.labels import LabelRow
from agentic.llm.base import ChatModel
from agentic_lab.synth.assignment import assignment_prompt

SUBMITTED_AT = datetime(2026, 10, 1, 15, 0, 0, tzinfo=timezone.utc)
_SEVERITY = {
    1: "occasionally (Approaching)",
    2: "frequently (Developing)",
    3: "pervasively (Beginning)",
}


class EssayOut(BaseModel):
    paragraphs: list[str] = Field(min_length=3)


@dataclass
class GenerateReport:
    written: int
    skipped: int


def build_prompt(row: LabelRow, assignment: AssignmentInfo | None = None) -> tuple[str, str]:
    system = (
        f"You are a grade {row.grade} student writing an essay for class. "
        "Write the way a student at that grade really writes. "
        "Return the essay as a list of paragraphs."
    )
    if row.meets_all_standards or not row.underdeveloped:
        guide = ("Write a clean, proficient essay for your grade level, with no deliberate "
                 "weaknesses.")
    else:
        lines = [f"- {u.concept}: {u.description}. Show this weakness "
                 f"{_SEVERITY.get(u.level, u.level_label.lower())} in the essay."
                 for u in row.underdeveloped]
        guide = ("Your writing should clearly show weakness in each of these areas, at the "
                 "stated frequency:\n" + "\n".join(lines) +
                 "\nOtherwise your writing is proficient for your grade. "
                 "Never mention standards and never point out or label your own flaws.")
    return system, f"{assignment_prompt(assignment)}\n\n{guide}"


def check_targets(labels: list[LabelRow], assignment: AssignmentInfo) -> list[str]:
    """Label codes outside the assignment's targets (empty when it declares none)."""
    if not assignment.targets:
        return []
    allowed = set(assignment.targets)
    return sorted({u.standard for r in labels for u in r.underdeveloped if u.standard not in allowed})


def _essay_row(label: LabelRow, version: str, paragraphs: list[str]) -> EssayRow:
    return EssayRow(
        student_hash=label.student_hash, submission_id=f"sub-{label.student_hash[:12]}",
        attempt=1, assignment_id=dataset.assignment_id(version),
        course_id=dataset.course_id(version), submitted_at=SUBMITTED_AT, paragraphs=paragraphs)


def _line(row: EssayRow) -> str:
    d = row.model_dump(mode="json")
    d["submitted_at"] = row.submitted_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return json.dumps(d, ensure_ascii=False) + "\n"


def generate(chat: ChatModel, labels: list[LabelRow], out_path: Path, version: str,
             overwrite: bool = False, progress: Callable[[str], None] | None = None,
             assignment: AssignmentInfo | None = None) -> GenerateReport:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    existing: set[str] = set()
    if not overwrite and out_path.exists():
        existing = {r.student_hash for r in dataset.read_rows(out_path, EssayRow)}
    written = skipped = 0
    tmp_name = None
    if overwrite:
        fd, tmp_name = tempfile.mkstemp(dir=out_path.parent, suffix=".tmp")
        fh = os.fdopen(fd, "w", encoding="utf-8", newline="\n")
    else:
        fh = out_path.open("a", encoding="utf-8", newline="\n")
    try:
        with fh:
            for label in labels:
                if label.student_hash in existing:
                    skipped += 1
                    continue
                out = chat.structured(*build_prompt(label, assignment), EssayOut)
                fh.write(_line(_essay_row(label, version, out.paragraphs)))
                fh.flush()
                written += 1
                if progress:
                    progress(f"generated {label.student_hash[:8]} ({written})")
        if tmp_name:
            os.replace(tmp_name, out_path)
            tmp_name = None
    finally:
        if tmp_name and os.path.exists(tmp_name):
            os.unlink(tmp_name)
    return GenerateReport(written, skipped)

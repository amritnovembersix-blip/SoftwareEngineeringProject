"""Synthetic labels: the evaluation ground truth (which standards each student is underdeveloped on).

Only the essay generator and the evaluator read labels; the reviewer never does.
"""
from pathlib import Path

from pydantic import BaseModel

from agentic.dataset import labels_path, read_rows


class Underdeveloped(BaseModel):
    standard: str
    standard_grade: str
    level: int
    level_label: str
    strand: str
    concept: str
    description: str


class LabelRow(BaseModel):
    student_hash: str
    salt: str
    grade: int
    meets_all_standards: bool
    underdeveloped: list[Underdeveloped]


def load_labels(data_dir: Path, version: str) -> list[LabelRow]:
    return read_rows(labels_path(data_dir, version), LabelRow)

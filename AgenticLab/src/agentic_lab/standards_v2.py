"""Reviewability catalog for synthetic data v2.

Which MLS standards can an essay show evidence for? A rule-based draft (`draft_reviewability`) is written
to `reviewability-v2.csv`, a human edits it, and `build_reviewable_xlsx` filters the corpus down to the
reviewable rows.
"""
import csv
from pathlib import Path

import openpyxl
from pydantic import BaseModel

from agentic.config import PACKAGE_ROOT
from agentic.standards.loader import StandardRow, normalize_rows, read_xlsx

# Through the installed package: the folder is agentic/ in the lab and AgenticAI/ in the team repo.
STANDARDS_DIR = PACKAGE_ROOT / "standards"
REVIEWABILITY_CSV = STANDARDS_DIR / "reviewability-v2.csv"
REVIEWABLE_XLSX = STANDARDS_DIR / "mls-ela-reviewable-v2.xlsx"
SOURCE_XLSX = STANDARDS_DIR / "curr-mls-standards-ela-2-12-sboe-2016.xlsx"

KEPT_STRANDS = {"R", "RL", "RI", "W", "L"}  # RF and SL are dropped

# (keywords, reason, sub_expectation_only); first matching rule wins. Rules flagged True ignore the MLS
# description fallback, because core word-meaning standards (RL/RI.1.B) mention dictionaries there.
_RULES: list[tuple[tuple[str, ...], str, bool]] = [
    (("legib",), "handwriting legibility is not visible in typed text", False),
    (("cursive",), "cursive handwriting is not visible in typed text", False),
    (("alphabetiz", "alphabetical order"), "alphabetical order is not shown in an essay", False),
    (("dictionary", "glossary", "thesaurus", "reference materials", "reference sources"),
     "reference-tool use is not visible in the finished text", True),
    (("keyboard",), "keyboarding skill is not visible in the finished text", False),
    (("technology",), "technology use is not visible in the finished text", False),
    (("generate a list",), "list generation is not an essay skill", False),
    (("take simple notes", "notes"), "note-taking is not part of the finished essay", False),
    (("bibliograph",), "bibliographies are not part of the essay text", False),
]


class ReviewabilityRow(BaseModel):
    code: str
    reviewable: bool
    reason: str


def draft_reviewability(rows: list[StandardRow]) -> list[ReviewabilityRow]:
    out = []
    for r in rows:
        if r.strand_code not in KEPT_STRANDS:
            continue
        text = (r.sub_expectation or r.description).lower()
        sub = (r.sub_expectation or "").lower()
        reason = next((why for words, why, sub_only in _RULES
                       if any(w in (sub if sub_only else text) for w in words)), "")
        out.append(ReviewabilityRow(code=r.code, reviewable=not reason, reason=reason))
    return out


def write_reviewability(path: Path, rows: list[ReviewabilityRow]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["code", "reviewable", "reason"])
        for r in rows:
            w.writerow([r.code, "true" if r.reviewable else "false", r.reason])


def read_reviewability(path: Path) -> dict[str, ReviewabilityRow]:
    flags = {"true": True, "false": False}
    rows = []
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for d in reader:
            flag = flags.get(d["reviewable"].strip().lower())
            if flag is None:
                raise ValueError(f"{path.name} line {reader.line_num}: code {d['code']}: "
                                 "reviewable must be true or false")
            rows.append(ReviewabilityRow(code=d["code"], reviewable=flag, reason=d["reason"]))
    return {r.code: r for r in rows}


def reviewable_codes(path: Path) -> set[str]:
    return {c for c, r in read_reviewability(path).items() if r.reviewable}


def build_reviewable_xlsx(source: Path, csv_path: Path, out: Path) -> int:
    """Copy the source header and every row whose normalized code is reviewable; returns rows written."""
    catalog = read_reviewability(csv_path)
    keep = {c for c, r in catalog.items() if r.reviewable}
    for row in normalize_rows(read_xlsx(source)):
        if row.strand_code in KEPT_STRANDS and row.code not in catalog:
            raise ValueError(f"{csv_path.name} is missing code {row.code}")
    src = openpyxl.load_workbook(source, read_only=True, data_only=True)
    try:
        raw = list(src.worksheets[0].iter_rows(values_only=True))
    finally:
        src.close()
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    ws.append(raw[0])
    written = 0
    for values in raw[1:]:
        header = [str(h).strip() if h is not None else f"_col{i}" for i, h in enumerate(raw[0])]
        parsed = normalize_rows([dict(zip(header, values))])
        if parsed and parsed[0].code in keep:
            ws.append(values)
            written += 1
    wb.save(out)
    return written

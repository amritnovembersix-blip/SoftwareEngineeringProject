"""Standards loader: read the spreadsheet, normalize, upsert into Postgres, embed new rows.

Documented exception to the "no psycopg outside Adapters" rule: this module talks to the
database directly (it is only used by wiring/CLI code, never by review/).
"""
import re
from dataclasses import dataclass
from pathlib import Path

import openpyxl

from agentic.llm.base import Embedder


@dataclass(frozen=True)
class StandardRow:
    code: str
    grade: str
    grade_min: int
    grade_max: int
    strand_code: str
    strand: str
    big_idea: str | None
    concept: str | None
    description: str
    sub_expectation: str | None


@dataclass(frozen=True)
class LoadReport:
    upserted: int
    deleted: int
    embedded: int


def read_xlsx(path: Path) -> list[dict[str, object]]:
    """First sheet; the first row is the header. Blank headers get unique placeholder keys."""
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        rows = wb.worksheets[0].iter_rows(values_only=True)
        header_row = next(rows, None)
        if header_row is None:
            return []
        keys = [str(h).strip() if h is not None and str(h).strip() else f"_col{i}"
                for i, h in enumerate(header_row)]
        return [dict(zip(keys, row)) for row in rows]
    finally:
        wb.close()


_BAND = re.compile(r"^(\d{1,2}) to (\d{1,2})$")
_SINGLE = re.compile(r"^\d{1,2}$")


def grade_range(label: str) -> tuple[int, int]:
    text = label.strip()
    if _SINGLE.match(text):
        n = int(text)
        return n, n
    m = _BAND.match(text)
    if m:
        lo, hi = int(m.group(1)), int(m.group(2))
        if lo <= hi:
            return lo, hi
    raise ValueError(f"unknown grade label: {label!r}")


def _clean(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return str(value).replace("\xa0", " ").strip()


def normalize_rows(raw: list[dict]) -> list[StandardRow]:
    out: list[StandardRow] = []
    for r in raw:
        code = _clean(r.get("MLS Code"))
        if not code:
            continue
        grade = _clean(r.get("Grade"))
        lo, hi = grade_range(grade)
        strand = _clean(r.get("Strand Description"))
        if strand == "Speaking/Listening":
            strand = "Speaking and Listening"
        out.append(StandardRow(
            code=code,
            grade=grade,
            grade_min=lo,
            grade_max=hi,
            strand_code=_clean(r.get("Strand")),
            strand=strand,
            big_idea=_clean(r.get("Theme/Big Idea Description")) or None,
            concept=_clean(r.get("Concept Description")) or None,
            description=_clean(r.get("MLS Description")),
            sub_expectation=_clean(r.get("Sub expectation")) or None,
        ))
    return out


def embed_text(row: StandardRow) -> str:
    return f"{row.strand} · {row.concept}: {row.description} {row.sub_expectation or ''}".strip()


_UPSERT = """
INSERT INTO standard (code, grade, grade_min, grade_max, strand_code, strand, big_idea, concept,
                      description, sub_expectation)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (code) DO UPDATE SET
  embedding = CASE WHEN (standard.strand, standard.concept, standard.description, standard.sub_expectation)
                        IS NOT DISTINCT FROM
                        (EXCLUDED.strand, EXCLUDED.concept, EXCLUDED.description, EXCLUDED.sub_expectation)
                   THEN standard.embedding ELSE NULL END,
  grade = EXCLUDED.grade, grade_min = EXCLUDED.grade_min, grade_max = EXCLUDED.grade_max,
  strand_code = EXCLUDED.strand_code, strand = EXCLUDED.strand, big_idea = EXCLUDED.big_idea,
  concept = EXCLUDED.concept, description = EXCLUDED.description,
  sub_expectation = EXCLUDED.sub_expectation
"""


def load(conn, rows: list[StandardRow], embedder: Embedder, batch: int = 50) -> LoadReport:
    by_code = {r.code: r for r in rows}
    with conn.transaction():
        with conn.cursor() as cur:
            codes = list(by_code)
            cur.execute("SELECT code FROM standard WHERE NOT (code = ANY(%s))", (codes,))
            stale = [c for (c,) in cur.fetchall()]
            if stale:
                cur.execute(
                    "SELECT DISTINCT s.code FROM observation o JOIN standard s ON s.id = o.standard_id "
                    "WHERE s.code = ANY(%s) ORDER BY s.code", (stale,))
                referenced = [c for (c,) in cur.fetchall()]
                if referenced:
                    raise ValueError(
                        "refusing to delete standards referenced by observations: "
                        + ", ".join(referenced))
                cur.execute("DELETE FROM standard WHERE code = ANY(%s)", (stale,))
            for r in by_code.values():
                cur.execute(_UPSERT, (r.code, r.grade, r.grade_min, r.grade_max, r.strand_code,
                                      r.strand, r.big_idea, r.concept, r.description,
                                      r.sub_expectation))
            cur.execute("SELECT code FROM standard WHERE embedding IS NULL ORDER BY code")
            todo = [c for (c,) in cur.fetchall() if c in by_code]
            for i in range(0, len(todo), batch):
                chunk = todo[i:i + batch]
                vectors = embedder.embed([embed_text(by_code[c]) for c in chunk], "document")
                for c, v in zip(chunk, vectors):
                    cur.execute("UPDATE standard SET embedding = %s::vector WHERE code = %s",
                                (str(list(v)), c))
    return LoadReport(upserted=len(by_code), deleted=len(stale), embedded=len(todo))

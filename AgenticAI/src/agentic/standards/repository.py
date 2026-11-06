"""Postgres Adapter for standards lookup (at_grade, in_range, search)."""
from pgvector.psycopg import register_vector

from agentic.models import StandardRef

_COLS = "code, coalesce(concept, ''), description, grade_min, grade_max, strand_code, sub_expectation"


def _ref(row) -> StandardRef:
    return StandardRef(code=row[0], concept=row[1], description=row[2],
                       grade_min=row[3], grade_max=row[4], strand=row[5],
                       sub_expectation=row[6])


class PostgresStandards:
    def __init__(self, conn):
        self._conn = conn
        register_vector(conn)

    def at_grade(self, grade: int) -> list[StandardRef]:
        with self._conn.cursor() as cur:
            cur.execute(f"SELECT {_COLS} FROM standards_at_grade(%s) ORDER BY code", (grade,))
            return [_ref(r) for r in cur.fetchall()]

    def in_range(self, lo: int, hi: int) -> list[StandardRef]:
        with self._conn.cursor() as cur:
            cur.execute(f"SELECT {_COLS} FROM standard WHERE grade_min >= %s AND grade_max <= %s "
                        "ORDER BY code", (lo, hi))
            return [_ref(r) for r in cur.fetchall()]

    def search(self, query_embedding: list[float], grade: int, k: int) -> list[StandardRef]:
        with self._conn.cursor() as cur:
            cur.execute(
                f"SELECT {_COLS} FROM standards_at_grade(%s) WHERE embedding IS NOT NULL "
                "ORDER BY embedding <=> %s::vector LIMIT %s",
                (grade, str(list(query_embedding)), k))
            return [_ref(r) for r in cur.fetchall()]

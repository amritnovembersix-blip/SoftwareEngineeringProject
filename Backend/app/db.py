"""Database access: one small helper every endpoint uses.

The tables are created by AgenticAI/db/migrations and filled by Justin's `python -m agentic`
commands. The backend reads them, and writes only its own tables (such as app_user).
"""
import psycopg
from psycopg.rows import dict_row

from app.config import settings


def query(sql: str, params: tuple = ()) -> list[dict]:
    """Run a SELECT and return the rows as dictionaries (FastAPI turns them into JSON)."""
    with psycopg.connect(settings.database_url, row_factory=dict_row) as conn:
        return conn.execute(sql, params).fetchall()


def execute(sql: str, params: tuple = ()) -> list[dict]:
    """Run an INSERT/UPDATE and commit. Returns any rows from a RETURNING clause."""
    with psycopg.connect(settings.database_url, row_factory=dict_row) as conn:
        cur = conn.execute(sql, params)
        rows = cur.fetchall() if cur.description else []
        conn.commit()
        return rows

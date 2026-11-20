"""Database access: one small helper every endpoint uses.

The tables are created by AgenticAI/db/migrations and filled by Justin's `python -m agentic`
commands. The backend reads them, and writes only its own tables (such as app_user).
"""
from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row

from app.config import settings


class DatabaseUnavailable(Exception):
    """The database is down, unreachable, or too slow. main.py turns this into a 503."""


@contextmanager
def _connect():
    """Open a connection with time limits, so a dead or stuck database fails fast."""
    try:
        with psycopg.connect(
            settings.database_url,
            row_factory=dict_row,
            connect_timeout=settings.db_connect_timeout,
            options=f"-c statement_timeout={settings.db_statement_timeout_ms}",
        ) as conn:
            yield conn
    except psycopg.errors.QueryCanceled:
        raise DatabaseUnavailable("the database took too long to answer") from None
    except psycopg.OperationalError:
        raise DatabaseUnavailable("the database is not reachable") from None


def ping() -> bool:
    """True if the database answers a trivial query. Used by /health."""
    try:
        query("SELECT 1")
        return True
    except DatabaseUnavailable:
        return False


def query(sql: str, params: tuple = ()) -> list[dict]:
    """Run a SELECT and return the rows as dictionaries (FastAPI turns them into JSON)."""
    with _connect() as conn:
        return conn.execute(sql, params).fetchall()


def execute(sql: str, params: tuple = ()) -> list[dict]:
    """Run an INSERT/UPDATE and commit. Returns any rows from a RETURNING clause."""
    with _connect() as conn:
        cur = conn.execute(sql, params)
        rows = cur.fetchall() if cur.description else []
        conn.commit()
        return rows

import psycopg
import pytest
from pgvector.psycopg import register_vector

from agentic.config import Settings


def pytest_configure(config):
    config.addinivalue_line("markers", "db: needs local Postgres")
    config.addinivalue_line("markers", "live: calls real external APIs")


def _db_unavailable_reason() -> str | None:
    try:
        import psycopg

        url = Settings.load().database_url_local
        with psycopg.connect(url, connect_timeout=3):
            pass
    except Exception as exc:  # noqa: BLE001
        return f"local Postgres unavailable ({type(exc).__name__})"
    return None


def pytest_collection_modifyitems(config, items):
    db_items = [i for i in items if "db" in i.keywords]
    if not db_items:
        return
    reason = _db_unavailable_reason()
    if reason:
        skip = pytest.mark.skip(reason=reason)
        for item in db_items:
            item.add_marker(skip)


@pytest.fixture
def db_conn():
    """Local Postgres connection inside one outer transaction that is always rolled back.

    The outer block matters: on an idle connection psycopg's `with conn.transaction():` starts a
    real transaction and COMMITS it. Inside this block, the code's transaction() calls become
    savepoints instead, so tests leave no data (see test_db_isolation.py).
    """
    conn = psycopg.connect(Settings.load().database_url_local)
    register_vector(conn)
    try:
        with conn.transaction(force_rollback=True):
            yield conn
    finally:
        conn.close()

"""The db_conn fixture must keep everything a test writes invisible to other connections."""
import psycopg
import pytest

from agentic.config import Settings

pytestmark = pytest.mark.db


def test_transaction_blocks_inside_db_conn_are_not_committed(db_conn):
    with db_conn.transaction():  # the pattern the loader, seeder and writer use
        db_conn.execute("INSERT INTO teacher (id, name) VALUES ('isolation-probe', 'p')")
    with psycopg.connect(Settings.load().database_url_local, autocommit=True) as other:
        seen = other.execute("SELECT count(*) FROM teacher WHERE id = 'isolation-probe'").fetchone()[0]
        if seen:  # clean up the leak so a failing run doesn't poison the dev DB
            other.execute("DELETE FROM teacher WHERE id = 'isolation-probe'")
    assert seen == 0

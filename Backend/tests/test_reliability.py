"""What the backend does when its dependencies fail (task 1 of the team plan).

Before this change a dead database gave a bare "500 Internal Server Error" and /health still
said "ok". Now: a clear, retryable 503 within the time limits, and a /health that tells the truth.
"""
import time

import pytest
from fastapi.testclient import TestClient

from app import agentic_client, db
from app.config import settings
from app.main import app

client = TestClient(app, raise_server_exceptions=False)

# Nothing listens on this port, so connecting fails straight away.
DEAD_DB = "postgresql://sass:change-me@127.0.0.1:5499/sass"


@pytest.fixture
def database_down(monkeypatch):
    monkeypatch.setattr(settings, "database_url", DEAD_DB)


# ---- the database is down -------------------------------------------------------------

@pytest.mark.parametrize("method, path", [
    ("GET", "/classrooms"),
    ("GET", "/classrooms/classroom_1.01/students"),
    ("GET", "/students/someone/findings"),
    ("POST", "/classrooms/classroom_1.01/reviews"),
])
def test_database_down_is_a_clear_503(database_down, method, path):
    r = client.request(method, path)
    assert r.status_code == 503
    assert r.json() == {"detail": "the database is not reachable"}
    assert r.headers["retry-after"] == "5"


def test_login_with_database_down_is_503_not_500(database_down):
    r = client.post("/auth/login", json={"email": "a@b.test", "password": "x"})
    assert r.status_code == 503


def test_unreachable_database_fails_within_the_connect_timeout(monkeypatch):
    # 10.255.255.1 is a non-routable address: without a timeout the connect can hang for minutes.
    monkeypatch.setattr(settings, "database_url", "postgresql://sass:x@10.255.255.1:5432/sass")
    monkeypatch.setattr(settings, "db_connect_timeout", 1)
    start = time.monotonic()
    with pytest.raises(db.DatabaseUnavailable):
        db.query("SELECT 1")
    assert time.monotonic() - start < 5


# ---- the database is up but a query is too slow ---------------------------------------

needs_db = pytest.mark.skipif(not db.ping(), reason="needs the Postgres from AgenticAI/")


@needs_db
def test_slow_query_is_cut_off_by_the_statement_timeout(monkeypatch):
    monkeypatch.setattr(settings, "db_statement_timeout_ms", 200)
    start = time.monotonic()
    with pytest.raises(db.DatabaseUnavailable, match="too long"):
        db.query("SELECT pg_sleep(3)")
    assert time.monotonic() - start < 2


# ---- /health tells the truth ----------------------------------------------------------

@pytest.mark.parametrize("database, agentic, code, status", [
    (True, True, 200, "ok"),
    (True, False, 200, "degraded"),   # pages still work; only "Review class" fails
    (False, True, 503, "down"),       # nothing works without the database
    (False, False, 503, "down"),
])
def test_health_reports_each_dependency(monkeypatch, database, agentic, code, status):
    monkeypatch.setattr(db, "ping", lambda: database)
    monkeypatch.setattr(agentic_client, "is_up", lambda: agentic)
    r = client.get("/health")
    assert r.status_code == code
    assert r.json() == {
        "status": status,
        "database": "ok" if database else "down",
        "agentic": "ok" if agentic else "down",
    }


def test_health_really_checks_the_database(database_down, monkeypatch):
    # No fakes for the database here: a real failed connection must show up as "down".
    monkeypatch.setattr(agentic_client, "is_up", lambda: True)
    r = client.get("/health")
    assert r.status_code == 503
    assert r.json()["database"] == "down"


def test_agentic_probe_is_false_when_nothing_listens(monkeypatch):
    monkeypatch.setattr(settings, "agentic_base_url", "http://127.0.0.1:8999")
    assert agentic_client.is_up() is False

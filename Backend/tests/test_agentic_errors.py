
"""Test backend responses when the AgenticAI service has problems."""

import httpx
import pytest
from fastapi.testclient import TestClient

from app import agentic_client
from app.main import app

client = TestClient(app)


def test_agentic_service_unavailable(monkeypatch):
    """A connection failure should return 503."""

    def fake_request(*args, **kwargs):
        raise httpx.ConnectError("Service unavailable")

    monkeypatch.setattr(httpx, "request", fake_request)

    with pytest.raises(agentic_client.AgenticError) as exc:
        agentic_client.get_run("test-run")

    assert exc.value.status == 503


def test_agentic_invalid_token(monkeypatch):
    """An authentication failure should return 502."""

    def fake_request(*args, **kwargs):
        return httpx.Response(401)

    monkeypatch.setattr(httpx, "request", fake_request)

    with pytest.raises(agentic_client.AgenticError) as exc:
        agentic_client.get_run("test-run")

    assert exc.value.status == 502


def test_agentic_unknown_run(monkeypatch):
    """An unknown review run should return 404."""

    def fake_request(*args, **kwargs):
        return httpx.Response(404)

    monkeypatch.setattr(httpx, "request", fake_request)

    with pytest.raises(agentic_client.AgenticError) as exc:
        agentic_client.get_run("unknown-run")

    assert exc.value.status == 404


def test_agentic_server_error(monkeypatch):
    """An AgenticAI server error should return 502."""

    def fake_request(*args, **kwargs):
        return httpx.Response(500)

    monkeypatch.setattr(httpx, "request", fake_request)

    with pytest.raises(agentic_client.AgenticError) as exc:
        agentic_client.get_run("test-run")

    assert exc.value.status == 502


def test_review_progress_reports_failure(monkeypatch):
    """A failed review should remain visibly failed in the API."""

    def fake_get_run(run_id):
        return {
            "run_id": run_id,
            "status": "failed",
            "submission_count": 10,
            "completed_count": 4,
            "skipped_count": 1,
            "error": "Review failed",
        }

    monkeypatch.setattr(agentic_client, "get_run", fake_get_run)

    response = client.get("/reviews/test-run")

    assert response.status_code == 200
    assert response.json()["status"] == "failed"
    assert response.json()["done"] == 5
    assert response.json()["total"] == 10
    assert response.json()["error"] == "Review failed"

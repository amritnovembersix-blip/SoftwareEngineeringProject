"""Talks to the agentic service (AgenticAI/, port 8000).

The only place that knows the agentic service's address and secret token. The token never
leaves the backend, so the browser can't call the agentic service directly.
"""
import httpx

from app.config import settings


class AgenticError(Exception):
    """The agentic service refused the call or couldn't be reached."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


def _call(method: str, path: str, json: dict | None = None) -> dict:
    headers = {"Authorization": f"Bearer {settings.agentic_api_token}"}
    try:
        resp = httpx.request(method, settings.agentic_base_url.rstrip("/") + path,
                             json=json, headers=headers, timeout=30.0)
    except httpx.TransportError:
        raise AgenticError(503, "the agentic service is not reachable") from None
    if resp.status_code == 401:
        raise AgenticError(502, "the agentic service rejected our token (check AGENTIC_API_TOKEN)")
    if resp.status_code == 404:
        raise AgenticError(404, "no such review run (runs are lost when the agentic service restarts)")
    if resp.status_code == 422:
        raise AgenticError(404, "the agentic service doesn't know this classroom")
    if resp.status_code >= 400:
        raise AgenticError(502, f"the agentic service returned {resp.status_code}")
    return resp.json()


def start_sweep(course_id: str) -> dict:
    """POST /sweeps: review every essay in the class in the background. Returns the run."""
    return _call("POST", "/sweeps", {"course_id": course_id})


def get_run(run_id: str) -> dict:
    """GET /runs/{run_id}: the run's status and counts."""
    return _call("GET", f"/runs/{run_id}")

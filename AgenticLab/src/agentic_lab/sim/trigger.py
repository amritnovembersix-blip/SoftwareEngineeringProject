"""Sends grading events to the agentic service and reads its run records."""
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

import httpx


class TriggerError(Exception):
    """A failed call. The message holds only ids and the status, never a response body or a URL."""

    def __init__(self, message: str, assignment_id: str | None = None, student_hash: str | None = None,
                 status: int | None = None):
        super().__init__(message)
        self.assignment_id = assignment_id
        self.student_hash = student_hash
        self.status = status


@dataclass(frozen=True)
class GradedEvent:
    """A SubmissionGradedEvent (contracts/mock-lms-api.yaml): ids only, never student text."""

    submission_id: str
    assignment_id: str
    course_id: str
    user_id: str
    attempt: int
    event_time: datetime

    def payload(self) -> dict:
        utc = self.event_time.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        return {"metadata": {"event_name": "submission_graded", "event_time": utc},
                "body": {"submission_id": self.submission_id, "assignment_id": self.assignment_id,
                         "course_id": self.course_id, "user_id": self.user_id, "attempt": self.attempt}}


class ReviewTrigger(Protocol):
    """Target: what the simulator needs to start one review."""

    def send(self, event: GradedEvent) -> str:
        """Start the review; returns the run id."""
        ...


class WebhookTrigger:
    """Adapter: POSTs the event to the agentic service's webhook (the Adaptee)."""

    def __init__(self, base_url: str, secret: str, client: httpx.Client | None = None):
        self._client = client or httpx.Client(base_url=base_url, timeout=30.0)
        self._secret = secret

    def send(self, event: GradedEvent) -> str:
        who = {"assignment_id": event.assignment_id, "student_hash": event.user_id}
        label = f"assignment {event.assignment_id}, student {event.user_id}"
        try:
            resp = self._client.post("/events/submission-graded", json=event.payload(),
                                     headers={"X-SASS-Webhook-Secret": self._secret})
        except httpx.HTTPError:
            raise TriggerError(f"event not delivered for {label}", **who) from None
        if resp.status_code != 202:
            raise TriggerError(f"event rejected for {label} (status {resp.status_code})", **who,
                               status=resp.status_code)
        try:
            body = resp.json()
        except ValueError:
            body = {}
        if body.get("ignored"):
            raise TriggerError(f"event ignored for {label}: {body.get('reason') or 'mode_disabled'}", **who,
                               status=202)
        if not body.get("run_id"):
            raise TriggerError(f"no run id returned for {label}", **who, status=202)
        return body["run_id"]  # a duplicate carries the existing run's id


class RunsClient:
    """Reads run records (GET /runs/{id}) from the agentic service."""

    def __init__(self, base_url: str, token: str, client: httpx.Client | None = None):
        self._client = client or httpx.Client(base_url=base_url, timeout=30.0)
        self._headers = {"Authorization": f"Bearer {token}"}

    def get(self, run_id: str) -> dict | None:
        """The run record, or None when the service has no such run (for example after a restart)."""
        try:
            resp = self._client.get(f"/runs/{run_id}", headers=self._headers)
        except httpx.HTTPError:
            raise TriggerError("run status not fetched") from None
        if resp.status_code == 404:
            return None
        if resp.status_code != 200:
            raise TriggerError(f"run status rejected (status {resp.status_code})", status=resp.status_code)
        try:
            return resp.json()
        except ValueError:
            raise TriggerError("run status unreadable", status=200) from None

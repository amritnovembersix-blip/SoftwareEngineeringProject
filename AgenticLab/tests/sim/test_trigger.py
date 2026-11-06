import json
from datetime import datetime, timezone

import httpx
import pytest

from agentic_lab.sim.trigger import GradedEvent, RunsClient, TriggerError, WebhookTrigger

EVENT = GradedEvent(submission_id="s1", assignment_id="ela9v2-a01@v2", course_id="classroom_2.00-control",
                    user_id="abc123", attempt=1, event_time=datetime(2027, 1, 18, 14, 0, tzinfo=timezone.utc))
BASE = "http://agentic.test"


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler), base_url=BASE)


def test_payload_matches_contract_shape():
    assert EVENT.payload() == {
        "metadata": {"event_name": "submission_graded", "event_time": "2027-01-18T14:00:00Z"},
        "body": {"submission_id": "s1", "assignment_id": "ela9v2-a01@v2", "course_id": "classroom_2.00-control",
                 "user_id": "abc123", "attempt": 1}}


def test_send_posts_payload_with_secret_and_returns_run_id():
    seen = {}

    def handler(request):
        seen["path"], seen["secret"] = request.url.path, request.headers["X-SASS-Webhook-Secret"]
        seen["json"] = json.loads(request.content)
        return httpx.Response(202, json={"run_id": "r1", "duplicate": False, "ignored": False})

    assert WebhookTrigger(BASE, "shh", _client(handler)).send(EVENT) == "r1"
    assert seen["path"] == "/events/submission-graded" and seen["secret"] == "shh"
    assert seen["json"] == EVENT.payload()


def test_duplicate_returns_existing_run_id():
    t = WebhookTrigger(BASE, "s", _client(lambda r: httpx.Response(
        202, json={"run_id": "r0", "duplicate": True, "ignored": False})))
    assert t.send(EVENT) == "r0"


def test_ignored_raises_with_mode_disabled():
    t = WebhookTrigger(BASE, "s", _client(lambda r: httpx.Response(
        202, json={"run_id": None, "duplicate": False, "ignored": True, "reason": "mode_disabled"})))
    with pytest.raises(TriggerError) as e:
        t.send(EVENT)
    assert e.value.status == 202 and "mode_disabled" in str(e.value)


def test_401_names_ids_but_not_body():
    t = WebhookTrigger(BASE, "s", _client(lambda r: httpx.Response(401, text="SECRET-BODY-TEXT")))
    with pytest.raises(TriggerError) as e:
        t.send(EVENT)
    msg = str(e.value)
    assert "ela9v2-a01@v2" in msg and "abc123" in msg and "401" in msg and "SECRET-BODY-TEXT" not in msg
    assert (e.value.assignment_id, e.value.student_hash, e.value.status) == ("ela9v2-a01@v2", "abc123", 401)


def test_transport_error_hides_url():
    def boom(request):
        raise httpx.ConnectError("cannot reach http://agentic.test/events/submission-graded")

    with pytest.raises(TriggerError) as e:
        WebhookTrigger(BASE, "s", _client(boom)).send(EVENT)
    assert "agentic.test" not in str(e.value) and e.value.status is None
    assert e.value.__cause__ is None and e.value.__suppress_context__


def test_runs_client_get_sends_bearer_and_returns_json():
    seen = {}

    def handler(request):
        seen["auth"], seen["path"] = request.headers["Authorization"], request.url.path
        return httpx.Response(200, json={"run_id": "r1", "status": "succeeded"})

    assert RunsClient(BASE, "tok", _client(handler)).get("r1") == {"run_id": "r1", "status": "succeeded"}
    assert seen == {"auth": "Bearer tok", "path": "/runs/r1"}


def test_runs_client_404_is_none():
    assert RunsClient(BASE, "t", _client(lambda r: httpx.Response(404))).get("gone") is None


def test_runs_client_other_errors_carry_no_ids():
    with pytest.raises(TriggerError) as e:
        RunsClient(BASE, "t", _client(lambda r: httpx.Response(500, text="oops"))).get("r1")
    assert e.value.assignment_id is None and e.value.student_hash is None and e.value.status == 500
    assert "oops" not in str(e.value)

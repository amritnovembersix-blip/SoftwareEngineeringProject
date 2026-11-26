"""Disagree and dismiss need a reason ("dismiss with a reason"); agree doesn't. No database needed:
the finding lookup and the insert are stubbed."""
import uuid

import pytest
from fastapi.testclient import TestClient

from app.auth import CurrentUser, current_user
from app.main import app
from app.routers import findings

TEACHER = CurrentUser(id=1, email="t@sass.test", role="teacher", teacher_id="t-1", display_name="T")
FID = str(uuid.uuid4())
client = TestClient(app)


@pytest.fixture
def saved(monkeypatch):
    rows = []
    monkeypatch.setattr(findings.db, "query", lambda sql, params=(): [{"classroom_id": "c-1"}])
    monkeypatch.setattr(findings, "require_classroom", lambda user, classroom_id: None)

    def insert(sql, params):
        fid, teacher, stance, reason = params
        rows.append({"stance": stance, "reason": reason})
        return [{"id": uuid.uuid4(), "finding_id": fid, "stance": stance, "reason": reason,
                 "responded_at": "2026-11-25T00:00:00Z"}]
    monkeypatch.setattr(findings.db, "execute", insert)
    app.dependency_overrides[current_user] = lambda: TEACHER
    yield rows
    app.dependency_overrides.pop(current_user, None)


@pytest.mark.parametrize("body", [{"stance": "disagree"}, {"stance": "dismiss"},
                                  {"stance": "dismiss", "reason": "   "}, {"stance": "disagree", "reason": None}])
def test_disagree_or_dismiss_without_a_reason_is_422(saved, body):
    r = client.post(f"/findings/{FID}/responses", json=body)
    assert r.status_code == 422
    assert "reason is required" in r.json()["detail"]
    assert saved == []


@pytest.mark.parametrize("body, reason", [({"stance": "agree"}, None),
                                          ({"stance": "agree", "reason": " ok "}, "ok"),
                                          ({"stance": "dismiss", "reason": " Quoted dialogue. "}, "Quoted dialogue."),
                                          ({"stance": "disagree", "reason": "Clear thesis."}, "Clear thesis.")])
def test_a_reason_given_or_not_needed_is_saved(saved, body, reason):
    r = client.post(f"/findings/{FID}/responses", json=body)
    assert r.status_code == 201
    assert saved == [{"stance": body["stance"], "reason": reason}]

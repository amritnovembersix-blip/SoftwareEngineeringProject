import pytest
from fakes import essay_row, label_row, write_essays, write_labels
from fastapi.testclient import TestClient
from test_app import AUTH, ESSAY, MemoryWriter

from agentic.config import Settings
from agentic.service.app import create_app


@pytest.fixture
def client(tmp_path):
    s = Settings(fake_llm=True, data_dir=tmp_path, agentic_api_token="test-token", webhook_secret="x")
    return TestClient(create_app(s))


def test_get_lists_every_key_as_defaults(client):
    r = client.get("/settings", headers=AUTH)
    assert r.status_code == 200
    rows = r.json()
    assert [x["key"] for x in rows] == ["mc_finding_min_picks", "root_cause_enabled",
                                        "notable_strengths_enabled", "extension_check_enabled",
                                        "attention_hits", "attention_window", "secure_hits", "secure_window",
                                        "trend_window", "review_flip_rate", "group_min_size", "max_tutees"]
    assert {x["source"] for x in rows} == {"default"}
    first = rows[0]
    assert first["value"] == 2 and first["description"]


def test_put_overrides_and_history_has_the_row(client):
    r = client.put("/settings/mc_finding_min_picks", json={"value": 3, "changed_by": "t1"}, headers=AUTH)
    assert r.status_code == 200
    assert r.json()["value"] == 3 and r.json()["source"] == "override"
    got = {x["key"]: x for x in client.get("/settings", headers=AUTH).json()}["mc_finding_min_picks"]
    assert (got["value"], got["source"]) == (3, "override")
    hist = client.get("/settings/mc_finding_min_picks/history", headers=AUTH).json()
    assert len(hist) == 1
    assert (hist[0]["value"], hist[0]["changed_by"]) == (3, "t1") and hist[0]["changed_at"]


def test_history_is_newest_first(client):
    for v in (3, 4):
        client.put("/settings/mc_finding_min_picks", json={"value": v, "changed_by": "t1"}, headers=AUTH)
    hist = client.get("/settings/mc_finding_min_picks/history", headers=AUTH).json()
    assert [h["value"] for h in hist] == [4, 3]


def test_invalid_value_is_422_with_the_field(client):
    r = client.put("/settings/mc_finding_min_picks", json={"value": 0, "changed_by": "t1"}, headers=AUTH)
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["code"] == "validation_failed" and err["details"]["fields"] == ["mc_finding_min_picks"]
    assert client.get("/settings/mc_finding_min_picks/history", headers=AUTH).json() == []


def test_unavailable_setting_is_422(client):
    r = client.put("/settings/extension_check_enabled", json={"value": True, "changed_by": "t1"}, headers=AUTH)
    assert r.status_code == 422 and "not available" in r.json()["error"]["message"]


def test_unknown_key_is_404(client):
    r = client.put("/settings/nope", json={"value": 1, "changed_by": "t1"}, headers=AUTH)
    assert r.status_code == 404
    assert client.get("/settings/nope/history", headers=AUTH).status_code == 404


def test_missing_changed_by_is_422(client):
    r = client.put("/settings/mc_finding_min_picks", json={"value": 3}, headers=AUTH)
    assert r.status_code == 422


def test_settings_need_the_service_token(client):
    assert client.get("/settings").status_code == 401
    assert client.put("/settings/mc_finding_min_picks", json={"value": 3, "changed_by": "x"}).status_code == 401
    assert client.get("/settings/mc_finding_min_picks/history").status_code == 401


def test_settings_change_mid_sweep_keeps_started_reviews_snapshot(tmp_path):
    students = ["a" * 64, "b" * 64]
    write_labels(tmp_path, "1.01", [label_row(student_hash=h, grade=9) for h in students])
    write_essays(tmp_path, "1.01", [essay_row(
        student_hash=h, paragraphs=[ESSAY, "Second para.", "Third para."], course_id="classroom_1.01",
        assignment_id="asg-classroom_1.01-essay1", submission_id=f"sub-{h[0]}") for h in students])
    s = Settings(fake_llm=True, data_dir=tmp_path, agentic_api_token="test-token", webhook_secret="x")
    holder = {}

    class ChangingWriter(MemoryWriter):
        def save(self, result, trigger="manual"):
            if not self.saved:  # the first review is done; a teacher changes a setting mid-sweep
                holder["app"].state.settings_store.append("mc_finding_min_picks", 5, "t1")
            return super().save(result, trigger)

    writer = ChangingWriter()
    app = create_app(s, writer_factory=lambda _s: writer)
    holder["app"] = app
    client = TestClient(app)
    r = client.post("/sweeps", json={"course_id": "classroom_1.01"}, headers=AUTH)
    assert client.get(f"/runs/{r.json()['run_id']}", headers=AUTH).json()["completed_count"] == 2
    first, second = (res for res, _ in writer.saved)
    assert first.settings_hash and second.settings_hash
    assert first.settings_hash != second.settings_hash  # the second review started after the change


def test_float_setting_put_round_trips(client):
    r = client.put("/settings/review_flip_rate", json={"value": 0.2, "changed_by": "t1"}, headers=AUTH)
    assert r.status_code == 200 and r.json()["value"] == 0.2


def test_cross_key_violation_is_422_naming_both_keys_and_changes_nothing(client):
    r = client.put("/settings/attention_hits", json={"value": 4, "changed_by": "t1"}, headers=AUTH)
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["code"] == "validation_failed"
    assert "attention_hits" in err["message"] and "attention_window" in err["message"]
    assert set(err["details"]["fields"]) == {"attention_hits", "attention_window"}
    assert client.get("/settings/attention_hits/history", headers=AUTH).json() == []


def test_raising_the_window_first_then_the_hits_is_accepted(client):
    assert client.put("/settings/attention_window", json={"value": 5, "changed_by": "t1"}, headers=AUTH).status_code == 200
    assert client.put("/settings/attention_hits", json={"value": 4, "changed_by": "t1"}, headers=AUTH).status_code == 200

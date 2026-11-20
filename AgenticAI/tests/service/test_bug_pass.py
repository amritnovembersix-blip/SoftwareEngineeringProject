"""Regression tests for docs/superpowers/reports/2026-11-11-agentic-bug-report.md (#1, #4, #5, #6, #8, #9, #13)."""
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fakes import essay_row, label_row, write_essays, write_labels
from fastapi.testclient import TestClient
from test_app import AUTH, ESSAY, MemoryWriter

from agentic import dataset, wiring
from agentic.config import Settings
from agentic.lms.base import Course, Submission
from agentic.models import SubmissionRef
from agentic.review.nodes import make_fetch
from agentic.service.app import create_app

STUDENTS = ["a" * 64, "b" * 64, "c" * 64]
REPO = Path(__file__).resolve().parents[3]


def _classroom(data, version, students):
    write_labels(data, version, [label_row(student_hash=h, grade=9) for h in students])
    write_essays(data, version, [essay_row(
        student_hash=h, paragraphs=[ESSAY, "Second para.", "Third para."], course_id=f"classroom_{version}",
        assignment_id=f"asg-classroom_{version}-essay1", submission_id=f"sub-{version}-{h[0]}")
        for h in students])


@pytest.fixture
def env(tmp_path):
    _classroom(tmp_path, "1.01", STUDENTS)
    s = Settings(fake_llm=True, data_dir=tmp_path, agentic_api_token="test-token", webhook_secret="x")
    writer = MemoryWriter()
    return TestClient(create_app(s, writer_factory=lambda _s: writer)), writer, tmp_path


def _post(client, body):
    return client.post("/sweeps", json=body, headers=AUTH)


# --- #1: the result names the attempt that was actually reviewed -------------------------------------

class _LatestIsAttempt2:
    def get_course(self, course_id):
        return Course(id=course_id, name="c", subject="ela", grade_level=9)

    def get_submission(self, course_id, assignment_id, user_id):
        return Submission(id="sub-1", user_id=user_id, assignment_id=assignment_id, attempt=2, body="<p>x</p>",
                          submitted_at=datetime(2026, 11, 2, tzinfo=timezone.utc))

    def get_assignment(self, course_id, assignment_id):
        raise AssertionError("not needed")


def test_fetch_labels_the_result_with_the_attempt_it_reviewed():
    ref = SubmissionRef(course_id="c", assignment_id="a", user_id="u", submission_id="sub-1", attempt=1)
    out = make_fetch(_LatestIsAttempt2(), use_context=False)({"submission_ref": ref})
    assert (out["submission_ref"].submission_id, out["submission_ref"].attempt) == ("sub-1", 2)
    assert out["submission_ref"].submitted_at == datetime(2026, 11, 2, tzinfo=timezone.utc)


# --- #5 and #13: empty or blank ids are rejected, not read as "everything" -----------------------------

@pytest.mark.parametrize("body", [
    {"course_id": ""}, {"course_id": "   "}, {"assignment_id": ""},
    {"course_id": "classroom_1.01", "assignment_id": ""}, {"course_id": "classroom_1.01", "assignment_id": " "},
])
def test_empty_or_blank_ids_are_422_and_start_nothing(env, body):
    client, writer, _ = env
    r = _post(client, body)
    assert r.status_code == 422 and r.json()["error"]["code"] == "validation_failed"
    assert writer.saved == []


def test_ids_are_trimmed_so_one_scope_has_one_key(env):
    client, _, _ = env
    a = _post(client, {"course_id": "classroom_1.01"})
    b = _post(client, {"course_id": " classroom_1.01 "})
    assert a.status_code == b.status_code == 202


# --- #6: an unknown assignment is an error, not an empty success -------------------------------------

def test_unknown_assignment_is_422_and_starts_nothing(env):
    client, writer, _ = env
    r = _post(client, {"course_id": "classroom_1.01", "assignment_id": "no-such-assignment"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "unknown_assignment"
    assert writer.saved == []


# --- #4: one unreadable course is contained ----------------------------------------------------------

def _break(data, version):
    with dataset.essays_path(data, version).open("a", encoding="utf-8") as fh:
        fh.write("this line is not json\n")


def test_unreadable_course_alone_is_502_naming_only_the_course(env):
    client, writer, data = env
    _break(data, "1.01")
    r = _post(client, {"course_id": "classroom_1.01"})
    assert r.status_code == 502 and r.json()["error"]["code"] == "lms_data_error"
    assert "classroom_1.01" in r.json()["error"]["message"] and "not json" not in r.text
    assert writer.saved == []


def test_sweep_of_every_course_skips_an_unreadable_one_and_reports_it(env):
    client, writer, data = env
    _classroom(data, "1.02", ["d" * 64])
    _break(data, "1.02")
    r = _post(client, {})
    assert r.status_code == 202
    run = client.get(f"/runs/{r.json()['run_id']}", headers=AUTH).json()
    assert run["status"] == "succeeded" and run["completed_count"] == 3
    assert run["unreadable_courses"] == ["classroom_1.02"]
    assert {res.submission_ref.course_id for res, _ in writer.saved} == {"classroom_1.01"}


# --- #8: every outside call has a time limit ---------------------------------------------------------

def test_gemini_clients_get_a_time_limit(monkeypatch):
    from agentic.llm import gemini

    seen = []
    monkeypatch.setattr(gemini.genai, "Client", lambda **kw: seen.append(kw) or object())
    gemini.GeminiChatModel("m", "k", 10, timeout_s=90)
    gemini.GeminiEmbedder("e", "k", 768, 10, timeout_s=30)
    assert [kw["http_options"].timeout for kw in seen] == [90_000, 30_000]  # the SDK takes milliseconds


def test_wiring_passes_the_llm_time_limit(monkeypatch):
    from agentic.llm import gemini

    seen = []
    monkeypatch.setattr(gemini.genai, "Client", lambda **kw: seen.append(kw) or object())
    s = Settings(llm_provider="gemini", llm_model="m", llm_api_key="k", llm_timeout_s=45)
    wiring.build_chat(s)
    assert seen[-1]["http_options"].timeout == 45_000


def test_postgres_connections_time_out(monkeypatch):
    import psycopg

    seen = {}
    monkeypatch.setattr(psycopg, "connect", lambda url, **kw: seen.update(kw) or (_ for _ in ()).throw(RuntimeError))
    with pytest.raises(RuntimeError):
        wiring.connect(Settings(db_statement_timeout_s=20))
    assert seen["connect_timeout"] == 10
    assert "statement_timeout=20000" in seen["options"]


def test_time_limits_must_be_positive():
    with pytest.raises(ValueError):
        Settings(llm_timeout_s=0)
    with pytest.raises(ValueError):
        Settings(db_statement_timeout_s=-1)


# --- #9: the agentic container comes back after a crash ----------------------------------------------

def test_compose_restarts_the_agentic_service():
    import yaml

    if not (REPO / "docker-compose.yml").exists():
        pytest.skip("the lab's compose file (in the team repo, AgenticAI/docker-compose.yml is generated by the export)")
    lab = yaml.safe_load((REPO / "docker-compose.yml").read_text(encoding="utf-8"))
    assert lab["services"]["agentic"]["restart"] == "unless-stopped"

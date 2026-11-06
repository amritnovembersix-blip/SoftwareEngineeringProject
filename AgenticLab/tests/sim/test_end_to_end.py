"""End to end against local Postgres: the simulator releases week 1 into a temporary sim folder, sends each
submission_graded event to the agentic service (in-process, FAKE_LLM=1), and every review lands in Postgres."""
import pytest

from agentic.coursedata import CourseDir, lms_assignment_id

from agentic_lab.config import SYNTHETIC_DIR, lab_settings
from agentic_lab.sim.release import FolderReleaser
from agentic_lab.sim.schedule import due_in_week, schedule
from agentic_lab.sim.simulator import Simulator
from agentic_lab.sim.trigger import RunsClient, WebhookTrigger

pytestmark = pytest.mark.db

VERSION, COURSE = "2.00-control", "classroom_2.00-control"
SECRET, TOKEN = "sim-e2e-webhook-secret", "sim-e2e-api-token"


@pytest.fixture
def conn():
    try:
        from agentic.wiring import connect

        c = connect(lab_settings())
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"local Postgres unavailable ({type(exc).__name__})")
    try:
        yield c
    finally:
        c.close()


@pytest.fixture
def seeded(conn):
    from agentic.results.reset import reset_reviews
    from agentic.results.seed import seed_course

    if conn.execute("SELECT count(*) FROM standard").fetchone()[0] == 0:
        pytest.skip("standards catalog not loaded")
    seed_course(conn, SYNTHETIC_DIR, VERSION)  # both twins
    # Note: reset_reviews wipes classroom_2.00-control's review results in the dev DB, before and after the test.
    reset_reviews(conn, COURSE)  # an earlier run may have left rows
    try:
        yield conn
    finally:
        reset_reviews(conn, COURSE)


def _settings(sim_dir):
    return lab_settings().model_copy(update={
        "fake_llm": True, "lms_source": "coursedir", "data_dir": sim_dir, "persist_results": True,
        "review_pipeline": "v2", "verify": False, "webhook_secret": SECRET, "agentic_api_token": TOKEN})


def test_week_one_reviews_land_in_postgres(seeded, tmp_path):
    from fastapi.testclient import TestClient

    from agentic.service.app import create_app

    sim_dir = tmp_path / "sim"
    week1 = due_in_week(schedule(SYNTHETIC_DIR, VERSION), 1)
    assert {i.file_id for i in week1} >= {"ela9v2-a01", "ela9v2-a02"}
    expected = {lms_assignment_id(i.file_id, VERSION): CourseDir(SYNTHETIC_DIR, VERSION).submissions(i.file_id)
                for i in week1}
    assert all(expected.values())

    polled: dict[str, str] = {}
    with TestClient(create_app(_settings(sim_dir))) as client:
        runs = RunsClient("http://testserver", TOKEN, client=client)

        class RecordingRuns:  # remembers the last status each run was polled with
            def get(self, run_id):
                record = runs.get(run_id)
                polled[run_id] = record.get("status") if record else None
                return record

        sim = Simulator(SYNTHETIC_DIR, [VERSION], FolderReleaser(SYNTHETIC_DIR, sim_dir),
                        WebhookTrigger("http://testserver", SECRET, client=client), RecordingRuns(), sim_dir,
                        sleep=lambda _s: None)
        report = sim.run_through(1, wait=True)

    n_rows = sum(len(rows) for rows in expected.values())
    assert (report.week, report.released, report.sent) == (1, n_rows, n_rows)
    assert len(polled) == n_rows and set(polled.values()) == {"succeeded"}
    assert not (sim_dir / "courses" / VERSION / "answer_key").exists()

    for lms_id, rows in expected.items():
        got = dict(seeded.execute(
            "SELECT s.lms_submission_id, s.review_status FROM submission s "
            "JOIN assignment a ON a.id = s.assignment_id JOIN classroom c ON c.id = a.classroom_id "
            "WHERE c.lms_course_id = %s AND a.lms_assignment_id = %s", (COURSE, lms_id)).fetchall())
        assert got == {r.submission_id: "succeeded" for r in rows}, lms_id
        without_verdicts = seeded.execute(
            "SELECT count(*) FROM submission s JOIN assignment a ON a.id = s.assignment_id "
            "WHERE a.lms_assignment_id = %s AND NOT EXISTS (SELECT 1 FROM verdict v WHERE v.submission_id = s.id)",
            (lms_id,)).fetchone()[0]
        assert without_verdicts == 0, lms_id

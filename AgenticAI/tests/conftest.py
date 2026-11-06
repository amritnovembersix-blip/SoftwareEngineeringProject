import psycopg
import pytest
from pgvector.psycopg import register_vector

from agentic.config import Settings


# Ruling P2: MVP tests that rely on the pipeline default (REVIEW_PIPELINE was "mvp" before v2 shipped)
# run with REVIEW_PIPELINE=mvp. Their bodies and assertions are unchanged.
MVP_DEFAULT_TESTS = {
    "test_wiring.py": {"test_fake_mode_builds_runnable_reviewer", "test_fake_rag_runs", "test_reviewer_models",
                       "test_reviewer_reviews_only_resolved_strands", "test_targets_define_review_scope"},
    "test_reports.py": {"test_report_has_no_essay_or_issue_text", "test_report_writes_given_models"},
    "test_cli.py": {"test_review_prints_findings_and_writes_report", "test_cli_persist_duplicate_message",
                    "test_review_strands_flag_is_recorded", "test_review_assignment_context_off_flag"},
    "test_app.py": {"test_webhook_reviews_and_run_succeeds", "test_course_reviewer_maps_classroom_course_to_version",
                    "test_webhook_for_multiple_choice_is_skipped"},
    "test_sweeps.py": {"test_sweep_skips_multiple_choice_submissions"},
}


@pytest.fixture(autouse=True)
def _mvp_pipeline_default(request, monkeypatch):
    if request.node.originalname not in MVP_DEFAULT_TESTS.get(request.path.name, ()):
        yield
        return
    monkeypatch.setenv("REVIEW_PIPELINE", "mvp")  # Settings.load()
    field = Settings.model_fields["review_pipeline"]  # Settings(...)
    original = field.default
    field.default = "mvp"
    Settings.model_rebuild(force=True)
    try:
        yield
    finally:
        field.default = original
        Settings.model_rebuild(force=True)


def pytest_configure(config):
    config.addinivalue_line("markers", "db: needs local Postgres")
    config.addinivalue_line("markers", "live: calls real external APIs")


def _db_unavailable_reason() -> str | None:
    try:
        import psycopg

        url = Settings.load().database_url_local
        with psycopg.connect(url, connect_timeout=3):
            pass
    except Exception as exc:  # noqa: BLE001
        return f"local Postgres unavailable ({type(exc).__name__})"
    return None


def pytest_collection_modifyitems(config, items):
    db_items = [i for i in items if "db" in i.keywords]
    if not db_items:
        return
    reason = _db_unavailable_reason()
    if reason:
        skip = pytest.mark.skip(reason=reason)
        for item in db_items:
            item.add_marker(skip)


@pytest.fixture
def db_conn():
    """Local Postgres connection inside one outer transaction that is always rolled back.

    The outer block matters: on an idle connection psycopg's `with conn.transaction():` starts a
    real transaction and COMMITS it. Inside this block, the code's transaction() calls become
    savepoints instead, so tests leave no data (see test_db_isolation.py).
    """
    conn = psycopg.connect(Settings.load().database_url_local)
    register_vector(conn)
    try:
        with conn.transaction(force_rollback=True):
            yield conn
    finally:
        conn.close()

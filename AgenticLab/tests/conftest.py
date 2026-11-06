"""Lab tests reuse production's test helpers (tests/fakes.py) from the installed agentic package."""
import sys
from pathlib import Path

import agentic

_PROD_TESTS = Path(agentic.__file__).resolve().parents[2] / "tests"
if str(_PROD_TESTS) not in sys.path:
    sys.path.append(str(_PROD_TESTS))

import pytest  # noqa: E402

from agentic.config import Settings  # noqa: E402

# Ruling P2: lab tests of the MVP strategies that rely on the pipeline default (REVIEW_PIPELINE was "mvp"
# before v2 shipped) run with REVIEW_PIPELINE=mvp. Their bodies and assertions are unchanged.
MVP_DEFAULT_TESTS = {
    "test_cli.py": {"test_evaluate_uses_classroom_assignment_strands"},
    "test_demo_app.py": {"test_demo_reading_strand_checkboxes_preset_from_assignment",
                         "test_demo_targeted_assignment_shows_assignment_and_hides_strand_boxes"},
    "test_eval.py": {"test_run_evaluation_both_strategies_fake"},
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

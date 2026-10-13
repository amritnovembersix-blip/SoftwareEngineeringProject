"""Every assignment template loads, and every target is a real grade 9-10 standard."""
from pathlib import Path

import pytest

from agentic.config import Settings
from agentic.dataset import read_assignment
from agentic.standards.loader import normalize_rows, read_xlsx
from agentic_lab.config import SYNTHETIC_DIR

LIBRARY = SYNTHETIC_DIR / "assignments"
STANDARDS = Settings().standards_file
TEMPLATES = sorted(LIBRARY.glob("*.json"))


def test_library_has_the_starter_assignments():
    assert {p.stem for p in TEMPLATES} >= {"argument", "rl-theme", "rl-pov", "rl-language", "ri-preface"}


@pytest.mark.parametrize("path", TEMPLATES, ids=lambda p: p.stem)
def test_template_loads_with_prompt_and_targets(path: Path):
    a = read_assignment(path)
    assert a.id == path.stem and a.title and a.prompt and a.targets


@pytest.mark.skipif(not STANDARDS.exists(), reason="standards file not present")
@pytest.mark.parametrize("path", TEMPLATES, ids=lambda p: p.stem)
def test_template_targets_exist_at_grade_9(path: Path):
    grade9 = {r.code for r in normalize_rows(read_xlsx(STANDARDS)) if r.grade_min <= 9 <= r.grade_max}
    assert set(read_assignment(path).targets) <= grade9

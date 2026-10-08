"""Lab settings: production Settings, with data and reports defaulting to the lab's folders."""
from pathlib import Path

from agentic.config import Settings

# The agentic_lab/ folder (AgenticLab/ in the team repo).
LAB_ROOT = Path(__file__).resolve().parents[2]
SYNTHETIC_DIR = LAB_ROOT / "data" / "synthetic"


def lab_settings(env_file: Path | None | str = "auto") -> Settings:
    """Settings.load(), except DATA_DIR and REPORTS_DIR default to the lab (unless set in env)."""
    s = Settings.load(env_file)
    defaults = Settings()
    updates = {}
    if s.data_dir == defaults.data_dir:
        updates["data_dir"] = SYNTHETIC_DIR
    if s.reports_dir == defaults.reports_dir:
        updates["reports_dir"] = LAB_ROOT / "local" / "reports"
    return s.model_copy(update=updates)

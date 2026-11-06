"""The simulator's clock and the events it has sent (sim_dir/state.json). Ids only, never student text."""
import os
import tempfile
from pathlib import Path

from pydantic import BaseModel, Field

STATE_FILE = "state.json"


class SentEvent(BaseModel):
    course_id: str
    assignment_id: str  # the LMS id, <file id>@<version>
    submission_id: str
    attempt: int
    run_id: str
    sent_at: str  # UTC ISO

    @property
    def key(self) -> tuple[str, str, int]:
        return (self.course_id, self.submission_id, self.attempt)


class SimState(BaseModel):
    week: int = 0
    sent: list[SentEvent] = Field(default_factory=list)


def load_state(sim_dir: Path) -> SimState:
    """The saved state, or week 0 with nothing sent when there is no state file."""
    path = Path(sim_dir) / STATE_FILE
    if not path.is_file():
        return SimState()
    return SimState.model_validate_json(path.read_text(encoding="utf-8"))


def save_state(sim_dir: Path, state: SimState) -> None:
    """Write through a temp file in the same folder, then os.replace, so a crash never leaves half a file."""
    sim_dir = Path(sim_dir)
    sim_dir.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=sim_dir, prefix=".state-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(state.model_dump_json(indent=1))
        os.replace(tmp, sim_dir / STATE_FILE)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise

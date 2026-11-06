"""Simulator settings, read from .env and the environment like the rest of the lab."""
from dataclasses import dataclass
from pathlib import Path

from agentic_lab.config import LAB_ROOT, SYNTHETIC_DIR, _synth_env

REPO_ROOT = LAB_ROOT.parent
DEFAULT_SIM_DIR = LAB_ROOT / "local" / "sim"


@dataclass(frozen=True)
class SimSettings:
    agentic_url: str = "http://localhost:8000"
    webhook_secret: str = ""
    agentic_api_token: str = ""
    sim_dir: Path = DEFAULT_SIM_DIR
    concurrency: int = 4
    sim_data_dir: Path = SYNTHETIC_DIR  # the v2 course folders it replays (independent of DATA_DIR)


def load_sim_settings(env_file: Path | None | str = "auto") -> SimSettings:
    """AGENTIC_URL, WEBHOOK_SECRET, AGENTIC_API_TOKEN, SIM_DIR, SIM_CONCURRENCY, SIM_DATA_DIR; the environment
    overrides .env, and blank means unset. Relative SIM_DIR and SIM_DATA_DIR are resolved against the repo root."""
    values = _synth_env(env_file)

    def get(name: str) -> str | None:
        v = values.get(name)
        return v.strip() if v is not None and v.strip() else None

    def path(name: str, default: Path) -> Path:
        raw = get(name)
        if not raw:
            return default
        return Path(raw) if Path(raw).is_absolute() else REPO_ROOT / raw

    d = SimSettings()
    sim_dir = path("SIM_DIR", d.sim_dir)
    concurrency = d.concurrency
    if raw := get("SIM_CONCURRENCY"):
        try:
            concurrency = int(raw)
        except ValueError:
            raise ValueError("SIM_CONCURRENCY must be a whole number") from None
        if concurrency < 1:
            raise ValueError("SIM_CONCURRENCY must be at least 1")
    return SimSettings(agentic_url=get("AGENTIC_URL") or d.agentic_url,
                       webhook_secret=get("WEBHOOK_SECRET") or "",
                       agentic_api_token=get("AGENTIC_API_TOKEN") or "",
                       sim_dir=sim_dir, concurrency=concurrency,
                       sim_data_dir=path("SIM_DATA_DIR", d.sim_data_dir))

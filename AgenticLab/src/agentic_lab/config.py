"""Lab settings: production Settings, with data and reports defaulting to the lab's folders."""
import os
from pathlib import Path

from dotenv import dotenv_values

from agentic.config import Settings, find_env_file

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


_SYNTH = ("provider", "model", "api_key", "rpm")
DEFAULT_SYNTH_WORKERS = 8


def _synth_env(env_file: Path | None | str) -> dict[str, str | None]:
    """The .env values (as Settings.load finds them), overridden by the environment."""
    if env_file == "auto":
        env_file = find_env_file()
    values: dict[str, str | None] = {}
    if env_file is not None and Path(env_file).exists():
        values.update(dotenv_values(env_file))
    values.update(os.environ)
    return values


def synth_settings(s: Settings, env_file: Path | None | str = "auto") -> Settings:
    """`s` with each SYNTH_LLM_<X> (from the same .env/environment as Settings.load) copied onto llm_<x>
    when set; unset or blank falls back to the LLM_<X> value already in `s`."""
    values = _synth_env(env_file)
    updates = {}
    for name in _SYNTH:
        v = values.get(f"SYNTH_LLM_{name.upper()}")
        if v is not None and v.strip():
            updates[f"llm_{name}"] = int(v) if name == "rpm" else v.strip()
    return s.model_copy(update=updates)


def audit_settings(s: Settings, env_file: Path | None | str = "auto") -> Settings:
    """`s` with the audit-fidelity model: AUDIT_LLM_MODEL is required and must differ from the writer model
    (`synth_settings`). AUDIT_LLM_PROVIDER/_API_KEY/_RPM are optional and fall back to the LLM_<X> value in `s`
    (never to SYNTH_LLM_*). Read from the same .env/environment as Settings.load."""
    values = _synth_env(env_file)
    model = (values.get("AUDIT_LLM_MODEL") or "").strip()
    if not model:
        raise ValueError("AUDIT_LLM_MODEL is not set: the audit needs a model other than the writer's")
    writer = synth_settings(s, env_file).llm_model.strip()
    if model.lower() == writer.lower():
        raise ValueError("AUDIT_LLM_MODEL equals the writer model (SYNTH_LLM_MODEL, else LLM_MODEL); "
                         "the audit needs a different model")
    updates: dict[str, str | int] = {"llm_model": model}
    for name in ("provider", "api_key", "rpm"):
        v = values.get(f"AUDIT_LLM_{name.upper()}")
        if v is not None and v.strip():
            updates[f"llm_{name}"] = int(v) if name == "rpm" else v.strip()
    return s.model_copy(update=updates)


def synth_workers(env_file: Path | None | str = "auto") -> int:
    """SYNTH_WORKERS (parallel generate-course writing tasks), read like SYNTH_LLM_*; unset or blank = 8."""
    v = (_synth_env(env_file).get("SYNTH_WORKERS") or "").strip()
    if not v:
        return DEFAULT_SYNTH_WORKERS
    try:
        n = int(v)
    except ValueError:
        raise ValueError("SYNTH_WORKERS must be a whole number") from None
    if n < 1:
        raise ValueError("SYNTH_WORKERS must be at least 1")
    return n

"""Settings: env-driven configuration. No vendor, model ID or key is hard-coded."""
import os
from pathlib import Path

from dotenv import dotenv_values
from pydantic import BaseModel

# The agentic/ folder (AgenticAI/ in the team repo): everything this package needs lives under it.
PACKAGE_ROOT = Path(__file__).resolve().parents[2]


def find_env_file(package_root: Path = PACKAGE_ROOT) -> Path | None:
    """`.env` in the package folder, else in its parent (the lab repo keeps one at the repo root)."""
    for candidate in (Path(package_root) / ".env", Path(package_root).parent / ".env"):
        if candidate.exists():
            return candidate
    return None


_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


def _bool(value: str) -> bool:
    v = value.strip().lower()
    if v in _TRUE:
        return True
    if v in _FALSE:
        return False
    raise ValueError(f"not a boolean: {value!r}")


class Settings(BaseModel):
    llm_provider: str = "gemini"
    llm_model: str = ""
    llm_api_key: str = ""
    llm_rpm: int = 10

    embedding_provider: str = "gemini"
    embedding_model: str = ""
    embedding_api_key: str = ""
    embedding_dim: int = 768
    embedding_rpm: int = 10

    decision_provider: str = "jev"
    jev_api_key: str = ""
    jev_base_url: str = "https://api.typesafe.ai"
    jev_model: str = "jev-latest"
    jev_miss_threshold: float = 0.5
    verify: bool = True

    fake_llm: bool = False
    review_strategy: str = "full"
    assignment_context: bool = True  # show the reviewer the assignment (context only)
    review_strands: str = ""  # e.g. "W,RL"; blank = use the classroom's assignment file
    retrieval_k: int = 3

    agentic_api_token: str = ""  # bearer token for the HTTP service; blank = every call is refused
    webhook_secret: str = ""  # X-SASS-Webhook-Secret from the LMS; blank = every event is refused
    persist_results: bool = True  # the HTTP service saves each review through the ResultsWriter

    database_url_local: str = "postgresql://sass:change-me@localhost:5432/sass"
    standards_file: Path = PACKAGE_ROOT / "standards" / "mls-ela-writing-v1.xlsx"
    data_dir: Path = PACKAGE_ROOT / "data"  # file-LMS data; the lab points this at its synthetic data
    reports_dir: Path = PACKAGE_ROOT / "local" / "reports"
    classroom: str = "1.01"

    @classmethod
    def load(cls, env_file: Path | None | str = "auto") -> "Settings":
        if env_file == "auto":
            env_file = find_env_file()
        values: dict[str, str | None] = {}
        if env_file is not None and Path(env_file).exists():
            values.update(dotenv_values(env_file))
        values.update(os.environ)  # environment overrides the file
        raw: dict[str, object] = {}
        for name, field in cls.model_fields.items():
            v = values.get(name.upper())
            if v is None or not v.strip():  # blank (e.g. `LLM_PROVIDER=` in .env.example) means unset
                continue
            if field.annotation is bool:
                raw[name] = _bool(v)
            elif field.annotation is Path:
                p = Path(v)
                raw[name] = p if p.is_absolute() else PACKAGE_ROOT / p
            else:
                raw[name] = v
        return cls(**raw)

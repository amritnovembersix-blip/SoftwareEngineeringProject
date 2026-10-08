import os

import pytest

from agentic.config import Settings

ENV_NAMES = [
    "LLM_PROVIDER", "LLM_MODEL", "LLM_API_KEY", "LLM_RPM",
    "EMBEDDING_PROVIDER", "EMBEDDING_MODEL", "EMBEDDING_API_KEY", "EMBEDDING_DIM", "EMBEDDING_RPM",
    "DECISION_PROVIDER", "JEV_API_KEY", "JEV_BASE_URL", "JEV_MODEL", "JEV_MISS_THRESHOLD", "VERIFY",
    "FAKE_LLM", "REVIEW_STRATEGY", "RETRIEVAL_K", "DATABASE_URL_LOCAL",
    "STANDARDS_FILE", "DATA_DIR", "REPORTS_DIR", "CLASSROOM",
]


@pytest.fixture
def clean_env(monkeypatch):
    for name in ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def test_defaults(clean_env):
    s = Settings.load(env_file=None)
    assert s.review_strategy == "full"
    assert s.jev_base_url == "https://api.typesafe.ai"
    assert s.jev_model == "jev-latest"
    assert s.classroom == "1.01"
    assert s.verify is True
    assert s.jev_miss_threshold == 0.5


def test_env_overrides(clean_env):
    clean_env.setenv("VERIFY", "0")
    clean_env.setenv("FAKE_LLM", "1")
    clean_env.setenv("RETRIEVAL_K", "5")
    s = Settings.load(env_file=None)
    assert s.verify is False
    assert s.fake_llm is True
    assert s.retrieval_k == 5


def test_blank_values_keep_defaults(clean_env, tmp_path):
    # .env.example ships blank lines like `LLM_PROVIDER=`; blank means "unset", not "".
    env = tmp_path / ".env"
    env.write_text("LLM_PROVIDER=\nLLM_RPM=\nVERIFY=\nJEV_MODEL=  \nSTANDARDS_FILE=\n", encoding="utf-8")
    s = Settings.load(env_file=env)
    assert s.llm_provider == "gemini"
    assert s.llm_rpm == 10
    assert s.verify is True
    assert s.jev_model == "jev-latest"
    assert s.standards_file == Settings().standards_file


def test_blank_environment_value_keeps_default(clean_env):
    clean_env.setenv("DECISION_PROVIDER", "")
    assert Settings.load(env_file=None).decision_provider == "jev"


# --- self-contained package: paths relative to the agentic/ (AgenticAI/) folder ---------------

def test_defaults_live_inside_the_package_folder(clean_env):
    from agentic.config import PACKAGE_ROOT

    s = Settings.load(env_file=None)
    assert s.standards_file == PACKAGE_ROOT / "standards" / "mls-ela-writing-v1.xlsx"
    assert s.reports_dir == PACKAGE_ROOT / "local" / "reports"
    assert s.data_dir == PACKAGE_ROOT / "data"


def test_relative_env_paths_resolve_against_package_root(clean_env):
    from agentic.config import PACKAGE_ROOT

    clean_env.setenv("DATA_DIR", "somewhere/else")
    assert Settings.load(env_file=None).data_dir == PACKAGE_ROOT / "somewhere/else"


def test_env_file_found_in_package_then_parent(tmp_path):
    from agentic.config import find_env_file

    pkg = tmp_path / "AgenticAI"
    pkg.mkdir()
    assert find_env_file(pkg) is None
    (tmp_path / ".env").write_text("LLM_RPM=7\n", encoding="utf-8")
    assert find_env_file(pkg) == tmp_path / ".env"
    (pkg / ".env").write_text("LLM_RPM=8\n", encoding="utf-8")
    assert find_env_file(pkg) == pkg / ".env"

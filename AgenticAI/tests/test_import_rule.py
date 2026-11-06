import ast
from pathlib import Path

AGENTIC = Path(__file__).resolve().parents[1]
SRC = AGENTIC / "src" / "agentic"

FORBIDDEN = (
    "psycopg",
    "google",
    "httpx",
    "agentic.llm.gemini",
    "agentic.llm.fake",
    "agentic.decisions.jev",
    "agentic.decisions.fake",
    "agentic.lms.jsonl_client",
    "agentic.lms.coursedir_client",
    "agentic.results.postgres_writer",
    "agentic.analytics.postgres",
    "agentic.analytics.memory",
    "agentic.settings.postgres_store",
    "agentic.standards.repository",
    "agentic.standards.fake",
)
ADAPTER_MODULES = {
    "llm": {"gemini", "fake"},
    "decisions": {"jev", "fake"},
    "lms": {"jsonl_client", "coursedir_client"},
    "results": {"postgres_writer"},
    "analytics": {"postgres", "memory"},
    "settings": {"postgres_store"},
    "standards": {"repository", "fake"},
}


# review/v2/run.py imports agentic.wiring on purpose: review_one is a composition point (spec section 4a),
# and wiring (not an Adapter module) is the one place that names Adapters, so this rule still holds.
def _client_files():
    files = sorted((SRC / "review").rglob("*.py")) if (SRC / "review").exists() else []
    files += sorted((SRC / "service").rglob("*.py"))
    files.append(SRC / "__main__.py")
    return files


def _imported_modules(path: Path) -> list[str]:
    mods: list[str] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            mods += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            base = ("." * node.level) + (node.module or "")
            mods.append(base)
            mods += [f"{base}.{a.name}" if base and not base.endswith(".") else f"{base}{a.name}"
                     for a in node.names]
    return mods


def _violates(mod: str) -> bool:
    stripped = mod.lstrip(".")
    for bad in FORBIDDEN:
        if stripped == bad or stripped.startswith(bad + "."):
            return True
    parts = stripped.split(".")
    if parts and parts[0] == "agentic":
        parts = parts[1:]
    # relative or absolute forms like "llm.gemini"
    return len(parts) >= 2 and parts[1] in ADAPTER_MODULES.get(parts[0], set())


def test_clients_do_not_import_adapters_or_sdks():
    offenders = [
        f"{p.name}: {m}" for p in _client_files() for m in _imported_modules(p) if _violates(m)
    ]
    assert offenders == []


def test_violation_detector_catches_forms():
    assert _violates("psycopg")
    assert _violates("google.genai")
    assert _violates("httpx")
    assert _violates("agentic.llm.gemini")  # from agentic.llm import gemini
    assert _violates("agentic.results.postgres_writer")
    assert _violates("agentic.settings.postgres_store")
    assert not _violates("agentic.llm.base")
    assert not _violates("agentic.models")


def test_pure_analytics_import_no_adapters_or_sdks():
    """analytics/ holds pure functions plus the orchestrator; only postgres.py and memory.py are Adapters."""
    pure = [p for p in sorted((SRC / "analytics").glob("*.py")) if p.stem not in ("postgres", "memory")]
    offenders = [f"{p.name}: {m}" for p in pure for m in _imported_modules(p) if _violates(m)]
    assert offenders == []


def test_production_never_imports_the_lab():
    """Layers: agentic_lab depends on agentic, never the reverse (AgenticAI/ ships without the lab)."""
    offenders = [
        f"{p.relative_to(SRC)}: {m}" for p in sorted(SRC.rglob("*.py")) for m in _imported_modules(p)
        if m.lstrip(".").split(".")[0] == "agentic_lab"
    ]
    assert offenders == []

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
    "agentic.results.postgres_writer",
    "agentic.standards.repository",
    "agentic.standards.fake",
)
ADAPTER_MODULES = {
    "llm": {"gemini", "fake"},
    "decisions": {"jev", "fake"},
    "lms": {"jsonl_client"},
    "results": {"postgres_writer"},
    "standards": {"repository", "fake"},
}


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
    assert not _violates("agentic.llm.base")
    assert not _violates("agentic.models")


def test_production_never_imports_the_lab():
    """Layers: agentic_lab depends on agentic, never the reverse (AgenticAI/ ships without the lab)."""
    offenders = [
        f"{p.relative_to(SRC)}: {m}" for p in sorted(SRC.rglob("*.py")) for m in _imported_modules(p)
        if m.lstrip(".").split(".")[0] == "agentic_lab"
    ]
    assert offenders == []

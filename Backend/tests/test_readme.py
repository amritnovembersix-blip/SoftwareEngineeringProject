"""Keep Backend/README.md honest: every endpoint the API serves must have a row in its
"All endpoints" table, and the table must not list endpoints that no longer exist."""
import re
from pathlib import Path

from app.main import app

README = Path(__file__).resolve().parents[1] / "README.md"


def _norm(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "{}", path.split("?")[0])


def _served() -> set[tuple[str, str]]:
    return {(method.upper(), _norm(path)) for path, ops in app.openapi()["paths"].items() for method in ops}


def _documented() -> set[tuple[str, str]]:
    text = README.read_text()
    table = text[text.index("## All endpoints"):text.index("## Set up from scratch")]
    return {(m, _norm(p)) for m, p in re.findall(r"^\| (GET|POST|PATCH|PUT|DELETE) \| `([^`]+)`", table, re.M)}


def test_every_endpoint_is_documented():
    missing = _served() - _documented()
    assert not missing, f"add these to the README's All endpoints table: {sorted(missing)}"


def test_no_documented_endpoint_is_gone():
    gone = _documented() - _served()
    assert not gone, f"these README rows have no endpoint any more: {sorted(gone)}"


def test_old_demo_script_is_not_mentioned():
    root = README.parents[1]
    for path in [README, root / "Frontend" / "README.md"]:
        text = path.read_text()
        assert "bash Backend/scripts/fill_demo_data.sh" not in text, path
    assert not (README.parent / "scripts" / "fill_demo_data.sh").exists()

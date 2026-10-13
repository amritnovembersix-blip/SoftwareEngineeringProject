"""Lab clients (evaluation, demo) reach models only through production wiring, never an Adapter."""
from pathlib import Path

from test_import_rule import _imported_modules, _violates

LAB = Path(__file__).resolve().parents[1]
CLIENTS = [LAB / "src" / "agentic_lab" / "eval.py", LAB / "src" / "agentic_lab" / "demo_view.py",
           LAB / "demo" / "app.py"]


def test_lab_clients_do_not_import_adapters_or_sdks():
    offenders = [f"{p.name}: {m}" for p in CLIENTS for m in _imported_modules(p) if _violates(m)]
    assert offenders == []

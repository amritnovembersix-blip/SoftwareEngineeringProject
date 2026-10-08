"""Lab tests reuse production's test helpers (tests/fakes.py) from the installed agentic package."""
import sys
from pathlib import Path

import agentic

_PROD_TESTS = Path(agentic.__file__).resolve().parents[2] / "tests"
if str(_PROD_TESTS) not in sys.path:
    sys.path.append(str(_PROD_TESTS))

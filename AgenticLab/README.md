# agentic_lab/

The research layer around the production agentic package (`agentic/`): synthetic classrooms, the essay
generator, evaluation against labels, and the Streamlit demo. It depends on `agentic`; production never
imports it (enforced by `agentic/tests/test_import_rule.py`). In the team repo it ships as `AgenticLab/`.

**Owner:** Justin Keck.

| Path | What |
|---|---|
| `data/synthetic/` | Labels, assignments and essays per classroom (see its README, including the mock LMS mapping) |
| `src/agentic_lab/labels.py` | Label models (`LabelRow`, `Underdeveloped`) and `load_labels` |
| `src/agentic_lab/synth/` | Essay generator (LLM writes essays that show the labeled weaknesses) |
| `src/agentic_lab/eval.py` | Scores reviews against labels: precision/recall, false alarms, calibration |
| `src/agentic_lab/demo_view.py`, `demo/` | Streamlit demo (Review and Compare reports tabs) |
| `local/` | Git-ignored: eval reports (`local/reports/`), throwaway demo data (`local/demo/`) |

## Setup

Install both packages into one venv (from the repo root):

```bash
python -m venv agentic/.venv
agentic/.venv/Scripts/python -m pip install -e "agentic[dev]" -e "agentic_lab[dev]"
```

Settings come from the same `.env` as production. The lab defaults `DATA_DIR` to `agentic_lab/data/synthetic`
and `REPORTS_DIR` to `agentic_lab/local/reports` unless those are set.

## CLI

`agentic/.venv/Scripts/python -m agentic_lab <command>`:

| Command | Purpose | Example |
|---|---|---|
| `generate-essays` | Generate synthetic essays from a classroom's labels | `generate-essays --classroom 1.02 --overwrite` |
| `evaluate` | Score reviews against the labels; writes `eval-<ts>.json` and `.md` | `evaluate --strategy both --compare-context` |

Production commands (`review`, `load-standards`, `seed-demo`) are `python -m agentic ...`. To review the lab's
essays with them, set `DATA_DIR=agentic_lab/data/synthetic` in `.env`.

`evaluate` flags: `--classroom`, `--strategy full|rag|both`, `--students N`, `--strands`,
`--assignment-context on|off`, `--compare-context` (each strategy also runs with context off as `<name>-noctx`).

To build a new classroom: copy a template from `data/synthetic/assignments/` to
`data/synthetic/classroom_<v>.assignment.json`, write labels using only its `targets`, then
`generate-essays --classroom <v>` (it refuses labels outside the targets).

## Demo

```bash
agentic/.venv/Scripts/python agentic_lab/demo/run_demo.py --fake   # no keys: fake models, throwaway essays in local/demo
agentic/.venv/Scripts/python agentic_lab/demo/run_demo.py          # real models and data from .env
```

The Claude desktop app starts either from `.claude/launch.json` (`agentic-demo-fake` on 8501, `agentic-demo`
on 8510). Tabs: **Review** (one essay, RL/RI checkboxes preset from the assignment) and **Compare reports**
(saved `eval-*.json` side by side, per-student A/B diff, calibration, not-evident counts; no model calls).

## Tests

```bash
cd agentic_lab && ../agentic/.venv/Scripts/python -m pytest -q
```

Lab tests reuse production's `tests/fakes.py` helpers (added to the path by `tests/conftest.py`).

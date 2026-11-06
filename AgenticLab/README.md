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
| `src/agentic_lab/sim/`, `sim_view.py` | Semester simulator and its Simulation tab helpers (see [Semester simulator](#semester-simulator)) |
| `local/` | Git-ignored: eval reports (`local/reports/`), throwaway demo data (`local/demo/`), simulator runtime folder (`local/sim/`) |

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
| `fill-demo` | Fill Postgres with labelled demo reviews from the answer key and run the analytics, so the Backend and Frontend have rows to build against. No model and no API keys. Safe to rerun | `fill-demo --through-week 7` |
| `study` | Twin intervention study: did the intervention classroom improve on the treated skill families more than its control twin? Prints the report and writes `study-<ts>.json` and `.md` to the reports folder. `--source answer-key` (default) needs no reviews; `db` reads Postgres | `study --twins 2.00` |

### v2 commands (matched-pair course folders)

| Command | Purpose | Example |
|---|---|---|
| `build-standards` | Draft `reviewability-v2.csv` from the corpus (`--overwrite` discards hand edits) and build the reviewable xlsx | `build-standards` |
| `build-course` | Write both twin course folders and answer keys from a plan (no model calls) | `build-course --plan agentic_lab/data/synthetic/plans/ela9-v2.json` |
| `generate-course` | Generate submissions, `answer_key/expected/` and `fidelity.jsonl` with the synth model | `generate-course --arm both --through-week 3 --students 6` |
| `audit-fidelity` | Re-run the fidelity checker with a different model (`AUDIT_LLM_MODEL`) on a stratified sample of both arms and report agreement; never changes the data | `audit-fidelity --sample 40 --seed 7` |

`generate-course` flags: `--arm control|intervention|both`, `--version` (default `2.00`),
`--through-week N`, `--students K` (first K roster students), `--seed`, `--overwrite`, and
`--workers N` (parallel workers; default `SYNTH_WORKERS`, else 8), and `--mc-only` (multiple choice only, no model
calls; with `--overwrite` it rewrites only multiple-choice rows, their `expected/` rows and their fidelity rows). It
prints, per arm, rows written, skipped, failed fidelity, multiple choice, and the fidelity pass rate of the written
submissions. A second line reports multiple choice on its own: how many passed the rule-based concept check, and
detectability per planted level 1/2/3 (the share of (submission, concept) pairs with 2 or more picks). It is resumable: rerunning skips rows already
written. If some model replies were unusable it prints "N unusable; rerun to retry" and exits 1; rerun the same
command and it retries only those.

The synth model is configured by `SYNTH_LLM_PROVIDER`, `SYNTH_LLM_MODEL`, `SYNTH_LLM_API_KEY` and `SYNTH_LLM_RPM`;
each falls back to the matching `LLM_*` value when unset, so one `.env` works. `SYNTH_WORKERS` (default 8) sets the number of parallel workers; `SYNTH_LLM_RPM` is the shared rate limit. Reports and errors name ids only,
never submission text. `audit-fidelity` needs `AUDIT_LLM_MODEL` (required, and different from the writer model);
`AUDIT_LLM_PROVIDER`, `AUDIT_LLM_API_KEY` and `AUDIT_LLM_RPM` fall back to `LLM_*`. It writes
`audit-fidelity-<time>.md` and `.json` under REPORTS_DIR (or `--out`), with codes, ids and counts only; the audit is
a second opinion, not ground truth. Layout and the visibility rule for `answer_key/`: `data/synthetic/README.md`.

Production commands (`review`, `load-standards`, `seed-demo`) are `python -m agentic ...`. To review the lab's
essays with them, set `DATA_DIR=agentic_lab/data/synthetic` in `.env`.

`evaluate` flags: `--classroom`, `--strategy full|rag|both`, `--students N`, `--strands`,
`--assignment-context on|off`, `--compare-context` (each strategy also runs with context off as `<name>-noctx`).

To build a new classroom: copy a template from `data/synthetic/assignments/` to
`data/synthetic/classroom_<v>.assignment.json`, write labels using only its `targets`, then
`generate-essays --classroom <v>` (it refuses labels outside the targets).

### Analytics in the lab

`analytics_lab.results_from_answer_key(data_dir, version)` returns an `InMemoryResultsReader` built from a
course's answer key, so the analytics (and the **Class summary** tab) run with no reviews and no model calls.
The answer key has no `finding_link` rows, so reteach groups there are by each student's own concept. `study`
compares the twins' change on the intervention's skill families and, for comparison, the effect planted in the
answer key's trajectory. Reports hold codes, counts and short student hashes only. This is a lab check, never a
product view.

## Demo

```bash
agentic/.venv/Scripts/python agentic_lab/demo/run_demo.py --fake   # no keys: fake models, throwaway essays in local/demo
agentic/.venv/Scripts/python agentic_lab/demo/run_demo.py          # real models and data from .env
```

The Claude desktop app starts either from `.claude/launch.json` (`agentic-demo-fake` on 8501, `agentic-demo`
on 8510). Tabs: **Review** (one essay, RL/RI checkboxes preset from the assignment), **Compare reports**
(saved `eval-*.json` side by side, per-student A/B diff, calibration, not-evident counts; no model calls),
**Review one**, **Compare runs**, **Simulation** (see [Semester simulator](#semester-simulator)), and
**Class summary** (status counts, common gaps, reteach groups as root-cause chains, peer pairs, a concepts x weeks
heatmap and the twin divergence chart; from the latest Postgres analysis or, with no reviews, the answer key).

## Semester simulator

Replays the v2 twin courses week by week, the way a real semester would reach SASS. For each week it
releases the assignments due that week (copies their submissions file into the runtime folder, which is what
the agentic service's course-folder LMS reads), then sends one `submission_graded` webhook per submission to the
agentic service. The service fetches the submission, reviews it, and saves the results to Postgres. Events carry
ids only. Multiple choice is reviewed too (v2 looks up the answers).

`agentic/.venv/Scripts/python -m agentic_lab simulate <action> [--course 2.00-control|2.00-intervention|both]`
(`--course` goes after the action; default `both`):

| Action | Purpose |
|---|---|
| `reset [--reviews]` | Empty the runtime folder and go back to week 0; `--reviews` also deletes the classrooms' review results from Postgres (DEV/DEMO only) |
| `advance [--weeks N] [--wait]` | Release and trigger the next week (or N weeks) |
| `run --through-week N [--wait]` | `reset`, then replay weeks 1..N |
| `status` | Each assignment's week, whether it is released, and its runs' states (queued, running, succeeded, failed) |

`--wait` polls each week's runs every 2 s until they finish (30-minute limit per week) and prints, per week, how
many runs failed and how many the service no longer knows (`unknown`, for example after a restart). Exit codes:
0 done, 1 unexpected error (class name only), 2 a rejected event or a wait timeout (ids, week and counts only),
3 the waited weeks finished but some review runs failed (see `status`). The clock and the sent events live in the
runtime folder's `state.json`, so a rerun resumes without sending anything twice.

Wait for runs to finish (`status`) before `reset` or `run`: both empty the runtime folder that in-flight reviews
read from. `reset` refuses a `SIM_DIR` that is or contains the data folder or the repository, or a non-empty
folder without `state.json`.

**Replaying.** The agentic service remembers the events it has accepted (in memory), so after
`reset --reviews` **restart the agentic service before replaying**; otherwise `run`/`advance` get the old run ids
back, nothing is reviewed, and the grid stays empty.

Settings (`.env` or the environment):

| Setting | Default | What |
|---|---|---|
| `AGENTIC_URL` | `http://localhost:8000` | The agentic service |
| `WEBHOOK_SECRET` | | Sent as `X-SASS-Webhook-Secret`; must match the service's |
| `AGENTIC_API_TOKEN` | | Bearer token for `GET /runs/{id}`; must match the service's |
| `SIM_DIR` | `agentic_lab/local/sim` | Runtime folder (relative paths resolve against the repo root) |
| `SIM_CONCURRENCY` | `4` | Events sent (and run states fetched) at once |
| `SIM_DATA_DIR` | `agentic_lab/data/synthetic` | The v2 course folders the simulator replays and the Simulation tab reads (relative paths resolve against the repo root); independent of `DATA_DIR`, which is what the agentic service reads |

The runtime folder is git-ignored and is a course-folder tree (`courses/`, `curricula/`) holding only what has
been released. It never holds `answer_key/`.

**Service setup.** Seed both twins first (once per database):

```bash
(cd agentic && LMS_SOURCE=coursedir DATA_DIR=../agentic_lab/data/synthetic ../agentic/.venv/Scripts/python -m agentic seed-demo --classroom 2.00-control)
```

Then point the agentic service at the runtime folder, either with the compose override:

```bash
docker compose -f docker-compose.yml -f docker-compose.sim.yml up -d --build agentic
```

or by running it locally on a free port and setting `AGENTIC_URL` to match:

```bash
(cd agentic && LMS_SOURCE=coursedir DATA_DIR=../agentic_lab/local/sim ../agentic/.venv/Scripts/python -m agentic serve --port 8010)
AGENTIC_URL=http://localhost:8010 agentic/.venv/Scripts/python -m agentic_lab simulate run --through-week 2 --wait
```

For a run with no model keys: with the compose route set `FAKE_LLM=1` in `.env` (compose passes it to the
service through `env_file`); with local `serve`, prefix it: `FAKE_LLM=1 LMS_SOURCE=coursedir ...`. Then
`simulate status`.

**Simulation tab** (in the Streamlit demo): the current week with **Advance one week**, **Reset** and
**Refresh run states** buttons, and an **As of week** slider to look back. **Pipeline** lists each due assignment
with its release state and sent count; its run states (one service call per sent event) load only on
**Refresh run states** or after Advance/Reset and stay cached while nothing new is sent, otherwise the run
columns are blank. It reads `SIM_DATA_DIR`, so it works in `run_demo.py --fake` too; **Interventions** lists the intervention twin's reteaching events so far. **Skill areas** is a grid of
students by skill area: each cell counts findings and ★ strengths, shaded by the highest support level (light
approaching, medium developing, dark beginning). ◆ marks students in an intervention. **Side by side** shows
matched control | intervention pairs in one grid. **Ground truth (lab only)** overlays the planted levels from
the answer key; it is a lab check, never a product view.

## Tests

```bash
cd agentic_lab && ../agentic/.venv/Scripts/python -m pytest -q
```

Lab tests reuse production's `tests/fakes.py` helpers (added to the path by `tests/conftest.py`).

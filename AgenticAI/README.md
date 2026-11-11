# agentic/

Agentic review for SASS (ELA). Two pipelines exist: **v2** (the default, see "Framework v2" below) and the
original **mvp** (described in this paragraph and the sections after it). The MVP reads a student's essay from the mock LMS (local files),
reviews it paragraph by paragraph against the Missouri Learning Standards with a chat model, optionally
verifies the result with TypeSafe Jev, and reports findings (standard, paragraphs, short reasons). Two
standards strategies exist: `full` (all standards at the grade in the prompt) and `rag` (pgvector retrieval).

**Owner:** Justin Keck. Design: `docs/superpowers/specs/`. Class diagram: `docs/sass_agentic_class_diagram.puml`.

This folder is the **production** layer and is self-contained: everything it needs (code, tests, standards
spreadsheets in `standards/`) lives under it, and default paths resolve against this folder, so it can be copied
to the team repo as `AgenticAI/`. Research tooling (synthetic data, essay generator, evaluation, Streamlit demo)
lives in `agentic_lab/`, which depends on this package; this package never imports the lab.

| Path | What |
|---|---|
| `src/agentic/` | Review graph, strategies, Targets and Adapters, `wiring.py` (the composition point), CLI |
| `standards/` | `mls-ela-reviewable-v2.xlsx` (v2 default), `mls-ela-writing-v1.xlsx` (MVP) and the full original corpus |
| `data/` | Default `DATA_DIR` for the file-backed LMS (`JsonlLMSClient`); empty here, the lab points elsewhere |
| `local/` | Git-ignored output (`local/reports/`) |

## Framework v2

`REVIEW_PIPELINE=v2` (the default; `mvp` keeps the original graph). Spec:
`docs/superpowers/specs/2026-10-28-agentic-framework-v2-design.md`.

**Exporting this folder flips the default to v2.** When `agentic/` ships as `AgenticAI/`, migration `V7` and the
v2 catalog (`standards/mls-ela-reviewable-v2.xlsx`, loaded with `load-standards`) must ship with it. To keep MVP
behavior (for example the team's "Review class" button on `classroom_1.01`), set `REVIEW_PIPELINE=mvp`.

**What it does.** Reviews every submission type against every reviewable standard from grade 3 up to the course
grade, with no gating. Each standard gets a verdict: `meets`, `growth_area` (with a support level: approaching,
developing or beginning) or `not_evident`. Strengths are kept for every student (`meets` rows; notable ones are
flagged). Growth areas become findings. SASS never scores: counts of evidence are fine, a percent-correct is not.
User-facing wording ("Growth area", "Approaching", ...) lives only in the `display_label` table.

```
fetch -> route by type -> multiple choice -> mc_lookup (no LLM) ----------------------------+
                       -> essay / paragraph / short answer -> segment -> plan_bands          |
                          -> band reviewers (parallel) -> merge -> root_cause -> aggregate   +-> ReviewResult
                          -> verify (Jev, if VERIFY=1) ---------------------------------------+
```

- **Band reviewers** (`review/v2/bands.py`): one class, `BandReviewer`, per grade band and strand set. Grades 3-5
  are judged per standard. Grade 6 and up is judged by progression: the highest grade met for each
  `strand.bigIdea.concept` (`working_grade`); grades at or below it are `meets`, grades above are `growth_area`.
  Identical wording across grades counts as one step. Each growth step has its own support level
  (`progression.v2` returns `step_levels`); a step without one takes `support_level`, and a step with neither drops
  the group (`missing_step_level`). Levels that fall as the grade rises are kept and flagged
  (`level_inversion:<group>` in the result's dropped list; evaluation counts them). On the full/lean profiles,
  `--prompt 6+-writing=v1` selects the one-level prompt. Mode `search` (`progression.v3`, the `graded` profile's 6+ reviewers) walks down instead:
  round 1 asks about every group's top step in one call, and each later round asks about the next lower step of
  the groups still unmet. A group stops at its first met step (the working grade; steps below it meet), at
  `not_evident` (that step and every one below it), or after its lowest step (working grade null). Each growth
  step keeps its own round's support level and evidence. A group whose reply is dropped keeps the growth steps
  found above it; each drop names its reason and group (`unjudged:<group>`, `no_valid_evidence:<group>`, ...).
  A search that stopped with no met step before its lowest step is marked `search_incomplete:<group>`, so its
  null working grade is not read as "below the band". Mode search needs `progression.v3` or later and mode
  progression needs v1 or v2; a mismatch fails at startup or on `--prompt`. The result counts `rounds`. A `BandScope` Decorator (`review/v2/scope.py`) limits the
  standards source to the band, as `StrandFilter` does for the MVP. Every standard belongs to exactly one reviewer.
- **Multiple choice** (`review/v2/mc.py`): a chosen distractor is an item-level observation from the answer key's
  tags. A finding needs `mc_finding_min_picks` picks on one standard (default 2). Support level and confidence
  come from picks divided by items on that standard. No chat call.
- **Root cause** (`review/v2/root_cause.py`): one call, only when a grade 6+ growth area and a grade 3-5 growth
  area share a cited segment. It links them (`finding_link`) and adds `condition`, `pattern` and `skill_state`.
  Links to findings that do not exist or do not share a segment are dropped.
- **Limits:** observation reasons 12 words or fewer; finding summaries, strength notes and link rationales 40
  or fewer. Over-long text is dropped (recorded in `dropped`), never truncated into the database.

### Profiles and cost

`BANDS_PROFILE=graded|full|lean` (`config/bands.toml`; default `graded`).

| Profile | Reviewers | Calls per written submission |
|---|---|---|
| `graded` | `3-language`, `3-writing`, `3-reading`, likewise for grades 4 and 5, then `6+-writing`, `6+-reading` | 3-5 reviewed per grade and strand at low reasoning with `per_standard.v2` (a higher evidence bar; `full` and `lean` stay on v1); 6+ by downward search at high reasoning, one call per round (course grade 9: 9 + 2 reviewers, plus root cause) |
| `full` | `3-5-language`, `3-5-writing`, `3-5-reading`, `6+-writing`, `6+-reading` | 5 reviewers, plus root cause, plus Jev: up to 7 |
| `lean` | `3-5-all`, `6+-all` | 2 reviewers: 2 to 4 |

Multiple choice makes 0 calls. For free-tier testing, `LLM_TPM` (tokens per minute) and `LLM_RPD` (requests per
day) cap usage; blank means unlimited. `LLM_RPD` counts per process and per local date: a restart resets the
count, and the provider's own day may reset in another timezone. When the daily budget runs out, no new review starts and the run finishes
with `paused: daily_quota`; the next trigger resumes (already-reviewed attempts are skipped). A provider 429 is
retried with backoff and is not a failure. Usage (input and output tokens per reviewer) is recorded on every
result; dollars are computed by the lab's evaluation from `config/pricing.toml`.

### Reasoning level

The model's thinking dominates output tokens, so its level is tunable: `default` (send nothing, the model's own
setting), `low`, `medium` or `high`. Precedence: the run (`review --course ... --reasoning <reviewer>=<level>`,
repeatable; the lab's `evaluate --course` takes the same flag, and the demo's Review one tab a text box), then the
reviewer's `reasoning` key in `config/bands.toml`, then `LLM_REASONING`, then default. Gemini 3 models take a thinking
level; earlier ones a token budget (1024, 4096, 16384; see `llm/gemini.py`). `ReviewResult.models` shows
`<model>@<level>` for a non-default level, and `usage` records `thinking_tokens` (already part of `output_tokens`).
The root-cause step and verification use `LLM_REASONING`.

### Settings in three tiers

| Tier | Where | What |
|---|---|---|
| Deployment | `.env` | `REVIEW_PIPELINE`, `BANDS_PROFILE`, `LLM_TPM`, `LLM_RPD`, `LLM_REASONING` (plus the usual keys) |
| Tuning | `config/bands.toml`, `conditions.toml`, `pricing.toml`; `prompts/<name>.v<N>.md` | Profiles, condition vocabulary per concept, prices, prompt templates. A new prompt version is a new file. Everything is validated at startup (errors name the file and key). An optional `reasoning` key per reviewer sets its reasoning level. `tuning_hash` covers the active profile, conditions, prompts and reasoning levels |
| Teacher/admin | `config/review-settings.toml` defaults plus Postgres overrides (`review_setting_override`, append-only) | `mc_finding_min_picks`, `root_cause_enabled`, `notable_strengths_enabled`, `extension_check_enabled` (not built: `true` is refused) |

A review snapshots the effective teacher/admin settings when it starts; `settings_hash` identifies the snapshot.
Settings API (bearer auth, `contracts/agentic-api.yaml`): `GET /settings`, `PUT /settings/{key}` with
`{value, changed_by}` (422 on a bad value), `GET /settings/{key}/history` (newest first). `/review-policy`
(triggers) is separate and unchanged.

### Single runs

`review_one` (`review/v2/run.py`) reviews one student's latest attempt with `ReviewOptions` (pipeline, profile,
prompt versions, root cause, verify, settings override; each defaults to the configured value). The CLI, the
service and the lab all use it. Nothing is persisted unless you ask. The trace (per reviewer: prompt version,
standards count, tokens, seconds, search rounds and the parsed reply) may echo submission text, so it stays in memory and is
written only by `review --course ... --json <path>` and by the lab's "Review one" saved runs
(`agentic_lab/local/reviews/`, git-ignored). CLI reports never include it.

Lab tools built on this: `python -m agentic_lab evaluate --course <v> [--pipeline] [--profile] [--repeat K]`
(growth-area precision/recall, working-grade accuracy, root-cause links, cost, run-to-run agreement) and the
demo's "Review one" and "Compare runs" tabs. `full`/`rag` strategies, the positional `review <student>` form, the
lab's `evaluate --classroom` and the demo's original Review and Compare reports tabs stay on the MVP pipeline.

### Known gaps

- `config/pricing.toml` prices and model ids are placeholders; verify them before trusting a cost figure.
- The live smoke test (`tests/test_live.py -k v2`) skips until the 2.00-control submissions are generated and the v2 catalog is loaded.

## Analytics (trends, class summary)

Spec: `docs/superpowers/specs/2026-11-02-trends-class-summary-design.md`. The package `src/agentic/analytics/`
turns saved verdicts into a per-student, per-concept picture and a class summary. It reads ids, codes, levels,
booleans, confidences, dates and `finding.condition` only, never student text, and it never scores: a status
rests on a count string such as `"2/3"`, not a percentage.

**What it computes**
- An evidence timeline per student and concept (`concept_code` is the first four `.` segments of a standard code).
  Levels: 0 Meets, 1 Approaching, 2 Developing, 3 Beginning. A finding a teacher disagreed with or dismissed
  counts as `meets`.
- A status per student and concept: `needs_attention`, `secure`, `emerging` or `too_little_evidence`, plus the
  current level and a trend (`improving`, `declining`, `steady`, `unclear`). A trend is reported only when it
  passes the noise rule, built from the review's own run-to-run variation (`review_flip_rate`).
- A weekly distribution of levels per class and concept (for the heatmap), common gaps, reteach groups (students
  who share a root concept, found by following `finding_link` to the lowest-grade root; a student with no links is
  grouped by their own concept) and peer-pair suggestions (secure tutors with needs-attention tutees).
- Pure functions (`timeline.py`, `status.py`, `summary.py`) import no Adapter or vendor SDK. `base.py` holds the
  Targets `ResultsReader` and `AnalyticsWriter`; `postgres.py` and `memory.py` are their Adapters;
  `run.py` has `analyze_classroom` and `analyze_student`.

**Settings** (teacher/admin, `config/review-settings.toml` defaults plus Postgres overrides, same API as the other
settings): `attention_hits` 2, `attention_window` 3, `secure_hits` 3, `secure_window` 4, `trend_window` 3,
`review_flip_rate` 0.10 (a float, 0.01 to 0.35), `group_min_size` 2, `max_tutees` 2. `PUT /settings/{key}` also
checks keys against each other.

**Storage (migration `V8`).** Append-only snapshots, one set per analysis under its own `review_run`
(`run_type = 'trend_analysis'`): `concept_status`, `class_concept_week`, `reteach_group` (+ `reteach_group_member`),
`peer_pair_suggestion`, `peer_pair_seen`. The team's Backend reads the views `concept_status_latest`,
`class_concept_week_latest`, `reteach_group_latest` and `peer_pair_latest` (they share the helper view
`classroom_run_latest`). The only UPDATE is the teacher's pairing decision on `peer_pair_suggestion` (`status`,
`decided_by`, `decided_at`), written by the Backend. `reset-reviews` also deletes a classroom's analytics rows.

**How analyses run**
- `POST /trend-analyses` (see HTTP service) runs one on request.
- Automatically: a webhook review debounces a classroom analysis (`ANALYSIS_DELAY_S`, default 60 s; a burst of
  reviews gives one analysis), and a finished sweep requests one. `AnalysisScheduler`
  (`service/analysis_scheduler.py`) runs at most one analysis per classroom at a time and keeps one queued
  follow-up.
- CLI: `analyze --classroom <v> [--student <prefix>] [--as-of YYYY-MM-DD] [--no-save]`.

## Setup

```bash
python -m venv agentic/.venv
agentic/.venv/Scripts/python -m pip install -e "agentic[dev]"
cp .env.example .env
```

`.env` is read from this folder, else its parent (the lab keeps one at the repo root). Relative paths in it
resolve against this folder.

Variables to fill in `.env` (names only): `LLM_PROVIDER`, `LLM_MODEL`, `LLM_API_KEY`, `EMBEDDING_PROVIDER`,
`EMBEDDING_MODEL`, `EMBEDDING_API_KEY`, `EMBEDDING_DIM`, `JEV_API_KEY`, `DATABASE_URL_LOCAL`, plus the optional
`DECISION_PROVIDER`, `JEV_MODEL`, `JEV_BASE_URL`, `JEV_MISS_THRESHOLD`, `LLM_BASE_URL`, `LLM_RPM`, `LLM_TIMEOUT_S`, `DB_STATEMENT_TIMEOUT_S`, `EMBEDDING_RPM`, `REVIEW_STRATEGY`, `RETRIEVAL_K`, `VERIFY`,
`REVIEW_PIPELINE`, `BANDS_PROFILE`, `LLM_TPM`, `LLM_RPD`, `LLM_REASONING`,
`CLASSROOM`, `ANALYSIS_DELAY_S`, `LMS_SOURCE`, `DATA_DIR`, `REPORTS_DIR`, `STANDARDS_FILE`, `FAKE_LLM`.

## Data tier (Postgres + pgvector)

```bash
docker compose up -d postgres
docker compose up --exit-code-from flyway flyway
```

## CLI

`agentic/.venv/Scripts/python -m agentic <command>`:

| Command | Purpose | Example |
|---|---|---|
| `review` | Review one student's essay (student hash prefix; MVP). With `--course`: one v2 review (see below) | `review 1a2b --strategy full --persist` |
| `load-standards` | Embed and load the standards spreadsheet into Postgres | `load-standards` (or `--file <xlsx>`) |
| `seed-demo` | Seed the demo teacher, classroom, students, enrollments and assignment (no reviews) | `seed-demo --classroom 1.01` |
| `reset-reviews` | DEV/DEMO only: delete one classroom's review results so it can be reviewed again (keeps standards, classroom, students) | `reset-reviews --classroom 1.01` |
| `analyze` | Trend analysis from the saved reviews: concept statuses, top gaps, reteach groups, pair suggestions. `--as-of` is the end of that day (America/Chicago); `--no-save` writes no snapshots | `analyze --classroom 1.01 --as-of 2026-11-01 --no-save` |

`generate-essays` and `evaluate` are lab commands: `python -m agentic_lab ...` (see `agentic_lab/README.md`).

`load-standards` defaults to `STANDARDS_FILE=standards/mls-ela-reviewable-v2.xlsx`, the v2 catalog (grades 3-12). The v2 pipeline needs it loaded; the MVP runs used `standards/mls-ela-writing-v1.xlsx` (the original corpus minus the non-writing strands). The full original corpus is `standards/curr-mls-standards-ela-2-12-sboe-2016.xlsx`. `standards_at_grade` is not strand-filtered, so every standard in the loaded file's grade band is reviewed. Loading a different file replaces the table's contents.

Other flags: `review --classroom`, `--no-verify`, `--strands`, `--assignment-context on|off`.

Single v2 review of a course-folder submission, with chosen options and no database:
`review --course 2.00-control --assignment ela9v2-a02 --student 1a2b [--pipeline mvp|v2] [--profile lean]
[--prompt 3-5-language=v2] [--no-root-cause] [--no-verify] [--json out.json] [--persist]`. It prints verdict counts
by reviewer, findings with support level and root links, strengths, tokens and calls (codes and counts only).
`--json` writes the full result including the trace (which may quote the submission) to the file you name.
Without `--persist` nothing is saved. The positional `review <student>` form stays the MVP review.

### MVP: assignments, targets, teacher prompt, and verdicts

(The MVP pipeline keeps its internal names: `weakness`, `severity`. v2 says `growth_area` and support level.)
A review judges each in-scope standard as `meets`, `weakness` (with severity approaching / developing /
beginning and cited paragraphs) or `not_evident` (not called for, or no evidence in the essay). Only weaknesses
become findings; `not_evident` standards are listed but never verified.

An assignment file can declare the teacher's wording and the standards it targets:

```json
{"id": "rl-theme", "type": "rl", "title": "Theme in Animal Farm",
 "prompt": "Choose ONE important theme ...", "targets": ["9-10.RL.1.A", "9-10.RL.1.D", "..."]}
```

- `targets` are the review's outer boundary (a `TargetFilter` Decorator stacked on `StrandFilter`).
- The reviewer sees `title` + `prompt` as context only (judge against the standard, never "didn't follow
  instructions"). Switch: `ASSIGNMENT_CONTEXT=1|0` or `--assignment-context on|off`.
- Per-target `notes` (e.g. `{"9-10.RL.1.D": "one theme only"}`) tell the reviewer and Jev which part of a
  standard the assignment calls for.
- Starter templates live in `agentic_lab/data/synthetic/assignments/`.

### Assignment types and review strands (assignments without targets)

Each classroom version is one assignment. An optional `classroom_<v>.assignment.json` in `DATA_DIR` says which:

```json
{"type": "rl", "text": "Animal Farm (George Orwell)", "strands": ["W", "RL"]}
```

| type | essay | strands reviewed by default |
|---|---|---|
| `argument` (no file) | generic argumentative essay | W |
| `rl` | literary analysis (default text: *Animal Farm*) | W, RL |
| `ri` | informational-text analysis (default: Orwell's 1947 preface to *Animal Farm*) | W, RI |

Reviews use those strands unless overridden by `REVIEW_STRANDS`
in `.env` or `--strands` (e.g. `--strands RL,RI`); writing (W) is always included. The file describes the
assignment, not the answers, so the reviewer may read it; it never sees labels. Strand filtering is a
Decorator (`standards/strand_filter.py`) around the standards source.

### Course folders (`LMS_SOURCE=coursedir`)
`LMS_SOURCE` picks the LMS Adapter: `jsonl` (default, `JsonlLMSClient`, the classroom files above) or `coursedir`
(`CourseDirLMSClient`, v2 course folders under `DATA_DIR/courses/<version>/`; the course id is `classroom_<version>`
and LMS assignment ids are `<file id>@<version>`). Course folders have no `classroom_<v>.assignment.json`, so
their reviews use strand W only unless `REVIEW_STRANDS` or `--strands` says otherwise. Under `REVIEW_PIPELINE=mvp`
multiple-choice assignments are not reviewed: the service skips their submissions with reason `unsupported_type`,
in sweeps and for the grading webhook. v2 (the default) reviews them by looking up the answers.

For v2 course folders: `LMS_SOURCE=coursedir DATA_DIR=../agentic_lab/data/synthetic` (DATA_DIR resolves against
`agentic/`). `seed-demo --classroom 2.00-control` seeds both twins (it prints 2 classrooms) from the course folder's
roster and assignments; it never invents submissions for students who were not assigned a piece of work.
`review <student>` reads the classroom JSONL files only; it does not work on course folders (use a sweep or the
webhook). Until the agentic spec lands, v2 reviews use strand W only (the default `standards_at_grade` over the
current MVP standards file); do not switch `STANDARDS_FILE` to the v2 reviewable xlsx yet, because loading it
replaces the `standard` table that existing findings reference.

## HTTP service

`contracts/agentic-api.yaml`, built with FastAPI (`src/agentic/service/`).

```bash
agentic/.venv/Scripts/python -m agentic serve --port 8000     # local
docker compose up -d --build agentic                          # container (from the repo root)
```

| Endpoint | Auth | What |
|---|---|---|
| `GET /health` | none | `{"status": "ok"}` |
| `POST /events/submission-graded` | `X-SASS-Webhook-Secret` = `WEBHOOK_SECRET` | LMS grading webhook. 202 with a `run_id`; the review runs in the background. One run per (submission, attempt): repeats return `duplicate: true`; a failed run can be retried |
| `GET /runs/{run_id}` | `Bearer AGENTIC_API_TOKEN` | Run status, counts, skips; `paused` is `daily_quota` when the daily budget ran out; `error` is an exception class name only |
| `GET`/`PUT /review-policy` | bearer | In-memory policy. `on_grade: false` makes the webhook ignore events |
| `POST /sweeps` | bearer | Review every submission in a course (`{"course_id": ...}`, optional `assignment_id`; no body = every course). 202 + `run_id`; counts on `GET /runs/{id}` update as each review finishes. Already-reviewed attempts are skipped; a sweep of the same scope still running is returned with `duplicate: true`. Backend integration notes: `docs/superpowers/specs/2026-10-14-review-button-demo-design.md` (lab repo) |
| `GET /settings`, `PUT /settings/{key}`, `GET /settings/{key}/history` | bearer | Teacher/admin review settings (see Framework v2) |
| `POST /trend-analyses` | bearer | Trend analysis of a classroom or one student (`{"course_id", "scope", "user_id", "as_of"}`) from the saved reviews. 202 + `run_id` (`GET /runs/{id}`); unknown course or student is a 422. Analyses also run automatically after reviews |

- Results are not served over HTTP: with `PERSIST_RESULTS=1` (default) each review is saved through the
  `ResultsWriter` (Postgres), and consumers read findings there by `lms_submission_id` + `attempt`.
- A blank `AGENTIC_API_TOKEN` or `WEBHOOK_SECRET` refuses every call (fail closed).
- The service reads submissions through `JsonlLMSClient` (course `classroom_<v>` = classroom version `<v>`);
  compose mounts `agentic_lab/data/synthetic` at `/app/data`. An HTTP mock-LMS Adapter replaces it later.
- Runs live in memory (lost on restart). No request content is logged or echoed in errors.
- Speed: a sweep reviews `REVIEW_CONCURRENCY` essays at once (default 1). All parallel reviews share one
  `LLM_RPM` budget, so raise both together on a paid model tier (e.g. 5 and 60).

## Demo

The Streamlit demo is in the lab (`agentic_lab/README.md`). With no API keys, the CLI also runs on fakes:

```bash
FAKE_LLM=1 DATA_DIR=../agentic_lab/data/synthetic agentic/.venv/Scripts/python -m agentic review <student>
```

## Tests

```bash
agentic/.venv/Scripts/python -m pytest agentic/tests -q            # default: no network, no database
agentic/.venv/Scripts/python -m pytest agentic/tests -q -m db      # needs Postgres running (skips otherwise)
agentic/.venv/Scripts/python -m pytest agentic/tests -q -m live    # needs API keys; costs a little
```

`tests/test_live.py -k v2` runs one 2.00-control submission through v2 (needs Postgres with the v2 catalog loaded).
Live tests skip when their key or model is empty (Gemini needs `LLM_API_KEY`+`LLM_MODEL` /
`EMBEDDING_API_KEY`+`EMBEDDING_MODEL`; Jev needs `JEV_API_KEY`).

## Privacy

Synthetic data only. SASS never stores essay text. Nothing is persisted but IDs, observations
(paragraph, standard, reason of at most 12 words), findings and teacher responses. Note that with the
free-tier Gemini and TypeSafe Jev, essay text is sent to those providers during a review.

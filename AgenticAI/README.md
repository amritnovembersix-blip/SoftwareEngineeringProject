# agentic/

Agentic essay review for SASS (ELA MVP). It reads a student's essay from the mock LMS (local files),
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
| `standards/` | `mls-ela-writing-v1.xlsx` (MVP) and the full original corpus |
| `data/` | Default `DATA_DIR` for the file-backed LMS (`JsonlLMSClient`); empty here, the lab points elsewhere |
| `local/` | Git-ignored output (`local/reports/`) |

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
`DECISION_PROVIDER`, `JEV_MODEL`, `JEV_BASE_URL`, `JEV_MISS_THRESHOLD`, `LLM_BASE_URL`, `LLM_RPM`, `EMBEDDING_RPM`, `REVIEW_STRATEGY`, `RETRIEVAL_K`, `VERIFY`,
`CLASSROOM`, `DATA_DIR`, `REPORTS_DIR`, `STANDARDS_FILE`, `FAKE_LLM`.

## Data tier (Postgres + pgvector)

```bash
docker compose up -d postgres
docker compose up --exit-code-from flyway flyway
```

## CLI

`agentic/.venv/Scripts/python -m agentic <command>`:

| Command | Purpose | Example |
|---|---|---|
| `review` | Review one student's essay (student hash prefix) | `review 1a2b --strategy full --persist` |
| `load-standards` | Embed and load the standards spreadsheet into Postgres | `load-standards` (or `--file <xlsx>`) |
| `seed-demo` | Seed the demo teacher, classroom, students and assignment | `seed-demo --classroom 1.01` |

`generate-essays` and `evaluate` are lab commands: `python -m agentic_lab ...` (see `agentic_lab/README.md`).

`load-standards` defaults to `STANDARDS_FILE=standards/mls-ela-writing-v1.xlsx`, the MVP standards file
(the original corpus minus the non-writing strands; it still includes Reading RI/RL). The full original corpus is
`standards/curr-mls-standards-ela-2-12-sboe-2016.xlsx`. `standards_at_grade` is not strand-filtered, so every
standard in the loaded file's grade band is reviewed. Loading a different file replaces the table's contents.

Other flags: `review --classroom`, `--no-verify`, `--strands`, `--assignment-context on|off`.

### Assignments: targets, teacher prompt, and verdicts

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
| `GET /runs/{run_id}` | `Bearer AGENTIC_API_TOKEN` | Run status, counts, skips; `error` is an exception class name only |
| `GET`/`PUT /review-policy` | bearer | In-memory policy. `on_grade: false` makes the webhook ignore events |
| `POST /sweeps`, `/trend-analyses` | bearer | 501 `not_implemented` for now |

- Results are not served over HTTP: with `PERSIST_RESULTS=1` (default) each review is saved through the
  `ResultsWriter` (Postgres), and consumers read findings there by `lms_submission_id` + `attempt`.
- A blank `AGENTIC_API_TOKEN` or `WEBHOOK_SECRET` refuses every call (fail closed).
- The service reads submissions through `JsonlLMSClient` (course `classroom_<v>` = classroom version `<v>`);
  compose mounts `agentic_lab/data/synthetic` at `/app/data`. An HTTP mock-LMS Adapter replaces it later.
- Runs live in memory (lost on restart). No request content is logged or echoed in errors.

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

Live tests skip when their key or model is empty (Gemini needs `LLM_API_KEY`+`LLM_MODEL` /
`EMBEDDING_API_KEY`+`EMBEDDING_MODEL`; Jev needs `JEV_API_KEY`).

## Privacy

Synthetic data only. SASS never stores essay text. Nothing is persisted but IDs, observations
(paragraph, standard, reason of at most 12 words), findings and teacher responses. Note that with the
free-tier Gemini and TypeSafe Jev, essay text is sent to those providers during a review.

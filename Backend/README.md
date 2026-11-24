# Backend

FastAPI service between the frontend and everything else. The frontend calls only this API; the
backend reads the shared Postgres (schema in `AgenticAI/db/migrations`) and calls the agentic
service (`AgenticAI/`) to start reviews.

## All endpoints

Every endpoint except `/health` and `/auth/login` needs `Authorization: Bearer <token>`. A teacher
sees only their own classes and students; an admin sees everything (see *Who can see what*).

| Method | Path | What it's for | Section |
|---|---|---|---|
| GET | `/health` | Is the backend able to work (database, agentic service) | When something is down |
| POST | `/auth/login` | Log in, get a token | Login |
| GET | `/auth/me` | Who is logged in | Login |
| GET | `/dashboard` | Home page: a card per class, plus totals | Dashboard and notifications |
| GET | `/notifications` | The bell: what needs attention now | Dashboard and notifications |
| GET | `/classrooms` | The teacher's classes | (below) |
| GET | `/classrooms/{id}/students` | Students in a class, with their latest review | (below) |
| POST | `/classrooms/{id}/reviews` | Start "Review class" | Review class progress |
| GET | `/reviews/{run_id}` | Progress of a class review | Review class progress |
| GET | `/students/{id}/findings` | What the AI found, with confidence and the teacher's answer | Findings, strengths and teacher answers |
| GET | `/students/{id}/strengths` | What the student did well | Findings, strengths and teacher answers |
| POST | `/findings/{id}/responses` | Agree, disagree or dismiss a finding | Findings, strengths and teacher answers |
| GET | `/students/{id}/concepts` | Status on every skill (FR6) | Student skill status (FR6) |
| GET | `/students/{id}/concept-history` | Each skill's level over time | Student skill status (FR6) |
| GET | `/classrooms/{id}/peer-pairs` | Suggested peer tutoring pairs (FR7) | Peer tutoring pairs (FR7) |
| PATCH | `/peer-pairs/{id}` | Approve or reject a pair | Peer tutoring pairs (FR7) |
| GET | `/classrooms/{id}/concept-weeks` | Heatmap: skills × weeks | Class views |
| GET | `/classrooms/{id}/reteach-groups` | Students who share a root gap | Class views |
| GET | `/comparison?classrooms=a,b` | Two classes side by side | Class views |

`tests/test_readme.py` fails if an endpoint is added without a row here.

## Set up from scratch

Run these from the repo root. They match what CI does (`.github/workflows/ci.yml`).

**1. Database.** Postgres with pgvector, user `sass` / password `change-me`, database `sass`.
Apply Justin's migrations in order, then the backend's own table:

```bash
for f in $(ls AgenticAI/db/migrations/V*.sql | sort -V); do
  psql -h localhost -U sass -d sass -v ON_ERROR_STOP=1 -f "$f"
done
psql -h localhost -U sass -d sass -v ON_ERROR_STOP=1 -f Backend/migrations/001_app_user.sql
```

("already exists" errors only mean a migration was applied before.)

**2. Demo data** (no API key needed):

```bash
pip install -e "AgenticAI[dev]" -e "AgenticLab[dev]" -e "Backend[dev]"
python -m agentic seed-demo --classroom 1.01   # the MVP class: 20 students, not reviewed yet
python -m agentic_lab fill-demo                # English 9 Period 1 and 2: 18 students each,
                                               # 11 weeks of reviews and Justin's analysis
```

`fill-demo` replaced `Backend/scripts/fill_demo_data.sh`, which was removed. To put the MVP class
back to "not reviewed" after a demo: `cd AgenticAI && python -m agentic reset-reviews --classroom 1.01`.

**3. Settings and accounts:**

```bash
cd Backend
cp .env.example .env
python -c "import secrets; print(secrets.token_urlsafe(48))"   # paste as JWT_SECRET in .env
python scripts/create_user.py --email admin@sass.test --name "Admin" --role admin
python scripts/create_user.py --email teacher@sass.test --name "Ms. Rivera" --role teacher --teacher-id demo-teacher
```

**4. Run** (the agentic service on 8000 is only needed for "Review class"):

```bash
uvicorn app.main:app --reload --port 8001
```

Then open http://localhost:8001/health and http://localhost:8001/docs.

## Login

Accounts live in the backend's own `app_user` table (the LMS `teacher` table has no passwords).
A **teacher** account is linked to a `teacher` row; an **admin** account is not.

Create accounts with `scripts/create_user.py` (see *Set up from scratch*). It asks for the password
and stores only a bcrypt hash. Teacher ids: `psql "$DATABASE_URL" -c "SELECT id, name FROM teacher;"`

| Endpoint | What it does |
|---|---|
| `POST /auth/login` | `{"email", "password"}` → `{"access_token", "token_type": "bearer", "expires_in", "user"}`; wrong email or password → 401 |
| `GET /auth/me` | The logged-in user. Send `Authorization: Bearer <token>`; missing, invalid or expired token → 401 |

### Who can see what

Every data endpoint needs `Authorization: Bearer <token>`; only `/health`, `/auth/login` and
`/docs` are open. Without a valid token: **401**.

- An **admin** sees every classroom and student.
- A **teacher** sees only the classrooms they teach and the students enrolled in them. Another
  teacher's classroom or student: **403**. Something that doesn't exist: **404** (checked first).
- `GET /classrooms` lists only the teacher's own classrooms.
- Disabling an account (`is_active = false`) takes effect on the next request.

To protect a new endpoint, add `user: CurrentUser = Depends(current_user)` (or `Depends(require_admin)`)
and call `require_classroom(user, id)` / `require_student(user, id)` from `app.access`.

In tests, `tests/conftest.py` logs every request in as a test admin. Tests about login and access
use `pytestmark = pytest.mark.real_auth` and send real tokens (`tests/test_access.py`).

## Student skill status (FR6)

| Endpoint | What it returns |
|---|---|
| `GET /students/{id}/concepts` | `{student_id, as_of, summary, concepts}`: the latest status per concept, most urgent first. Each concept has `status` (+`status_label`), `current_level` 0–3 (+`level_label`: Meets, Approaching, Developing, Beginning), `basis` (e.g. `2/3`), `trend`, `strand`, `concept_name`, and `skill_code` (the code without its grade, to group 7.W.3.A and 9-10.W.3.A once). `summary` counts concepts per status. Optional `?classroom_id=` |
| `GET /students/{id}/concept-history` | One entry per concept with `points`: the level on each reviewed piece of work, oldest first, with its assignment and `week_start` (Monday). Same series as the analysis, so the chart matches the status. Optional `?concept_code=`, `?classroom_id=` |

No percentages or scores: SASS doesn't grade. `needs_attention` means Developing or Beginning on at
least 2 of the last 3 pieces of work.

## Class views

| Endpoint | What it returns |
|---|---|
| `GET /classrooms/{id}/concept-weeks` | The heatmap. `weeks` (Mondays, in order), `legend`, and `skills`: one row per skill (`concept_code`, `skill_code`, `strand`, `concept_name`) with `cells` aligned to `weeks` (`null` = no data). Each cell counts students: `meets`, `approaching`, `developing`, `beginning`, `too_little_evidence`, and `needing_support` (developing + beginning). Skills needing the most support in the latest week come first. Optional `?strand=Writing` |
| `GET /classrooms/{id}/reteach-groups` | Students who share a root gap: `root_concept` (the lower-grade skill to teach) and `grade_concept` (where it shows up), with names, `student_ids` and `size`. Largest groups first |
| `GET /comparison?classrooms=a,b` | Two classes side by side: `student_count`, `students_needing_attention`, `skill_statuses` and `skill_trends` (counts), and `weekly` (student-skill results needing support, and meeting, per week). **422** unless exactly two different IDs; **403** if either isn't yours |

Counts only, never percentages. For colour in the heatmap, also show the number or an icon, so it
works without colour.

## Dashboard and notifications

| Endpoint | What it returns |
|---|---|
| `GET /dashboard` | `{user, totals, classrooms}`. One card per class the user can see: `student_count`, `students_needing_attention`, `pending_pairs`, `failed_reviews`, `last_reviewed_at`, `analysis_as_of`. `totals` adds them up for the top of the page |
| `GET /notifications` | What needs attention now, most urgent first: `failed_reviews`, then `needs_attention`, then `pending_pairs`, one per class that has something to report. Each has a stable `id` (e.g. `needs_attention:classroom_2.00-control`), `kind`, `classroom_id`, `classroom_name`, `count`, a ready-to-show `message`, and `student_ids` for needs_attention |

Both are computed on each request from the same numbers, so the bell and the cards always agree.
A teacher sees only their own classes. There is no read/unread state on the server yet: the page
can remember the `id`s it has shown.

## Findings, strengths and teacher answers

| Endpoint | What it does |
|---|---|
| `GET /students/{id}/findings` | Each finding now also has `support_level` (+`support_level_label`: Approaching, Developing, Beginning), `confidence` (0–1) with `confidence_label` (High ≥ 0.8, Medium ≥ 0.7, Low), `pattern`, `skill_state`, `is_draft: true`, and `teacher_response` (the latest answer: `stance`, `stance_label`, `reason`, `responded_at`, or `null`) |
| `GET /students/{id}/strengths` | What the student did well: standards marked as a notable strength, with `strength_note` and assignment, newest first. Optional `?assignment_id=` |
| `POST /findings/{id}/responses` | Body `{"stance": "agree" \| "disagree" \| "dismiss", "reason": "optional, ≤ 500 chars"}`. **201** with the saved answer. Only the class's teacher: **403** for another teacher or an admin, **404** unknown finding, **422** bad stance |

Findings never change. Each answer is a new `teacher_response` row and the latest one counts; Justin's
next analysis treats a disagreed or dismissed finding as "meets". Show confidence as the word, never as
a percentage, and label AI findings as drafts: the reviewer still over-flags.

## Peer tutoring pairs (FR7)

| Endpoint | What it does |
|---|---|
| `GET /classrooms/{id}/peer-pairs` | `{classroom_id, pending, pairs}`: the pairs from the latest analysis, waiting ones first. Each pair has `pair_id`, `concept_code`, `skill_code`, `strand`, `concept_name`, `tutor` and `tutee` (`student_id`, `status`, `status_label`), `status` (`suggested`, `approved`, `rejected`) with `status_label`, `decided_by`, `decided_at`. `pending` counts the suggested ones. Optional `?status=` |
| `PATCH /peer-pairs/{id}` | Body `{"status": "approved"}` or `{"status": "rejected"}`. Saves `decided_by` (the user's email) and `decided_at`, and returns the pair. **404** unknown pair, **403** another teacher's class, **409** already decided, **422** any other status |

This is the backend's only write to Justin's tables (`peer_pair_suggestion`, as his handoff says).
A decision survives later analyses: approving a pair and re-running `fill-demo` keeps it approved.

## When something is down

| Situation | What the API returns |
|---|---|
| Database down or unreachable | `503 {"detail": "the database is not reachable"}` with `Retry-After: 5`, within `DB_CONNECT_TIMEOUT` seconds |
| A query runs longer than `DB_STATEMENT_TIMEOUT_MS` | `503 {"detail": "the database took too long to answer"}` |
| Agentic service down | `503` on "Review class" only; every other page still works |

`GET /health` checks both dependencies: `{"status": "ok" \| "degraded" \| "down", "database": ..., "agentic": ...}`.
It returns 200 for `ok` and `degraded` (agentic down, pages still work) and 503 for `down` (no database).

## Test

```bash
cd Backend
pytest -q
```

A run counts only with **0 failed and 0 skipped**. Tests that need data skip with a reason
(`pytest -rs` shows it) when the database or the demo data is missing: do steps 1 and 2 above.
Ordinary tests run as a test admin (`tests/conftest.py`); login and access tests send real tokens.

## Docker

```bash
docker build -t sass-backend .
docker run -p 8001:8001 --env-file .env sass-backend
```

## Layout

```
app/
  main.py        # creates the app, CORS, includes routers
  config.py      # settings from env / .env
  auth.py        # password hashing, login tokens, current_user / require_admin
  access.py      # who may see which classroom and student
  db.py          # query / execute with time limits; DatabaseUnavailable -> 503
  agentic_client.py  # the only code that calls the agentic service
  routers/       # one file per area: health, auth, classrooms, findings, concepts,
                 # peer_pairs, dashboard, class_views, reviews
migrations/      # the backend's own tables (app_user)
scripts/         # create_user.py
tests/
```

Port 8001 is used so it runs alongside the agentic service on 8000.

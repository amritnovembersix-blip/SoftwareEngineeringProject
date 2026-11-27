# Backend

FastAPI service between the frontend and everything else. The frontend calls only this API; the
backend reads the shared Postgres (schema in `AgenticAI/db/migrations`) and calls the agentic
service (`AgenticAI/`) to start reviews.

## Run locally

```bash
cd Backend
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
uvicorn app.main:app --reload --port 8001
```

Then open http://localhost:8001/health and http://localhost:8001/docs.

## Login

Accounts live in the backend's own `app_user` table (the LMS `teacher` table has no passwords).
A **teacher** account is linked to a `teacher` row; an **admin** account is not.

One-time setup, after AgenticAI's migrations:

```bash
psql "postgresql://sass:change-me@localhost:5432/sass" -v ON_ERROR_STOP=1 -f migrations/001_app_user.sql
python -c "import secrets; print(secrets.token_urlsafe(48))"   # paste as JWT_SECRET in .env
python scripts/create_user.py --email admin@sass.test --name "Admin" --role admin
python scripts/create_user.py --email teacher@sass.test --name "Ms. Rivera" --role teacher --teacher-id <id>
```

`create_user.py` asks for the password and stores only a bcrypt hash. Teacher ids:
`psql "$DATABASE_URL" -c "SELECT id, name FROM teacher;"`

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
pytest -q
```

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
  routers/       # one file per area (health, auth, classrooms, findings, reviews ...)
migrations/      # the backend's own tables (app_user)
scripts/         # fill_demo_data.sh, create_user.py
tests/
```

Port 8001 is used so it runs alongside the agentic service on 8000.

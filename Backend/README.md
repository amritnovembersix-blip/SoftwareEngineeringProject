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

To protect an endpoint, add `user: CurrentUser = Depends(current_user)` (or `Depends(require_admin)`)
from `app.auth`.

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

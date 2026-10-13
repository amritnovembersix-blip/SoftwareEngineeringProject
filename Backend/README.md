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
  routers/       # one file per area (health, classrooms, findings, reviews ...)
tests/
```

Port 8001 is used so it runs alongside the agentic service on 8000.

"""GET /health: is the backend able to do its job right now?

- "ok": the database and the agentic service both answer.
- "degraded": the database answers but the agentic service doesn't. Pages and findings still
  work; only "Review class" fails (with a clear 503).
- "down": the database doesn't answer, so nothing works. Returns 503, so a monitor or load
  balancer sees the outage instead of a misleading "ok".
"""
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app import agentic_client, db

router = APIRouter(tags=["health"])


@router.get("/health")
def health():
    database = db.ping()
    agentic = agentic_client.is_up()
    if not database:
        status = "down"
    elif not agentic:
        status = "degraded"
    else:
        status = "ok"
    body = {
        "status": status,
        "database": "ok" if database else "down",
        "agentic": "ok" if agentic else "down",
    }
    return JSONResponse(body, status_code=503 if status == "down" else 200)

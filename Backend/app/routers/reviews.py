"""The "Review class" button: start a review of a whole class and report its progress."""
from fastapi import APIRouter, HTTPException

from app import agentic_client, db
from app.agentic_client import AgenticError

router = APIRouter(tags=["reviews"])


@router.post("/classrooms/{classroom_id}/reviews", status_code=202)
def start_class_review(classroom_id: str) -> dict:
    """Ask the agentic service to review every essay in this class. Returns at once with a run_id;
    the reviews happen in the background. Clicking again while it runs returns the same run_id."""
    rows = db.query("SELECT lms_course_id FROM classroom WHERE id = %s", (classroom_id,))
    if not rows:
        raise HTTPException(status_code=404, detail="classroom not found")
    try:
        run = agentic_client.start_sweep(rows[0]["lms_course_id"])
    except AgenticError as err:
        raise HTTPException(status_code=err.status, detail=err.message) from None
    return {"run_id": run["run_id"], "already_running": run.get("duplicate", False)}


@router.get("/reviews/{run_id}")
def review_progress(run_id: str) -> dict:
    """Progress for the UI: done = reviewed + skipped (already reviewed), out of total."""
    try:
        run = agentic_client.get_run(run_id)
    except AgenticError as err:
        raise HTTPException(status_code=err.status, detail=err.message) from None
    return {
        "run_id": run["run_id"],
        "status": run["status"],  # queued -> running -> succeeded, or failed
        "done": run.get("completed_count", 0) + run.get("skipped_count", 0),
        "total": run.get("submission_count", 0),
        "error": run.get("error"),
    }

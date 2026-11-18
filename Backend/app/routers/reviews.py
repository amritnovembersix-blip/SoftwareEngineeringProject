"""The "Review class" button: start a review of a whole class and report its progress."""
from fastapi import APIRouter, Depends, HTTPException

from app import agentic_client, db
from app.access import require_classroom
from app.agentic_client import AgenticError
from app.auth import CurrentUser, current_user

router = APIRouter(tags=["reviews"])


@router.post("/classrooms/{classroom_id}/reviews", status_code=202)
def start_class_review(classroom_id: str, user: CurrentUser = Depends(current_user)) -> dict:
    """Ask the agentic service to review every essay in this class. Returns at once with a run_id;
    the reviews happen in the background. Clicking again while it runs returns the same run_id."""
    require_classroom(user, classroom_id)
    rows = db.query("SELECT lms_course_id FROM classroom WHERE id = %s", (classroom_id,))
    try:
        run = agentic_client.start_sweep(rows[0]["lms_course_id"])
    except AgenticError as err:
        raise HTTPException(status_code=err.status, detail=err.message) from None
    return {"run_id": run["run_id"], "already_running": run.get("duplicate", False)}


@router.get("/reviews/{run_id}")
def review_progress(run_id: str, user: CurrentUser = Depends(current_user)) -> dict:
    """Progress for the UI: done = reviewed + skipped (already reviewed), out of total.

    Needs a login. The run_id is a random UUID the teacher got from starting the review, and the
    response holds only counts and a status, no student data."""
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

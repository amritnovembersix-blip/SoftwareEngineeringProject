"""The "Review class" button: start a review of a whole class and report its progress."""
import re

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


# The agentic service reports failures only as "3 of 18 reviews failed (TimeoutError)" (the class
# name, never a message, which could quote essay text).
_FAILED = re.compile(r"^(\d+) of (\d+) reviews failed")


def describe(run: dict) -> dict:
    """Turn the agentic service's run into numbers and one sentence for the teacher.

    done = reviewed now + skipped (already reviewed before). failed = reviews that raised an error.
    not_started = left untouched because the AI's daily limit ran out (the run is "paused"); clicking
    Review class again later finishes them, and nothing already reviewed is redone."""
    total = run.get("submission_count", 0)
    completed, skipped = run.get("completed_count", 0), run.get("skipped_count", 0)
    done = completed + skipped
    finished = run["status"] in ("succeeded", "failed")
    match = _FAILED.match(run.get("error") or "")
    failed = int(match.group(1)) if match else 0
    quota = run.get("paused") == "daily_quota"
    not_started = max(total - done - failed, 0) if finished else 0
    if run["status"] == "failed" and not match:
        # The whole run broke before reviewing (not one essay's error): everything left is undone.
        not_started = max(total - done, 0)

    essays = "essay" if total == 1 else "essays"
    if not finished:
        message = f"Reviewing… {done} of {total} done."
    elif not failed and not not_started:
        message = f"All {total} {essays} reviewed."
    else:
        parts = [f"{done} of {total} done"]
        if failed:
            parts.append(f"{failed} failed")
        if quota and not_started:
            parts.append(f"{not_started} waiting because the AI's daily limit was reached")
        elif not_started:
            parts.append(f"{not_started} not reviewed")
        message = ", ".join(parts) + "."
        if quota:
            message += " Click Review class again tomorrow to finish; reviewed essays are kept."
        else:
            message += " Click Review class again to retry; reviewed essays are kept."
    return {
        "run_id": run["run_id"],
        "status": run["status"],  # queued -> running -> succeeded, or failed
        "done": done,
        "total": total,
        "completed": completed,
        "skipped": skipped,
        "failed": failed,
        "not_started": not_started,
        "quota_reached": quota,
        "message": message,
        "error": run.get("error"),
    }


@router.get("/reviews/{run_id}")
def review_progress(run_id: str, user: CurrentUser = Depends(current_user)) -> dict:
    """Progress for the UI, with a ready-to-show `message` such as "12 of 18 done, 1 failed."

    Needs a login. The run_id is a random UUID the teacher got from starting the review, and the
    response holds only counts and a status, no student data."""
    try:
        run = agentic_client.get_run(run_id)
    except AgenticError as err:
        raise HTTPException(status_code=err.status, detail=err.message) from None
    return describe(run)

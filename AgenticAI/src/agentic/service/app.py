"""HTTP service (FastAPI) for contracts/agentic-api.yaml.

Built now: /health, the grading webhook (on_grade), POST /sweeps (manual), GET /runs/{run_id},
GET/PUT /review-policy. Not yet: /trend-analyses answers 501.

Reviews run after the 202 response (FastAPI background task, worker thread). Nothing here logs
or returns request content: errors carry codes and field names only.
"""
import hmac
import threading
from collections.abc import Callable

from fastapi import BackgroundTasks, Depends, FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from agentic import wiring
from agentic.config import Settings
from agentic.lms.base import LMSClient
from agentic.models import SubmissionRef
from agentic.results.base import ResultsWriter
from agentic.review.graph import Reviewer
from agentic.service.policy import DEFAULT_POLICY, ReviewPolicy, policy_error
from agentic.service.runs import RunStore, Skip, now

ReviewerFactory = Callable[[Settings, str], Reviewer]  # (settings, course_id) -> Reviewer
WriterFactory = Callable[[Settings], ResultsWriter]
LMSFactory = Callable[[Settings, str], LMSClient]  # (settings, course_id) -> LMSClient


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, details: dict | None = None):
        self.status, self.code, self.message, self.details = status, code, message, details


class EventMetadata(BaseModel):
    event_name: str = Field(pattern="^submission_graded$")
    event_time: str


class EventBody(BaseModel):
    submission_id: str
    assignment_id: str
    course_id: str
    user_id: str
    attempt: int = Field(ge=1)


class SubmissionGradedEvent(BaseModel):
    metadata: EventMetadata
    body: EventBody


class SweepRequest(BaseModel):
    course_id: str | None = None
    assignment_id: str | None = None  # requires course_id


class TriggerAccepted(BaseModel):
    run_id: str | None
    duplicate: bool
    ignored: bool
    reason: str | None = None


def _error(status: int, code: str, message: str, details: dict | None = None) -> JSONResponse:
    err = {"code": code, "message": message}
    if details:
        err["details"] = details
    return JSONResponse(status_code=status, content={"error": err})


def _same(given: str | None, expected: str) -> bool:
    """Constant-time compare; an unset secret rejects everything (fail closed)."""
    return bool(expected) and given is not None and hmac.compare_digest(given.encode(), expected.encode())


def create_app(settings: Settings | None = None, reviewer_factory: ReviewerFactory | None = None,
               writer_factory: WriterFactory | None = None, lms_factory: LMSFactory | None = None,
               course_lister: Callable[[Settings], list[str]] | None = None) -> FastAPI:
    s = settings or Settings.load()
    shared: dict = {}
    lock = threading.Lock()

    def default_reviewer(st: Settings, course_id: str) -> Reviewer:
        """One standards connection shared by all reviews (psycopg connections are thread-safe)."""
        if st.fake_llm:
            return wiring.build_course_reviewer(st, course_id)
        with lock:
            if "conn" not in shared:
                shared["conn"] = wiring.connect(st)
        return wiring.build_course_reviewer(st, course_id, conn=shared["conn"])

    make_reviewer = reviewer_factory or default_reviewer
    make_writer = writer_factory or wiring.build_results_writer
    make_lms = lms_factory or wiring.build_course_lms
    list_courses = course_lister or wiring.list_course_ids
    runs = RunStore()
    state = {"policy": DEFAULT_POLICY}
    app = FastAPI(title="SASS Agentic Service", version="0.1.0")
    app.state.runs = runs

    @app.exception_handler(ApiError)
    async def _api_error(_req: Request, exc: ApiError):
        return _error(exc.status, exc.code, exc.message, exc.details)

    @app.exception_handler(RequestValidationError)
    async def _validation(_req: Request, exc: RequestValidationError):
        fields = [".".join(str(x) for x in e["loc"]) for e in exc.errors()]  # names only, never values
        return _error(422, "validation_failed", "invalid request", {"fields": fields})

    @app.exception_handler(StarletteHTTPException)
    async def _http(_req: Request, exc: StarletteHTTPException):
        code = {404: "not_found", 405: "method_not_allowed"}.get(exc.status_code, "http_error")
        return _error(exc.status_code, code, str(exc.detail))

    bearer = HTTPBearer(auto_error=False)

    def service_token(creds: HTTPAuthorizationCredentials | None = Depends(bearer)) -> None:
        if not _same(creds.credentials if creds else None, s.agentic_api_token):
            raise ApiError(401, "unauthorized", "missing or invalid bearer token")

    def webhook_secret(x_sass_webhook_secret: str | None = Header(default=None)) -> None:
        if not _same(x_sass_webhook_secret, s.webhook_secret):
            raise ApiError(401, "unauthorized", "missing or invalid webhook secret")

    def _save(result):
        writer = make_writer(s)  # its own connection: writes use transactions
        try:
            return writer.save(result, trigger="on_grade")
        finally:
            close = getattr(writer, "close", None)
            if close:
                close()

    def review(run_id: str, ref: SubmissionRef) -> None:
        runs.update(run_id, status="running", started_at=now())
        try:
            result = make_reviewer(s, ref.course_id).run(ref)
            outcome = _save(result) if s.persist_results else None
        except Exception as err:  # noqa: BLE001 - message may quote essay text; keep the class only
            runs.update(run_id, status="failed", error=type(err).__name__, finished_at=now())
            return
        if outcome is not None and outcome.status == "duplicate":
            runs.update(run_id, status="succeeded", skipped_count=1, finished_at=now(),
                        skips=[Skip(submission_id=ref.submission_id, attempt=ref.attempt,
                                    reason="already_reviewed")])
        else:
            runs.update(run_id, status="succeeded", completed_count=1, finished_at=now())

    def sweep(run_id: str, refs: list[SubmissionRef], skips: list[Skip]) -> None:
        """Review each submission in turn, updating the run's counts as it goes (the UI's progress)."""
        runs.update(run_id, status="running", started_at=now(), skipped_count=len(skips), skips=list(skips))
        completed, failures, reviewers = 0, [], {}
        writer = None
        try:
            writer = make_writer(s) if s.persist_results else None
            for ref in refs:
                try:
                    if writer is not None and writer.is_reviewed(ref.submission_id, ref.attempt):
                        skips.append(Skip(submission_id=ref.submission_id, attempt=ref.attempt,
                                          reason="already_reviewed"))
                    else:
                        if ref.course_id not in reviewers:
                            reviewers[ref.course_id] = make_reviewer(s, ref.course_id)
                        result = reviewers[ref.course_id].run(ref)
                        outcome = writer.save(result, trigger="manual") if writer is not None else None
                        if outcome is not None and outcome.status == "duplicate":
                            skips.append(Skip(submission_id=ref.submission_id, attempt=ref.attempt,
                                              reason="already_reviewed"))
                        else:
                            completed += 1
                except Exception as err:  # noqa: BLE001 - keep the class only; carry on with the rest
                    failures.append(type(err).__name__)
                runs.update(run_id, completed_count=completed, skipped_count=len(skips), skips=list(skips))
        except Exception as err:  # noqa: BLE001 - e.g. the database is down before any review
            failures.append(type(err).__name__)
        finally:
            close = getattr(writer, "close", None)
            if close:
                close()
        if failures:
            names = ", ".join(sorted(set(failures)))
            runs.update(run_id, status="failed", finished_at=now(),
                        error=f"{len(failures)} of {len(refs)} reviews failed ({names})")
        else:
            runs.update(run_id, status="succeeded", finished_at=now())

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.post("/events/submission-graded", status_code=202, dependencies=[Depends(webhook_secret)])
    def submission_graded(event: SubmissionGradedEvent, tasks: BackgroundTasks) -> TriggerAccepted:
        if not state["policy"].submission_review.on_grade:
            return TriggerAccepted(run_id=None, duplicate=False, ignored=True, reason="mode_disabled")
        b = event.body  # a notification only: the review fetches the attempt from the LMS
        run, duplicate = runs.claim(b.submission_id, b.attempt, "on_grade")
        if not duplicate:
            ref = SubmissionRef(course_id=b.course_id, assignment_id=b.assignment_id, user_id=b.user_id,
                                submission_id=b.submission_id, attempt=b.attempt)
            tasks.add_task(review, run.run_id, ref)
        return TriggerAccepted(run_id=run.run_id, duplicate=duplicate, ignored=False)

    @app.get("/runs/{run_id}", dependencies=[Depends(service_token)])
    def get_run(run_id: str):
        run = runs.get(run_id)
        if run is None:
            raise ApiError(404, "not_found", "no such run")
        return run.model_dump(mode="json")

    @app.get("/review-policy", dependencies=[Depends(service_token)])
    def get_policy() -> ReviewPolicy:
        return state["policy"]

    @app.put("/review-policy", dependencies=[Depends(service_token)])
    def put_policy(policy: ReviewPolicy) -> ReviewPolicy:
        code = policy_error(policy)
        if code:
            raise ApiError(422, code, "policy unchanged")
        state["policy"] = policy
        return policy

    @app.post("/sweeps", status_code=202, dependencies=[Depends(service_token)])
    def sweeps(tasks: BackgroundTasks, req: SweepRequest | None = None) -> TriggerAccepted:
        """Review the latest attempt of every submitted, non-excused submission in scope that has not
        been reviewed. Listing happens now (an unknown course is a 422); reviewing in the background.
        A sweep of the same scope that is still queued or running is returned with duplicate=True."""
        req = req or SweepRequest()
        if req.assignment_id and not req.course_id:
            raise ApiError(422, "validation_failed", "assignment_id requires course_id",
                           {"fields": ["body.course_id"]})
        refs, skips = [], []
        for course_id in [req.course_id] if req.course_id else list_courses(s):
            try:
                submissions = make_lms(s, course_id).list_submissions(course_id, req.assignment_id)
            except (LookupError, FileNotFoundError):
                raise ApiError(422, "unknown_course", "the LMS has no such course") from None
            for sub in submissions:
                if sub.excused:
                    skips.append(Skip(submission_id=sub.id, attempt=sub.attempt, reason="excused"))
                elif sub.workflow_state == "unsubmitted":
                    skips.append(Skip(submission_id=sub.id, attempt=None, reason="not_submitted"))
                else:
                    refs.append(SubmissionRef(course_id=course_id, assignment_id=sub.assignment_id,
                                              user_id=sub.user_id, submission_id=sub.id,
                                              attempt=sub.attempt, submitted_at=sub.submitted_at))
        run, duplicate = runs.create("manual", len(refs) + len(skips), (req.course_id, req.assignment_id))
        if not duplicate:
            tasks.add_task(sweep, run.run_id, refs, skips)
        return TriggerAccepted(run_id=run.run_id, duplicate=duplicate, ignored=False)

    @app.post("/trend-analyses", dependencies=[Depends(service_token)])
    def trend_analyses():
        raise ApiError(501, "not_implemented", "trend analysis is planned")

    return app

"""HTTP service (FastAPI) for contracts/agentic-api.yaml.

Built now: /health, the grading webhook (on_grade), POST /sweeps (manual), GET /runs/{run_id},
GET/PUT /review-policy, GET /settings, PUT /settings/{key}, GET /settings/{key}/history, POST /trend-analyses.

Trend analyses run in the background too. Besides POST /trend-analyses (trigger "manual"), one runs
automatically (trigger "after_review"; every run is in the RunStore as run_type "trend_analysis") when a
sweep ends, and `analysis_delay_s` after the last webhook review that succeeded (debounced). The
AnalysisScheduler keeps it to one analysis per classroom at a time.

Reviews run after the 202 response (FastAPI background task, worker thread). Nothing here logs
or returns request content: errors carry codes and field names only.
"""
import hmac
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import BackgroundTasks, Depends, FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import AwareDatetime, BaseModel, Field, StringConstraints
from starlette.exceptions import HTTPException as StarletteHTTPException

from agentic import wiring
from agentic.analytics.base import AnalyticsWriter, ResultsReader
from agentic.analytics.run import UnknownClassroom, analyze_classroom, analyze_student, everything_reviewed
from agentic.analytics.status import from_effective
from agentic.config import Settings
from agentic.llm.base import DailyQuotaExceeded
from agentic.lms.base import LMSClient
from agentic.models import ReviewResult, SubmissionRef
from agentic.results.base import ResultsWriter
from agentic.review.graph import Reviewer
from agentic.review.v2.graph import ReviewerV2
from agentic.review.v2.options import ReviewOptions
from agentic.review.v2.run import review_one as run_review_one
from agentic.service.analysis_scheduler import AFTER_REVIEW, AnalysisScheduler
from agentic.service.policy import DEFAULT_POLICY, ReviewPolicy, policy_error
from agentic.service.runs import RunStore, Skip, now
from agentic.settings.base import effective, load_defs, validate, validate_all

ReviewerFactory = Callable[[Settings, str], Reviewer | ReviewerV2]  # (settings, course_id) -> Reviewer | ReviewerV2; a test hook
WriterFactory = Callable[[Settings], ResultsWriter]
LMSFactory = Callable[[Settings, str], LMSClient]  # (settings, course_id) -> LMSClient
AnalyticsReaderFactory = Callable[[Settings], ResultsReader]
AnalyticsWriterFactory = Callable[[Settings], AnalyticsWriter]


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


Id = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]  # blank is an error, never "everything"


class SweepRequest(BaseModel):
    course_id: Id | None = None
    assignment_id: Id | None = None  # requires course_id


class TrendAnalysisRequest(BaseModel):
    scope: Literal["classroom", "student"]
    course_id: str = Field(min_length=1)
    user_id: str | None = None  # the student's LMS user id; required for student scope
    as_of: AwareDatetime | None = None  # default: everything reviewed so far


class SettingChange(BaseModel):
    value: Any
    changed_by: str = Field(min_length=1)


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
               course_lister: Callable[[Settings], list[str]] | None = None,
               analytics_reader_factory: AnalyticsReaderFactory | None = None,
               analytics_writer_factory: AnalyticsWriterFactory | None = None,
               analysis_timer_factory: Callable | None = None,
               analysis_spawn: Callable[[Callable[[], None]], None] | None = None) -> FastAPI:
    s = settings or Settings.load()
    defs = load_defs(s.config_dir)
    fake_store = wiring.build_settings_store(s) if s.fake_llm else None  # lives as long as the app

    make_writer = writer_factory or wiring.build_results_writer
    make_lms = lms_factory or wiring.build_course_lms
    list_courses = course_lister or wiring.list_course_ids
    make_analytics_reader = analytics_reader_factory or wiring.build_analytics_reader
    make_analytics_writer = analytics_writer_factory or wiring.build_analytics_writer
    # Automatic analyses need the analytics Adapters: when none are injected and the app runs on the
    # in-memory fakes (FAKE_LLM=1) or without persistence, there is no database to read from.
    auto_analysis = analytics_reader_factory is not None or (not s.fake_llm and s.persist_results)
    runs = RunStore()
    state = {"policy": DEFAULT_POLICY}
    app = FastAPI(title="SASS Agentic Service", version="0.1.0")
    app.state.runs = runs
    app.state.settings_store = fake_store  # in-memory under FAKE_LLM=1, else None (Postgres per request)

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

    @contextmanager
    def settings_store():
        """Postgres: a connection per request (closed after); fake: the app's in-memory store."""
        if fake_store is not None:
            yield fake_store
            return
        store = wiring.build_settings_store(s)
        try:
            yield store
        finally:
            store.close()

    def run_review(ref: SubmissionRef, reviewers: dict | None = None) -> ReviewResult:
        """One review through review_one, which snapshots the settings at its start. Each review has its
        own connection (standards and settings): a shared one would interleave transactions across threads.
        `reviewer_factory` (tests) replaces that; its reviewers are cached in `reviewers` when given."""
        if reviewer_factory:
            cache = reviewers if reviewers is not None else {}
            if ref.course_id not in cache:
                cache[ref.course_id] = reviewer_factory(s, ref.course_id)
            return cache[ref.course_id].run(ref)
        conn = None if s.fake_llm else wiring.connect(s)
        try:
            store = fake_store or wiring.build_settings_store(s, conn)
            return run_review_one(s, ref.course_id, ref.assignment_id, ref.user_id, ReviewOptions(), store, conn)
        finally:
            if conn is not None:
                conn.close()

    def _save(result):
        writer = make_writer(s)  # its own connection: writes use transactions
        try:
            return writer.save(result, trigger="on_grade")
        finally:
            close = getattr(writer, "close", None)
            if close:
                close()

    skip_mc = s.review_pipeline == "mvp"  # the MVP reviews written work only; v2 looks up the answers

    def is_multiple_choice(course_id: str, assignment_id: str) -> bool:
        """Under the MVP pipeline multiple-choice assignments are not reviewable."""
        lms = make_lms(s, course_id)
        return lms.get_assignment(course_id, assignment_id).submission_type == "multiple_choice"

    def review(run_id: str, ref: SubmissionRef) -> None:
        runs.update(run_id, status="running", started_at=now())
        try:
            if skip_mc and is_multiple_choice(ref.course_id, ref.assignment_id):
                runs.update(run_id, status="succeeded", skipped_count=1, finished_at=now(),
                            skips=[Skip(submission_id=ref.submission_id, attempt=ref.attempt,
                                        reason="unsupported_type")])
                return
            result = run_review(ref)
            outcome = _save(result) if s.persist_results else None
        except DailyQuotaExceeded:  # not a failure: the next trigger resumes
            runs.update(run_id, status="succeeded", paused="daily_quota", finished_at=now())
            return
        except Exception as err:  # noqa: BLE001 - message may quote essay text; keep the class only
            runs.update(run_id, status="failed", error=type(err).__name__, finished_at=now())
            return
        if outcome is not None and outcome.status == "duplicate":
            runs.update(run_id, status="succeeded", skipped_count=1, finished_at=now(),
                        skips=[Skip(submission_id=ref.submission_id, attempt=ref.attempt,
                                    reason="already_reviewed")])
        else:
            runs.update(run_id, status="succeeded", completed_count=1, finished_at=now())
            after_reviews([ref.course_id], debounce=True)

    def sweep(run_id: str, refs: list[SubmissionRef], skips: list[Skip]) -> None:
        """Review the submissions, REVIEW_CONCURRENCY at a time, updating the run's counts after each
        one (the UI's progress). Each worker thread has its own reviewer and results writer (a database
        connection carries one thread's transactions at a time); model calls share one rate limit."""
        runs.update(run_id, status="running", started_at=now(), skipped_count=len(skips), skips=list(skips))
        lock = threading.Lock()
        local = threading.local()
        completed, failures, writers = [0], [], []
        paused = threading.Event()  # the daily budget ran out: reviews not yet started are left unclaimed

        def thread_writer() -> ResultsWriter | None:
            if not s.persist_results:
                return None
            if not hasattr(local, "writer"):
                local.writer = make_writer(s)
                with lock:
                    writers.append(local.writer)
            return local.writer

        def review_ref(ref: SubmissionRef) -> None:
            if paused.is_set():
                return
            try:
                writer = thread_writer()
                if writer is not None and writer.is_reviewed(ref.submission_id, ref.attempt):
                    outcome = "skipped"
                else:
                    result = run_review(ref, local.__dict__.setdefault("reviewers", {}))
                    saved = writer.save(result, trigger="manual") if writer is not None else None
                    outcome = "skipped" if saved is not None and saved.status == "duplicate" else "done"
            except DailyQuotaExceeded:
                paused.set()
                outcome = "paused"
            except Exception as err:  # noqa: BLE001 - keep the class only; carry on with the rest
                outcome = type(err).__name__
            with lock:
                if outcome == "paused":
                    pass  # not claimed, not failed
                elif outcome == "done":
                    completed[0] += 1
                elif outcome == "skipped":
                    skips.append(Skip(submission_id=ref.submission_id, attempt=ref.attempt,
                                      reason="already_reviewed"))
                else:
                    failures.append(outcome)
                runs.update(run_id, completed_count=completed[0], skipped_count=len(skips), skips=list(skips))

        try:
            with ThreadPoolExecutor(max_workers=max(1, s.review_concurrency),
                                    thread_name_prefix="sweep") as pool:
                list(pool.map(review_ref, refs))
        finally:
            for writer in writers:
                close = getattr(writer, "close", None)
                if close:
                    close()
        pause = "daily_quota" if paused.is_set() else None
        if failures:
            names = ", ".join(sorted(set(failures)))
            runs.update(run_id, status="failed", finished_at=now(), paused=pause,
                        error=f"{len(failures)} of {len(refs)} reviews failed ({names})")
        else:
            runs.update(run_id, status="succeeded", finished_at=now(), paused=pause)
        if completed[0]:
            after_reviews([ref.course_id for ref in refs], debounce=False)

    @contextmanager
    def closing_all():
        """Collects Adapters (reader, writer) and closes each that has a close() on exit."""
        opened: list = []
        try:
            yield opened
        finally:
            for adapter in opened:
                close = getattr(adapter, "close", None)
                if close:
                    close()

    def execute_analysis(run_id: str, classroom_id: str, student_id: str | None, trigger: str, as_of) -> None:
        """One analysis for a RunStore entry. The settings are snapshotted here, at the start of the run."""
        runs.update(run_id, status="running", started_at=now())
        try:
            with settings_store() as store:
                cfg = from_effective(effective(defs, store.overrides()))
            with closing_all() as opened:
                reader = make_analytics_reader(s)
                opened.append(reader)
                writer = make_analytics_writer(s)
                opened.append(writer)
                as_of = as_of or everything_reviewed(reader, classroom_id)
                if student_id is None:
                    analyze_classroom(reader, writer, classroom_id, as_of, cfg, trigger, run_id=UUID(run_id))
                else:
                    analyze_student(reader, writer, classroom_id, student_id, as_of, cfg, trigger,
                                    run_id=UUID(run_id))
        except Exception as err:  # noqa: BLE001 - class name only: a message may echo student text
            runs.update(run_id, status="failed", error=type(err).__name__, finished_at=now())
            return
        runs.update(run_id, status="succeeded", finished_at=now())

    def automatic_analysis(classroom_id: str, trigger: str) -> None:
        """The scheduler's run_fn: its runs get RunStore entries too (listed under run_type trend_analysis)."""
        run = runs.new(trigger, "trend_analysis")
        execute_analysis(run.run_id, classroom_id, None, trigger, None)

    scheduler_args = {} if analysis_timer_factory is None else {"timer_factory": analysis_timer_factory}
    if analysis_spawn is not None:
        scheduler_args["spawn"] = analysis_spawn
    scheduler = AnalysisScheduler(automatic_analysis, delay_s=s.analysis_delay_s, **scheduler_args)
    app.state.analysis_scheduler = scheduler

    def after_reviews(course_ids, debounce: bool) -> None:
        """Ask for an analysis of each course's classroom. Never lets an analytics problem touch a review."""
        if not auto_analysis:
            return
        for course_id in sorted(set(course_ids)):
            try:
                with closing_all() as opened:
                    reader = make_analytics_reader(s)
                    opened.append(reader)
                    classroom_id = reader.classroom_for_course(course_id)
                if classroom_id is None:
                    raise UnknownClassroom(course_id)
                if debounce:
                    scheduler.debounce(classroom_id)
                else:
                    scheduler.request(classroom_id, AFTER_REVIEW)
            except Exception as err:  # noqa: BLE001 - the review stays as it is; leave a trace of the class name only
                failed = runs.new(AFTER_REVIEW, "trend_analysis")
                runs.update(failed.run_id, status="failed", error=type(err).__name__, finished_at=now())

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

    def setting_entry(key: str, rows: list) -> dict:
        source = "override" if any(r.key == key for r in rows) else "default"
        return {"key": key, "value": effective(defs, rows)[key], "source": source,
                "description": defs[key].description}

    def known(key: str) -> None:
        if key not in defs:
            raise ApiError(404, "not_found", "no such setting")

    @app.get("/settings", dependencies=[Depends(service_token)])
    def get_settings() -> list[dict]:
        with settings_store() as store:
            rows = store.overrides()
        return [setting_entry(key, rows) for key in defs]

    @app.put("/settings/{key}", dependencies=[Depends(service_token)])
    def put_setting(key: str, change: SettingChange) -> dict:
        known(key)
        with settings_store() as store:
            try:
                value = validate(defs[key], change.value)
            except ValueError as err:  # names the key and the rule
                raise ApiError(422, "validation_failed", str(err), {"fields": [key]}) from None
            proposed = {**effective(defs, store.overrides()), key: value}
            if errors := validate_all(defs, proposed):  # cross-key rules on the set this change would make
                fields = sorted({w for e in errors for w in defs if w in e})
                raise ApiError(422, "validation_failed", "; ".join(errors), {"fields": fields})
            store.append(key, value, change.changed_by)
            return setting_entry(key, store.overrides())

    @app.get("/settings/{key}/history", dependencies=[Depends(service_token)])
    def setting_history(key: str) -> list[dict]:
        known(key)
        with settings_store() as store:
            rows = [r for r in store.overrides() if r.key == key]
        return [{"key": r.key, "value": r.value, "changed_by": r.changed_by,
                 "changed_at": r.changed_at.isoformat()} for r in reversed(rows)]

    @app.post("/sweeps", status_code=202, dependencies=[Depends(service_token)])
    def sweeps(tasks: BackgroundTasks, req: SweepRequest | None = None) -> TriggerAccepted:
        """Review the latest attempt of every submitted, non-excused submission in scope that has not
        been reviewed. Listing happens now (an unknown course is a 422); reviewing in the background.
        A sweep of the same scope that is still queued or running is returned with duplicate=True."""
        req = req or SweepRequest()
        if req.assignment_id is not None and req.course_id is None:
            raise ApiError(422, "validation_failed", "assignment_id requires course_id",
                           {"fields": ["body.course_id"]})
        refs, skips, unreadable = [], [], []
        for course_id in [req.course_id] if req.course_id is not None else list_courses(s):
            try:
                lms = make_lms(s, course_id)
                lms.get_course(course_id)
            except (LookupError, FileNotFoundError):
                raise ApiError(422, "unknown_course", "the LMS has no such course") from None
            except ValueError:  # the course's files can't be read: never a 500, never the content
                if req.course_id is not None:
                    raise ApiError(502, "lms_data_error", f"the LMS returned unreadable data for {course_id}") from None
                unreadable.append(course_id)
                continue
            if req.assignment_id is not None:
                try:
                    lms.get_assignment(course_id, req.assignment_id)
                except (LookupError, FileNotFoundError):
                    raise ApiError(422, "unknown_assignment", "the course has no such assignment") from None
            try:
                submissions = lms.list_submissions(course_id, req.assignment_id)
            except (LookupError, FileNotFoundError):
                raise ApiError(422, "unknown_course", "the LMS has no such course") from None
            except ValueError:
                if req.course_id is not None:
                    raise ApiError(502, "lms_data_error", f"the LMS returned unreadable data for {course_id}") from None
                unreadable.append(course_id)
                continue
            mc = {}  # assignment id -> is multiple choice (skipped under the MVP pipeline)
            for sub in submissions:
                if skip_mc and sub.assignment_id not in mc:
                    try:
                        mc[sub.assignment_id] = lms.get_assignment(
                            course_id, sub.assignment_id).submission_type == "multiple_choice"
                    except (LookupError, FileNotFoundError):
                        raise ApiError(422, "unknown_course", "the LMS has no such course") from None
                if sub.excused:
                    skips.append(Skip(submission_id=sub.id, attempt=sub.attempt, reason="excused"))
                elif sub.workflow_state == "unsubmitted":
                    skips.append(Skip(submission_id=sub.id, attempt=None, reason="not_submitted"))
                elif mc.get(sub.assignment_id):
                    skips.append(Skip(submission_id=sub.id, attempt=sub.attempt, reason="unsupported_type"))
                else:
                    refs.append(SubmissionRef(course_id=course_id, assignment_id=sub.assignment_id,
                                              user_id=sub.user_id, submission_id=sub.id,
                                              attempt=sub.attempt, submitted_at=sub.submitted_at))
        run, duplicate = runs.create("manual", len(refs) + len(skips), (req.course_id, req.assignment_id))
        if not duplicate:
            if unreadable:
                runs.update(run.run_id, unreadable_courses=unreadable)
            tasks.add_task(sweep, run.run_id, refs, skips)
        return TriggerAccepted(run_id=run.run_id, duplicate=duplicate, ignored=False)

    @app.post("/trend-analyses", status_code=202, dependencies=[Depends(service_token)])
    def trend_analyses(req: TrendAnalysisRequest, tasks: BackgroundTasks) -> TriggerAccepted:
        """Analyse a classroom (or one student in it) from the persisted verdicts. Resolving the course and
        student happens now (unknown ones are a 422); the analysis runs in the background."""
        if req.scope == "student" and not req.user_id:
            raise ApiError(422, "validation_failed", "user_id is required for student scope",
                           {"fields": ["body.user_id"]})
        with closing_all() as opened:
            reader = make_analytics_reader(s)
            opened.append(reader)
            classroom_id = reader.classroom_for_course(req.course_id)
            if classroom_id is None:
                raise ApiError(422, "unknown_course", "no classroom for that course")
            student_id = None
            if req.scope == "student":
                student_id = reader.student_for_user(req.user_id)
                if student_id is None:
                    raise ApiError(422, "unknown_student", "no student with that user id")
        run = runs.new("manual", "trend_analysis")

        def analyse() -> None:
            as_of = req.as_of
            if student_id is not None:
                execute_analysis(run.run_id, classroom_id, student_id, "manual", as_of)
            else:
                with scheduler.exclusive(classroom_id):  # not alongside an automatic run of the classroom
                    execute_analysis(run.run_id, classroom_id, None, "manual", as_of)

        tasks.add_task(analyse)
        return TriggerAccepted(run_id=run.run_id, duplicate=False, ignored=False)

    return app

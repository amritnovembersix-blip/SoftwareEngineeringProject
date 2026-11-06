"""Command line: `python -m agentic review <student>` (or `review --course ...`), `load-standards`,
`seed-demo`, `analyze`, `serve`.

Lab commands (generate-essays, evaluate) live in `python -m agentic_lab`."""
import argparse
import re
import sys
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

from agentic import dataset, reports, wiring
from agentic.analytics.run import UnknownClassroom, analyze_classroom, analyze_student, everything_reviewed
from agentic.analytics.status import from_effective
from agentic.config import Settings
from agentic.coursedata import CourseDir, lms_assignment_id
from agentic.models import ReviewResult, SubmissionRef
from agentic.review.v2.options import ReviewOptions
from agentic.review.v2.run import review_one
from agentic.results.seed import seed_course, seed_demo
from agentic.settings.base import effective, load_defs
from agentic.standards import loader
from agentic.tuning import load_prompt


# The deliberate not-found messages of the LMS Adapters and review_one: they name a course id (from the
# command line), an assignment or a missing submission, never submission text.
_NOT_FOUND = re.compile(r"unknown course data|unknown course '[^']*'|unknown assignment|no submission found")


def _err(msg: str) -> int:
    print(msg, file=sys.stderr)
    return 2


def _strands(args: argparse.Namespace) -> list[str] | None:
    return args.strands.split(",") if getattr(args, "strands", None) else None


def _context(args: argparse.Namespace) -> bool | None:
    flag = getattr(args, "assignment_context", None)
    return None if flag is None else flag == "on"


def _review(args: argparse.Namespace) -> int:
    if args.course or args.assignment or args.student_flag:
        return _review_course(args)
    if not args.student:
        return _err("review needs a student, or --course, --assignment and --student")
    v2_only = [flag for flag, given in (("--pipeline", args.pipeline), ("--profile", args.profile),
                                        ("--prompt", args.prompt), ("--reasoning", args.reasoning),
                                        ("--no-root-cause", args.no_root_cause),
                                        ("--json", args.json)) if given]
    if v2_only:
        return _err(f"{', '.join(v2_only)} need the --course form (--course, --assignment and --student); "
                    "the positional form is the MVP review")
    return _review_legacy(args)


def _reviewer_pairs(flag: str, what: str, pairs: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for pair in pairs:
        reviewer, sep, value = pair.partition("=")
        if not sep or not reviewer or not value:
            raise ValueError(f"{flag} must look like <reviewer>=<{what}>, got {pair!r}")
        out[reviewer] = value
    return out


def _prompt_versions(pairs: list[str]) -> dict[str, str]:
    return _reviewer_pairs("--prompt", "version", pairs)


def reasoning_levels(pairs: list[str]) -> dict[str, str]:
    """`--reasoning <reviewer>=<level>` pairs (the lab's evaluate uses it too)."""
    return _reviewer_pairs("--reasoning", "level", pairs)


def _resolve_roster_student(s: Settings, version: str, prefix: str) -> str:
    return CourseDir(s.data_dir, version).resolve_student(prefix)


def _course_summary(result: ReviewResult) -> str:
    """Codes and counts only: never segment text or model replies."""
    lines = [f"Pipeline: {result.pipeline}   Verification: {result.verification.status}   "
             f"Submission type: {result.submission_type}"]
    lines.append(f"Verdicts ({len(result.verdicts)}):")
    for reviewer in dict.fromkeys(v.reviewer for v in result.verdicts):
        counts = {k: sum(1 for v in result.verdicts if v.reviewer == reviewer and v.verdict == k)
                  for k in ("meets", "growth_area", "not_evident")}
        lines.append(f"  {reviewer}: " + ", ".join(f"{k} {n}" for k, n in counts.items()))
    lines.append(f"Findings ({len(result.findings)}):")
    for f in result.findings:
        roots = ", ".join(l.root_code for l in result.links if l.finding_code == f.standard_code)
        lines.append(f"  {f.standard_code}  support level: {f.support_level or 'n/a'}"
                     + (f"  roots: {roots}" if roots else ""))
    strengths = [v.standard_code for v in result.verdicts if v.notable_strength]
    lines.append(f"Strengths ({len(strengths)}): " + ", ".join(strengths))
    tokens = sum(u.input_tokens + u.output_tokens for u in result.usage.values())
    lines.append(f"Tokens: {tokens}   LLM calls: {result.llm_calls}")
    return "\n".join(lines)


def _review_course(args: argparse.Namespace) -> int:
    if not (args.course and args.assignment and args.student_flag):
        return _err("--course, --assignment and --student go together")
    s = Settings.load().model_copy(update={"lms_source": "coursedir"})
    course_id = f"classroom_{args.course}"
    assignment_id = (args.assignment if "@" in args.assignment
                     else lms_assignment_id(args.assignment, args.course))
    try:
        options = ReviewOptions(pipeline=args.pipeline, profile=args.profile,
                                prompt_versions=_prompt_versions(args.prompt),
                                reasoning=reasoning_levels(args.reasoning),
                                root_cause=False if args.no_root_cause else None,
                                verify=False if args.no_verify else None)
        user_id = _resolve_roster_student(s, args.course, args.student_flag)
        resolved = wiring.resolve_options(s, options)
        for reviewer in options.prompt_versions:  # a missing prompt file fails before any model call
            load_prompt(s.prompts_dir, resolved.prompts[reviewer])
    except (LookupError, ValueError, FileNotFoundError) as err:
        return _err(str(err))
    conn = None
    try:
        store = None
        if args.persist:  # a saved review uses the admin overrides; without --persist, TOML defaults (no DB)
            conn = None if s.fake_llm else wiring.connect(s)
            store = wiring.build_settings_store(s, conn)
        result = review_one(s, course_id, assignment_id, user_id, options, store=store, conn=conn)
    except LookupError as err:
        if type(err) is LookupError and _NOT_FOUND.fullmatch(str(err)):  # a known not-found
            return _err(str(err))
        print(f"review failed: {type(err).__name__}", file=sys.stderr)  # KeyError etc.: class only
        return 1
    except Exception as err:  # noqa: BLE001 - message may quote submission text; print class only
        print(f"review failed: {type(err).__name__}", file=sys.stderr)
        return 1
    finally:
        if conn is not None:
            conn.close()
    print(_course_summary(result))
    if args.json:  # the one place the trace reaches disk, because the user asked for the file
        Path(args.json).write_text(result.model_dump_json(indent=2), encoding="utf-8")
        print(f"JSON: {args.json}")
    return _persist(args, s, result)


def _persist(args: argparse.Namespace, s: Settings, result: ReviewResult) -> int:
    if args.persist:
        try:
            outcome = wiring.build_results_writer(s).save(result)
        except Exception as err:  # noqa: BLE001 - print class only, never the message
            print(f"persist failed: {type(err).__name__}", file=sys.stderr)
            return 1
        if outcome.status == "written":
            print(f"saved (review_run {outcome.review_run_id})")
        else:
            print(f"already reviewed (review_run {outcome.review_run_id}); nothing written")
    return 0


def _review_legacy(args: argparse.Namespace) -> int:
    s = Settings.load()
    updates: dict = {"review_pipeline": "mvp"}  # Ruling T9a: this form keeps its MVP output
    if args.no_verify:
        updates["verify"] = False
    if args.classroom:
        updates["classroom"] = args.classroom
    if args.strategy:
        updates["review_strategy"] = args.strategy
    s = s.model_copy(update=updates)
    try:
        essays = dataset.load_essays(s.data_dir, s.classroom)
        row = dataset.resolve_student(essays, args.student)
        ref = SubmissionRef(course_id=row.course_id, assignment_id=row.assignment_id,
                            user_id=row.student_hash, submission_id=row.submission_id,
                            attempt=row.attempt, submitted_at=row.submitted_at)
        reviewer = wiring.build_reviewer(s, strands=_strands(args), context=_context(args))
    except (LookupError, ValueError, FileNotFoundError) as err:
        return _err(str(err))
    try:
        result = reviewer.run(ref)
        path = reports.write_review_report(result, s.reports_dir, models=reviewer.models,
                                           strands=reviewer.strands)
    except Exception as err:  # noqa: BLE001 - message may quote essay text; print class only
        name = type(err).__name__
        print(f"review failed: {name}", file=sys.stderr)
        return 1
    print(f"Strands: {', '.join(reviewer.strands)}"
          + (f"   Targets: {', '.join(reviewer.targets)}" if reviewer.targets else "")
          + f"   Assignment context: {'on' if reviewer.assignment_context else 'off'}")
    print(reports.print_summary(result))
    print(f"Report: {path}")
    return _persist(args, s, result)


def _load_standards(args: argparse.Namespace) -> int:
    s = Settings.load()
    path = Path(args.file) if args.file else s.standards_file
    if not path.exists():
        return _err(f"standards file not found: {path}")
    try:
        embedder = wiring.build_embedder(s)
    except ValueError as err:
        return _err(str(err))
    try:
        rows = loader.normalize_rows(loader.read_xlsx(path))
        conn = wiring.connect(s)
        with conn:
            report = loader.load(conn, rows, embedder)
    except Exception as err:  # noqa: BLE001 - print class only, never the message
        name = type(err).__name__
        print(f"load-standards failed: {name}", file=sys.stderr)
        return 1
    print(report)
    return 0


def _seed_demo(args: argparse.Namespace) -> int:
    s = Settings.load()
    version = args.classroom or s.classroom
    try:
        conn = wiring.connect(s)
        counts = seed_course(conn, s.data_dir, version) if s.lms_source == "coursedir" else None
        count = None if counts else seed_demo(conn, s.data_dir, version)
    except Exception as err:  # noqa: BLE001 - print class only, never the message
        print(f"seed-demo failed: {type(err).__name__}", file=sys.stderr)
        return 1
    if counts:
        print(f"seeded course {version} and its twin: {counts['classrooms']} classrooms, "
              f"{counts['students']} students, {counts['assignments']} assignments")
        return 0
    print(f"seeded classroom {version}: {count} students")
    return 0


def _reset_reviews(args: argparse.Namespace) -> int:
    from agentic.results.reset import reset_reviews

    s = Settings.load()
    classroom_id = dataset.course_id(args.classroom)  # the LMS course ID
    try:
        conn = wiring.connect(s)
        with conn:
            counts = reset_reviews(conn, classroom_id)
    except Exception as err:  # noqa: BLE001 - print class only, never the message
        print(f"reset-reviews failed: {type(err).__name__}", file=sys.stderr)
        return 1
    print(f"reset {classroom_id}: " + ", ".join(f"{t} {n}" for t, n in counts.items()))
    print("POST /sweeps can review this class again now. The webhook remembers attempts it reviewed "
          "until the agentic service restarts.")
    return 0


def _analytics_settings(s: Settings, store_needed: bool):
    """Effective teacher/admin settings (TOML defaults plus Postgres overrides). Without a saved run,
    an unreachable database falls back to the TOML defaults."""
    defs = load_defs(s.config_dir)
    try:
        store = wiring.build_settings_store(s)
        try:
            return from_effective(effective(defs, store.overrides()))
        finally:
            close = getattr(store, "close", None)
            if close:
                close()
    except Exception:  # noqa: BLE001 - the class is not needed: the defaults are the answer
        if store_needed:
            raise
        return from_effective(effective(defs, []))


def _short(student_id: str) -> str:
    return student_id[:8]


def _analysis_summary(version: str, as_of: datetime, scope_student: bool, out) -> str:
    """Codes, counts and short hashes only."""
    counts: dict[str, int] = {}
    for st in out.statuses:
        counts[st.status] = counts.get(st.status, 0) + 1
    lines = [f"Classroom {version}   as of {as_of.isoformat()}   run {out.run_id}",
             f"Concept statuses: {len(out.statuses)}",
             "  " + (", ".join(f"{k} {n}" for k, n in sorted(counts.items())) or "none")]
    if scope_student:
        return "\n".join(lines)
    lines.append("Top gaps (concept, students, top condition):")
    lines += [f"  {g.concept_code}: {len(g.students)} students   {g.top_condition or 'n/a'}"
              for g in out.gaps[:5]] or ["  none"]
    lines.append(f"Reteach groups ({len(out.groups)}):")
    lines += [f"  {g.root_concept} <- {g.grade_concept}: {len(g.students)} students" for g in out.groups]
    lines.append(f"Pair suggestions ({len(out.pairs)}):")
    lines += [f"  {p.concept_code}: {_short(p.tutor_id)} -> {_short(p.tutee_id)}" for p in out.pairs]
    return "\n".join(lines)


def _analyze(args: argparse.Namespace) -> int:
    s = Settings.load()
    if args.as_of:
        try:
            day = datetime.strptime(args.as_of, "%Y-%m-%d").date()
        except ValueError:
            return _err(f"--as-of must look like YYYY-MM-DD, got {args.as_of!r}")
        as_of = datetime.combine(day, time.max, tzinfo=ZoneInfo("America/Chicago"))
    else:
        as_of = None  # everything reviewed so far
    reader = writer = None
    try:
        cfg = _analytics_settings(s, store_needed=not args.no_save)
        reader = wiring.build_analytics_reader(s)
        writer = wiring.build_memory_analytics_writer() if args.no_save else wiring.build_analytics_writer(s)
        classroom_id = reader.classroom_for_course(dataset.course_id(args.classroom))
        if classroom_id is None:
            return _err(f"unknown classroom {args.classroom!r}")
        as_of = as_of or everything_reviewed(reader, classroom_id)
        if args.student:
            ids = sorted({r.student_id for r in reader.verdict_rows(classroom_id, as_of)
                          if r.student_id.startswith(args.student)})
            if len(ids) != 1:
                return _err(f"student prefix {args.student!r} matches {len(ids)} students in {args.classroom}")
            out = analyze_student(reader, writer, classroom_id, ids[0], as_of, cfg, "manual")
        else:
            out = analyze_classroom(reader, writer, classroom_id, as_of, cfg, "manual")
    except UnknownClassroom:
        return _err(f"unknown classroom {args.classroom!r}")
    except Exception as err:  # noqa: BLE001 - print class only, never the message
        print(f"analyze failed: {type(err).__name__}", file=sys.stderr)
        return 1
    finally:
        for adapter in (reader, writer):
            close = getattr(adapter, "close", None)
            if close:
                close()
    print(_analysis_summary(args.classroom, as_of, bool(args.student), out))
    return 0


def _serve(args: argparse.Namespace) -> int:
    import uvicorn

    from agentic.service.app import create_app

    uvicorn.run(create_app(), host=args.host, port=args.port)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="agentic")
    sub = p.add_subparsers(dest="command", required=True)
    r = sub.add_parser("review", help="review one student's essay")
    r.add_argument("student", nargs="?", help="student hash prefix (legacy classroom form)")
    r.add_argument("--course", help="course version of a v2 course folder, e.g. 2.00-control")
    r.add_argument("--assignment", help="assignment file id, e.g. ela9v2-a02 (with --course)")
    r.add_argument("--student", dest="student_flag", metavar="PREFIX", help="student hash prefix (with --course)")
    r.add_argument("--pipeline", choices=["mvp", "v2"])
    r.add_argument("--profile", help="reviewer profile in config/bands.toml (full or lean)")
    r.add_argument("--prompt", action="append", default=[], metavar="REVIEWER=VERSION",
                   help="use a prompt version for one reviewer, e.g. 3-5-language=v2 (repeatable)")
    r.add_argument("--reasoning", action="append", default=[], metavar="REVIEWER=LEVEL",
                   help="reasoning level for one reviewer (default, low, medium or high), e.g. 3-5-all=low (repeatable)")
    r.add_argument("--no-root-cause", action="store_true")
    r.add_argument("--json", metavar="PATH", help="write the full result, including the trace, to a file")
    r.add_argument("--classroom")
    r.add_argument("--strategy", choices=["full", "rag"])
    r.add_argument("--no-verify", action="store_true")
    r.add_argument("--persist", action="store_true")
    r.add_argument("--strands", help="strands to review, e.g. RL or RI,RL (writing W is always included)")
    r.add_argument("--assignment-context", choices=["on", "off"],
                   help="show the reviewer the assignment as context (default from ASSIGNMENT_CONTEXT)")
    r.set_defaults(func=_review)
    ls = sub.add_parser("load-standards", help="embed and load the standards spreadsheet")
    ls.add_argument("--file")
    ls.set_defaults(func=_load_standards)
    sd = sub.add_parser("seed-demo", help="seed the demo teacher, classroom, students, enrollments and assignment")
    sd.add_argument("--classroom")
    sd.set_defaults(func=_seed_demo)
    rr = sub.add_parser("reset-reviews", help="DEV/DEMO: delete one classroom's review results")
    rr.add_argument("--classroom", required=True, help="classroom version, e.g. 1.01")
    rr.set_defaults(func=_reset_reviews)
    an = sub.add_parser("analyze", help="trend analysis of a classroom (or one student) from saved reviews")
    an.add_argument("--classroom", required=True, help="classroom version, e.g. 1.01")
    an.add_argument("--student", metavar="PREFIX", help="student id prefix (one student's statuses only)")
    an.add_argument("--as-of", metavar="YYYY-MM-DD", help="end of that day, America/Chicago (default: everything reviewed so far)")
    an.add_argument("--no-save", action="store_true", help="print only; write no snapshot rows")
    an.set_defaults(func=_analyze)
    sv = sub.add_parser("serve", help="run the HTTP service (contracts/agentic-api.yaml)")
    sv.add_argument("--host", default="127.0.0.1")
    sv.add_argument("--port", type=int, default=8000)
    sv.set_defaults(func=_serve)
    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

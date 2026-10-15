"""Command line: `python -m agentic review <student>`, `load-standards`, `seed-demo`, `serve`.

Lab commands (generate-essays, evaluate) live in `python -m agentic_lab`."""
import argparse
import sys
from pathlib import Path

from agentic import dataset, reports, wiring
from agentic.config import Settings
from agentic.models import SubmissionRef
from agentic.results.seed import seed_demo
from agentic.standards import loader


def _err(msg: str) -> int:
    print(msg, file=sys.stderr)
    return 2


def _strands(args: argparse.Namespace) -> list[str] | None:
    return args.strands.split(",") if getattr(args, "strands", None) else None


def _context(args: argparse.Namespace) -> bool | None:
    flag = getattr(args, "assignment_context", None)
    return None if flag is None else flag == "on"


def _review(args: argparse.Namespace) -> int:
    s = Settings.load()
    updates: dict = {}
    if args.no_verify:
        updates["verify"] = False
    if args.classroom:
        updates["classroom"] = args.classroom
    if args.strategy:
        updates["review_strategy"] = args.strategy
    if updates:
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
        count = seed_demo(conn, s.data_dir, version)
    except Exception as err:  # noqa: BLE001 - print class only, never the message
        print(f"seed-demo failed: {type(err).__name__}", file=sys.stderr)
        return 1
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


def _serve(args: argparse.Namespace) -> int:
    import uvicorn

    from agentic.service.app import create_app

    uvicorn.run(create_app(), host=args.host, port=args.port)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="agentic")
    sub = p.add_subparsers(dest="command", required=True)
    r = sub.add_parser("review", help="review one student's essay")
    r.add_argument("student", help="student hash prefix")
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
    sd = sub.add_parser("seed-demo", help="seed the demo teacher, classroom, students and assignment")
    sd.add_argument("--classroom")
    sd.set_defaults(func=_seed_demo)
    rr = sub.add_parser("reset-reviews", help="DEV/DEMO: delete one classroom's review results")
    rr.add_argument("--classroom", required=True, help="classroom version, e.g. 1.01")
    rr.set_defaults(func=_reset_reviews)
    sv = sub.add_parser("serve", help="run the HTTP service (contracts/agentic-api.yaml)")
    sv.add_argument("--host", default="127.0.0.1")
    sv.add_argument("--port", type=int, default=8000)
    sv.set_defaults(func=_serve)
    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

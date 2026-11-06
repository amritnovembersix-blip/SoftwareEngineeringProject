"""Lab command line: `python -m agentic_lab generate-essays`, `evaluate`, `build-course`, `generate-course`,
`audit-fidelity`, `simulate`, `study`.

Production commands (review, load-standards, seed-demo) are `python -m agentic ...`; the lab
defaults DATA_DIR/REPORTS_DIR to its own folders (see agentic_lab.config).
"""
import argparse
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

from agentic import dataset, wiring
from agentic.coursedata import CourseDir, curriculum_path
from agentic.standards.loader import normalize_rows, read_xlsx
from agentic.__main__ import _context, _err, _strands, reasoning_levels
from agentic.review.v2.options import ReviewOptions
from agentic_lab import eval as evaluation
from agentic_lab import eval_v2
from agentic_lab.config import audit_settings, lab_settings, synth_settings, synth_workers
from agentic_lab import standards_v2, study as study_mod
from agentic_lab.analytics_lab import results_from_answer_key
from agentic_lab.labels import load_labels
from agentic_lab.sim import wiring as sim_wiring
from agentic_lab.sim.simulator import Simulator
from agentic_lab.sim.trigger import TriggerError
from agentic_lab.synth import audit
from agentic_lab.synth import generate_course as course_gen
from agentic_lab.synth.families import load_families
from agentic_lab.synth.generate_course import generate_course
from agentic_lab.synth.generate_essays import check_targets, generate
from agentic_lab.synth.students import build_course, load_plan, validate_codes
from agentic_lab.wiring import build_chat, build_synth_chat


def _failure(err: Exception) -> str:
    """The class name, plus a provider error's HTTP code and status (e.g. `ClientError 402 RESOURCE_EXHAUSTED`).
    Never the message: it may quote submission text."""
    code, status = getattr(err, "code", None), getattr(err, "status", None)
    parts = [type(err).__name__]
    if isinstance(code, int):
        parts.append(str(code))
    if isinstance(status, str) and status.replace("_", "").isalpha() and status.isupper():
        parts.append(status)
    return " ".join(parts)


def _generate_essays(args: argparse.Namespace) -> int:
    s = lab_settings()
    version = args.classroom or s.classroom
    try:
        labels = load_labels(s.data_dir, version)
        assignment = dataset.load_assignment(s.data_dir, version)
        outside = check_targets(labels, assignment)
        if outside:
            return _err(f"labels use codes outside the assignment targets: {', '.join(outside)}")
        chat = build_chat(s)
    except (LookupError, ValueError, FileNotFoundError) as err:
        return _err(str(err))
    try:
        report = generate(chat, labels, dataset.essays_path(s.data_dir, version), version,
                          overwrite=args.overwrite, progress=print, assignment=assignment)
    except Exception as err:  # noqa: BLE001 - message may quote essay text; class + status only
        print(f"generate-essays failed: {_failure(err)}", file=sys.stderr)
        return 1
    print(f"classroom {version}: {report.written} written, {report.skipped} skipped")
    return 0


def _evaluate_course(args: argparse.Namespace) -> int:
    """`evaluate --course`: v2 metrics against the answer key; the --classroom MVP path is separate."""
    for flag, value in (("--repeat", args.repeat), ("--students", args.students),
                        ("--through-week", args.through_week)):
        if value is not None and value <= 0:
            return _err(f"{flag} must be positive")
    if args.max_usd is not None and args.max_usd <= 0:
        return _err("--max-usd must be positive")
    if args.classroom or args.strategy:
        return _err("--course cannot be combined with --classroom or --strategy")
    s = lab_settings().model_copy(update={"lms_source": "coursedir"})
    try:
        options = ReviewOptions(pipeline=args.pipeline, profile=args.profile,
                                reasoning=reasoning_levels(args.reasoning))
        wiring.resolve_options(s, options)  # a bad reviewer or level fails before any model call
        CourseDir(s.data_dir, args.course).course()
        report = eval_v2.evaluate_course(s, args.course, args.through_week, options, repeat=args.repeat or 1,
                                         students=args.students, max_usd=args.max_usd)
        json_path, md_path = eval_v2.write_reports(report, s.reports_dir)
    except (LookupError, ValueError, FileNotFoundError) as err:
        return _err(str(err))
    except Exception as err:  # noqa: BLE001 - print class only, never the message
        print(f"evaluate failed: {type(err).__name__}", file=sys.stderr)
        return 1
    print(eval_v2.format_report(report))
    print(f"Report: {json_path}")
    print(f"Markdown: {md_path}")
    return 0


def _evaluate(args: argparse.Namespace) -> int:
    if args.course:
        return _evaluate_course(args)
    s = lab_settings().model_copy(update={"review_pipeline": "mvp"})  # compares MVP strategies
    if args.classroom:
        s = s.model_copy(update={"classroom": args.classroom})
    if args.students is not None and args.students <= 0:
        return _err("--students must be positive")
    names = ["full", "rag"] if args.strategy in (None, "both") else [args.strategy]
    try:
        labels = load_labels(s.data_dir, s.classroom)
        essays = dataset.load_essays(s.data_dir, s.classroom)
        grade = dataset.classroom_grade(s.data_dir, s.classroom)
        allowed = {x.code for x in wiring.build_review_standards(s, strands=_strands(args)).at_grade(grade)}
        reviewers = {}
        for n in names:
            reviewers[n] = wiring.build_reviewer(s, strategy=n, strands=_strands(args), context=_context(args))
            if args.compare_context:
                reviewers[f"{n}-noctx"] = wiring.build_reviewer(s, strategy=n, strands=_strands(args),
                                                                context=False)
    except (LookupError, ValueError, FileNotFoundError) as err:
        return _err(str(err))
    try:
        report = evaluation.run_evaluation(reviewers, labels, essays, allowed, students=args.students)
        s.reports_dir.mkdir(parents=True, exist_ok=True)
        path = s.reports_dir / f"eval-{datetime.now().strftime('%Y%m%dT%H%M%S')}.json"
        path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
        md_path = path.with_suffix(".md")
        md_path.write_text(evaluation.format_report_markdown(report), encoding="utf-8")
    except Exception as err:  # noqa: BLE001 - class + status only, never the message
        print(f"evaluate failed: {_failure(err)}", file=sys.stderr)
        return 1
    print(evaluation.format_report(report))
    print(f"Report: {path}")
    print(f"Markdown: {md_path}")
    return 0


def read_corpus():
    return normalize_rows(read_xlsx(standards_v2.SOURCE_XLSX))


def _build_standards(args: argparse.Namespace) -> int:
    path = standards_v2.REVIEWABILITY_CSV
    try:
        if args.overwrite or not path.exists():
            rows = standards_v2.draft_reviewability(read_corpus())
            standards_v2.write_reviewability(path, rows)
            print(f"drafted {path.name}: {len(rows)} rows")
        written = standards_v2.build_reviewable_xlsx(standards_v2.SOURCE_XLSX, path,
                                                     standards_v2.REVIEWABLE_XLSX)
    except (ValueError, FileNotFoundError, KeyError) as err:
        return _err(str(err))
    catalog = standards_v2.read_reviewability(path).values()
    print(f"{standards_v2.REVIEWABLE_XLSX.name}: {written} reviewable, "
          f"{sum(not r.reviewable for r in catalog)} not reviewable")
    for reason, n in sorted(Counter(r.reason for r in catalog if not r.reviewable).items()):
        print(f"  {n:4d}  {reason}")
    return 0


def _build_course(args: argparse.Namespace) -> int:
    s = lab_settings()
    try:
        plan = load_plan(Path(args.plan))
        families = load_families(s.data_dir / "skill_families.json")
        reviewable = standards_v2.reviewable_codes(standards_v2.REVIEWABILITY_CSV)
        missing = validate_codes(plan, families, curriculum_path(s.data_dir, plan.curriculum), reviewable)
        if missing:
            return _err(f"codes missing from the reviewable catalog: {', '.join(missing)}")
        build_course(plan, s.data_dir, args.seed)
    except (ValueError, FileNotFoundError, KeyError) as err:
        return _err(str(err))
    print(f"built {plan.version}-control and {plan.version}-intervention (seed {args.seed})")
    return 0


def read_standard_texts() -> dict[str, str]:
    return course_gen.read_standard_texts(standards_v2.REVIEWABLE_XLSX)


def _detectability(detect: dict[int, tuple[int, int]]) -> str:
    parts = []
    for level in (1, 2, 3):
        hits, pairs = detect.get(level, (0, 0))
        parts.append(f"level {level} " + (f"{hits}/{pairs} ({hits / pairs:.0%})" if pairs else "n/a"))
    return ", ".join(parts)


def _generate_course(args: argparse.Namespace) -> int:
    s = lab_settings()
    arms = ["control", "intervention"] if args.arm == "both" else [args.arm]
    versions = [f"{args.version}-{arm}" for arm in arms]
    for n, name in (("--students", args.students), ("--through-week", args.through_week)):
        if name is not None and name <= 0:
            return _err(f"{n} must be positive")
    if args.workers is not None and args.workers < 1:
        return _err("--workers must be at least 1")
    try:
        workers = args.workers if args.workers is not None else synth_workers()
        for v in versions:
            CourseDir(s.data_dir, v).course()
        standards = read_standard_texts()
        chat = None if args.mc_only else build_synth_chat(synth_settings(s))  # multiple choice needs no model
    except (LookupError, ValueError, FileNotFoundError) as err:
        return _err(str(err))
    unusable = 0
    for v in versions:
        try:
            report = generate_course(chat, s.data_dir, v, args.seed, through_week=args.through_week,
                                     students=args.students, overwrite=args.overwrite, progress=print,
                                     standards=standards, workers=workers, mc_only=args.mc_only)
        except Exception as err:  # noqa: BLE001 - message may quote submission text; class + status only
            print(f"generate-course failed: {_failure(err)}", file=sys.stderr)
            return 1
        rate = "n/a" if report.pass_rate is None else f"{report.pass_rate:.0%}"
        print(f"{v}: {report.written} written, {report.skipped} skipped, {report.failed_fidelity} failed "
              f"fidelity, {report.mc} mc; fidelity pass rate {rate}")
        if report.mc:
            print(f"{v}: multiple choice {report.mc - report.failed_mc}/{report.mc} passed concept fidelity; "
                  f"detectability (pairs with >= 2 picks): {_detectability(report.mc_detect)}")
        unusable += report.unusable
    if unusable:
        print(f"{unusable} unusable; rerun to retry")
        return 1
    return 0


def _audit_fidelity(args: argparse.Namespace) -> int:
    s = lab_settings()
    if args.sample <= 0:
        return _err("--sample must be positive")
    versions = [f"{args.version}-{arm}" for arm in ("control", "intervention")]
    try:
        a = audit_settings(s)
        for v in versions:
            CourseDir(s.data_dir, v).course()
        standards = read_standard_texts()
        workers = synth_workers()  # threads only; calls are still rate-limited by the audit RPM (AUDIT_LLM_RPM)
        writer = synth_settings(s).llm_model
        chat = build_synth_chat(a)
    except (LookupError, ValueError, FileNotFoundError) as err:
        return _err(str(err))
    out = Path(args.out) if args.out else (
        s.reports_dir / f"audit-fidelity-{datetime.now().strftime('%Y%m%dT%H%M%S')}.md")
    try:
        result = audit.run_audit(chat, s.data_dir, versions, n=args.sample, seed=args.seed, standards=standards,
                                 model=a.llm_model, workers=workers, writer_model=writer)
        md, js = audit.write_report(result, out)
    except Exception as err:  # noqa: BLE001 - message may quote submission text; class + status only
        print(f"audit-fidelity failed: {_failure(err)}", file=sys.stderr)
        return 1
    sub = result.summary["submissions"]
    agree = "n/a" if sub["percent_agreement"] is None else f"{sub['percent_agreement']:.0%}"
    print(f"audit-fidelity ({a.llm_model}; writer {writer or 'unknown'}): {len(result.sample.chosen)} sampled, {result.summary['usable']} "
          f"usable; pass/fail agreement {agree}")
    print(f"Report: {md}")
    print(f"JSON: {js}")
    return 0


SIM_VERSIONS = ("2.00-control", "2.00-intervention")


def _versions(args: argparse.Namespace) -> list[str]:
    """The selected course versions: `both` is control then intervention."""
    return list(SIM_VERSIONS) if args.course == "both" else [args.course]


def _simulator(args: argparse.Namespace) -> Simulator:
    """The Simulator for the selected course(s), wired to the folder releaser and the agentic service."""
    return sim_wiring.build_simulator(_versions(args))


def _reset_reviews(versions: list[str]) -> None:
    from agentic.results.reset import reset_reviews
    conn = wiring.connect(lab_settings())
    try:
        for v in versions:
            reset_reviews(conn, f"classroom_{v}")
    finally:
        conn.close()


def _print_progress(week, item, released, sent) -> None:
    print(f"week {week}  {item.lms_id}  released {released}  sent {sent}")


def _print_report(report, lines: int) -> None:
    if lines == 0:
        print(f"week {report.week}  nothing due")
    else:
        print(f"week {report.week}  released {report.released}  sent {report.sent}  "
              f"already sent {report.already_sent}")


def _print_week_done(week, failed, unknown) -> None:
    print(f"week {week}  failed {failed}  unknown {unknown}")


def _print_status(rows) -> None:
    print(f"{'week':>4}  {'assignment':<32}  rel  sent  queued  running  succeeded  failed  unknown")
    for r in rows:
        print(f"{r.week:>4}  {r.file_id + '@' + r.version:<32}  {'yes' if r.released else 'no':<3}  "
              f"{r.sent:>4}  {r.queued:>6}  {r.running:>7}  {r.succeeded:>9}  {r.failed:>6}  {r.unknown:>7}")


def _simulate(args: argparse.Namespace) -> int:
    lines = 0

    def progress(week, item, released, sent) -> None:
        nonlocal lines
        lines += 1
        _print_progress(week, item, released, sent)

    report = None
    try:
        sim = _simulator(args)
        if args.action == "reset":
            sim.reset()
            if args.reviews:
                _reset_reviews(_versions(args))
            # The service remembers events it has seen, so a replay against it would only get old run ids back.
            print("simulation reset to week 0" + ("; reviews deleted; restart the agentic service before "
                                                  "replaying" if args.reviews else ""))
        elif args.action == "advance":
            if args.weeks < 1:
                return _err("--weeks must be at least 1")
            report = sim.advance(weeks=args.weeks, wait=args.wait, progress=progress, week_done=_print_week_done)
        elif args.action == "run":
            if args.through_week < 1:
                return _err("--through-week must be at least 1")
            report = sim.run_through(args.through_week, wait=args.wait, progress=progress,
                                     week_done=_print_week_done)
        else:
            _print_status(sim.status())
    except (TriggerError, TimeoutError) as err:  # ids, week and counts only
        print(str(err), file=sys.stderr)
        return 2
    except Exception as err:  # noqa: BLE001 - class name only, never the message
        print(f"simulate failed: {type(err).__name__}", file=sys.stderr)
        return 1
    if report is not None:
        _print_report(report, lines)
        if report.failed:
            print(f"{report.failed} review run(s) failed", file=sys.stderr)
            return 3
    return 0


def _study(args: argparse.Namespace) -> int:
    """`study`: the twin intervention study, from answer-key results or from the Postgres reader."""
    s = lab_settings()
    versions = [f"{args.twins}-control", f"{args.twins}-intervention"]
    db_reader = None
    try:
        if args.source == "db":
            db_reader = wiring.build_analytics_reader(s)
            readers = dict.fromkeys(versions, db_reader)
        else:
            readers = {v: results_from_answer_key(s.data_dir, v) for v in versions}
        report = study_mod.study(readers, s.data_dir, args.twins)
        json_path, md_path = study_mod.write_reports(report, s.reports_dir)
    except (LookupError, ValueError, FileNotFoundError) as err:
        return _err(str(err))
    except Exception as err:  # noqa: BLE001 - print class only, never the message
        print(f"study failed: {type(err).__name__}", file=sys.stderr)
        return 1
    finally:
        close = getattr(db_reader, "close", None)
        if close:
            close()
    print(study_mod.format_report(report))
    print(f"Report: {json_path}")
    print(f"Markdown: {md_path}")
    return 0


def _fill_demo(args: argparse.Namespace) -> int:
    from agentic_lab.fill_demo import fill_demo

    try:
        r = fill_demo(lab_settings(), through_week=args.through_week, reset=args.reset)
    except (LookupError, ValueError, FileNotFoundError) as err:
        return _err(str(err))
    except Exception as err:  # noqa: BLE001 - class + status only
        print(f"fill-demo failed: {_failure(err)}", file=sys.stderr)
        return 1
    print(f"demo reviews: {r.saved} saved, {r.already_there} already there")
    print(f"analytics: {r.statuses} statuses ({r.needs_attention} need attention), {r.groups} reteach groups, "
          f"{r.pairs} peer pair suggestions")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="agentic_lab")
    sub = p.add_subparsers(dest="command", required=True)
    ge = sub.add_parser("generate-essays", help="generate synthetic essays from the labels file")
    ge.add_argument("--classroom")
    ge.add_argument("--overwrite", action="store_true")
    ge.set_defaults(func=_generate_essays)
    ev = sub.add_parser("evaluate", help="score reviews against the synthetic labels")
    ev.add_argument("--classroom")
    ev.add_argument("--course", help="v2 course folder version (e.g. 2.00-control): score against its answer key")
    ev.add_argument("--through-week", type=int, help="with --course: only assignments due by this week")
    ev.add_argument("--pipeline", choices=["mvp", "v2"], help="with --course (default: REVIEW_PIPELINE)")
    ev.add_argument("--profile", choices=["lean", "full", "graded"], help="with --course (default: BANDS_PROFILE)")
    ev.add_argument("--reasoning", action="append", default=[], metavar="REVIEWER=LEVEL",
                    help="with --course: reasoning level for one reviewer (default, low, medium or high), "
                         "e.g. 3-5-all=low (repeatable)")
    ev.add_argument("--repeat", type=int, help="with --course: runs per submission, for run-to-run agreement")
    ev.add_argument("--max-usd", type=float, help="with --course: stop starting submissions once the priced "
                    "spend so far exceeds this (writes a partial report)")
    ev.add_argument("--strategy", choices=["full", "rag", "both"])
    ev.add_argument("--students", type=int)
    ev.add_argument("--strands", help="strands to review, e.g. RL (writing W is always included)")
    ev.add_argument("--assignment-context", choices=["on", "off"])
    ev.add_argument("--compare-context", action="store_true",
                    help="also run each strategy with the assignment context off (<name>-noctx)")
    ev.set_defaults(func=_evaluate)
    bs = sub.add_parser("build-standards", help="draft the reviewability CSV and build the reviewable xlsx")
    bs.add_argument("--overwrite", action="store_true", help="redraft the CSV, discarding hand edits")
    bs.set_defaults(func=_build_standards)
    bc = sub.add_parser("build-course", help="write both twin course folders and answer keys from a plan")
    bc.add_argument("--plan", required=True)
    bc.add_argument("--seed", type=int, default=2027)
    bc.set_defaults(func=_build_course)
    gc = sub.add_parser("generate-course", help="write a v2 course's submissions, expected labels and fidelity")
    gc.add_argument("--arm", choices=["control", "intervention", "both"], default="both")
    gc.add_argument("--version", default="2.00", help="course version without the arm (folder <version>-<arm>)")
    gc.add_argument("--through-week", type=int)
    gc.add_argument("--students", type=int, help="only the first K roster students")
    gc.add_argument("--seed", type=int, default=2027)
    gc.add_argument("--overwrite", action="store_true")
    gc.add_argument("--mc-only", action="store_true",
                    help="multiple choice only: no model calls; with --overwrite rewrites only MC rows")
    gc.add_argument("--workers", type=int, help="writing tasks run at once (default SYNTH_WORKERS, else 8)")
    gc.set_defaults(func=_generate_course)
    af = sub.add_parser("audit-fidelity", help="re-check a stratified sample with AUDIT_LLM_MODEL (report only)")
    af.add_argument("--sample", type=int, default=40, help="about this many written submissions (default 40)")
    af.add_argument("--seed", type=int, default=7)
    af.add_argument("--version", default="2.00", help="course version without the arm (both arms are sampled)")
    af.add_argument("--out", help="Markdown report path (JSON next to it); default under REPORTS_DIR")
    af.set_defaults(func=_audit_fidelity)
    si = sub.add_parser("simulate", help="replay the semester week by week against the agentic service")
    course = argparse.ArgumentParser(add_help=False)
    course.add_argument("--course", choices=[*SIM_VERSIONS, "both"], default="both")
    acts = si.add_subparsers(dest="action", required=True)
    rs = acts.add_parser("reset", parents=[course], help="empty the released files and go back to week 0")
    rs.add_argument("--reviews", action="store_true",
                    help="also delete the selected classrooms' review results from Postgres (DEV/DEMO only)")
    ad = acts.add_parser("advance", parents=[course], help="release and trigger the next week(s)")
    ad.add_argument("--weeks", type=int, default=1)
    ad.add_argument("--wait", action="store_true", help="wait for each week's reviews to finish")
    ru = acts.add_parser("run", parents=[course], help="reset, then replay weeks 1..N")
    ru.add_argument("--through-week", type=int, required=True)
    ru.add_argument("--wait", action="store_true", help="wait for each week's reviews to finish")
    acts.add_parser("status", parents=[course], help="show each assignment's release and run states")
    si.set_defaults(func=_simulate)
    st = sub.add_parser("study", help="twin intervention study (control vs intervention classroom)")
    st.add_argument("--twins", default="2.00", help="course version without the arm (default 2.00)")
    st.add_argument("--source", choices=["answer-key", "db"], default="answer-key",
                    help="verdicts from the answer key (no reviews needed) or from Postgres")
    st.set_defaults(func=_study)
    fd = sub.add_parser("fill-demo", help="fill Postgres with labelled demo reviews from the answer key (no API keys)")
    fd.add_argument("--through-week", type=int, default=7, help="last week to fill (default 7: trends are still moving)")
    fd.add_argument("--reset", action="store_true", help="delete the 2.00 classrooms' review results first (DEV/DEMO)")
    fd.set_defaults(func=_fill_demo)
    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

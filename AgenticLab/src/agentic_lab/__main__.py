"""Lab command line: `python -m agentic_lab generate-essays` and `evaluate`.

Production commands (review, load-standards, seed-demo) are `python -m agentic ...`; the lab
defaults DATA_DIR/REPORTS_DIR to its own folders (see agentic_lab.config).
"""
import argparse
import sys
from datetime import datetime

from agentic import dataset, wiring
from agentic.__main__ import _context, _err, _strands
from agentic_lab import eval as evaluation
from agentic_lab.config import lab_settings
from agentic_lab.labels import load_labels
from agentic_lab.synth.generate_essays import check_targets, generate
from agentic_lab.wiring import build_chat


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
    except Exception as err:  # noqa: BLE001 - message may quote essay text; print class only
        print(f"generate-essays failed: {type(err).__name__}", file=sys.stderr)
        return 1
    print(f"classroom {version}: {report.written} written, {report.skipped} skipped")
    return 0


def _evaluate(args: argparse.Namespace) -> int:
    s = lab_settings()
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
    except Exception as err:  # noqa: BLE001 - print class only, never the message
        print(f"evaluate failed: {type(err).__name__}", file=sys.stderr)
        return 1
    print(evaluation.format_report(report))
    print(f"Report: {path}")
    print(f"Markdown: {md_path}")
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
    ev.add_argument("--strategy", choices=["full", "rag", "both"])
    ev.add_argument("--students", type=int)
    ev.add_argument("--strands", help="strands to review, e.g. RL (writing W is always included)")
    ev.add_argument("--assignment-context", choices=["on", "off"])
    ev.add_argument("--compare-context", action="store_true",
                    help="also run each strategy with the assignment context off (<name>-noctx)")
    ev.set_defaults(func=_evaluate)
    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

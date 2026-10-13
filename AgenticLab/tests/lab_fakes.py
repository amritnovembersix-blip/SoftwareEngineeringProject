"""Lab-only test helpers."""
from pathlib import Path


def eval_report_file(dir_path: Path, stamp: str, runs: dict) -> Path:
    """Write a small eval-<stamp>.json. runs: {name: (false_alarm_rate, [(student, expected, found)])}."""
    from agentic_lab.eval import Bin, Calibration, Counts, EvalReport, StrategyReport, StudentRow

    strategies = {}
    for name, (far, students) in runs.items():
        strategies[name] = StrategyReport(
            exact=Counts(tp=1, fp=1, fn=1), concept=Counts(tp=1, fp=0, fn=1), false_alarm_rate=far,
            llm_calls=len(students), seconds=1.0,
            finding_calibration=Calibration(brier=0.3, bins=[Bin(lo=0.8, hi=1.0, count=2, mean_p=0.9, frac_true=0.5)]),
            met_calibration=Calibration(brier=None, bins=[]), thresholds=[], miss_recall=None,
            not_evident={"9-10.W.3.A.a": 1}, recall_by_level={},
            per_student=[StudentRow(student=s, expected=e, found=f, finding_p=[], possible_misses=[],
                                    llm_calls=1, seconds=1.0) for s, e, f in students])
    path = Path(dir_path) / f"eval-{stamp}.json"
    path.write_text(EvalReport(students=2, strands=["W", "RL"], out_of_scope=[],
                               strategies=strategies).model_dump_json(), encoding="utf-8")
    return path

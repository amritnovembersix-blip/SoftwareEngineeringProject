"""Spot check: a flag log written during evaluate --course, and a Markdown file sampled from it."""
import json

from agentic.models import Observation, ReviewResult, SubmissionRef, Verdict, Verification
from agentic_lab.config import lab_settings
from agentic_lab.spot_check import FlagLog, build_spot_check, latest_log, read_log, sample

VERSION = "2.00-control"
A02_STUDENT = None  # filled from the committed data below


def _students():
    s = lab_settings(None)
    rows = [json.loads(line) for line in
            (s.data_dir / "courses" / VERSION / "submissions" / "ela9v2-a02.jsonl").read_text(encoding="utf-8").splitlines()]
    return [r["student_hash"] for r in rows], {r["student_hash"]: r["submission_id"] for r in rows}


def _result(student, sid, verdicts, observations=()):
    ref = SubmissionRef(course_id=f"classroom_{VERSION}", assignment_id=f"ela9v2-a02@{VERSION}", user_id=student,
                        submission_id=sid, attempt=1)
    return ReviewResult(submission_ref=ref, course_grade=9, strategy="full", standards=[], observations=list(observations),
                        findings=[], met_standards=[], dropped=[], pipeline="v2", submission_type="paragraph",
                        verification=Verification(status="disabled"), verdicts=verdicts)


def _g(code, reviewer, segs):
    return Verdict(standard_code=code, verdict="growth_area", support_level="developing", segments=segs,
                   reviewer=reviewer)


def _log(tmp_path):
    students, sids = _students()
    log = FlagLog(tmp_path / "flags-20261112T000000.jsonl", VERSION)
    for i, st in enumerate(students[:6]):
        res = _result(st, sids[st], [
            _g("9-10.W.3.A.a", "6+-writing", [1]), _g("3.L.1.A.e", "3-language", [2]),
            _g("9-10.RL.1.A", "6+-reading", [3]),
            Verdict(standard_code="5.L.1.B.c", verdict="meets", reviewer="5-language")],
            [Observation(standard_code="3.L.1.A.e", paragraph=2, reason="Subject and verb disagree.", level="sentence")])
        log(res, "ela9v2-a02", st, {"9-10.RL.1.A": 2} if i % 2 else {})
    return log, students


def test_log_holds_ids_codes_and_reasons_but_no_essay_text(tmp_path):
    log, students = _log(tmp_path)
    rows = read_log(log.path)
    assert len(rows) == 6 and rows[0]["version"] == VERSION and rows[0]["assignment"] == "ela9v2-a02"
    assert [f["standard"] for f in rows[0]["flags"]] == ["9-10.W.3.A.a", "3.L.1.A.e", "9-10.RL.1.A"]  # growth only
    assert rows[0]["flags"][1]["reasons"] == ["Subject and verb disagree."]
    essay = (lab_settings(None).data_dir / "courses" / VERSION / "submissions" / "ela9v2-a02.jsonl").read_text(encoding="utf-8")
    first_sentence = json.loads(essay.splitlines()[0])["paragraphs"][0][:40]
    assert first_sentence not in log.path.read_text(encoding="utf-8")


def test_a_half_written_last_line_is_skipped(tmp_path):
    log, _ = _log(tmp_path)
    with log.path.open("a", encoding="utf-8") as fh:
        fh.write('{"version": "2.00-con')  # the run is still writing
    assert len(read_log(log.path)) == 6
    assert latest_log(tmp_path) == log.path


def test_sample_takes_only_unplanted_flags_spread_across_reviewers_and_students(tmp_path):
    log, _ = _log(tmp_path)
    picks = sample(read_log(log.path), n=6)
    assert len(picks) == 6
    assert all(p.flag["standard"] not in p.record["planted"] for p in picks)
    assert {p.flag["reviewer"] for p in picks} == {"6+-writing", "3-language", "6+-reading"}
    assert len({p.record["student"] for p in picks}) >= 5
    assert [(p.record["student"], p.flag["standard"]) for p in picks] == [
        (p.record["student"], p.flag["standard"]) for p in sample(read_log(log.path), n=6)]  # same seed, same picks


def test_the_file_shows_prompt_numbered_response_planted_standard_and_answers(tmp_path):
    log, _ = _log(tmp_path)
    s = lab_settings(None)
    path, items, subs = build_spot_check(log.path, s.data_dir, s.standards_file, tmp_path / "out.md", n=3)
    md = path.read_text(encoding="utf-8")
    assert (items, subs) == (3, 6)
    assert "Write one paragraph that makes an inference about what kind of person Rainsford is" in md
    assert "> **S1** " in md or "> **S2** " in md or "> **S3** " in md
    assert "◀ **cited**" in md and "> S" in md
    assert "Planted for this submission" in md and "Real problem, wrong standard" in md
    assert "Also flagged on the same segment" not in md or "`" in md


# ---- review supervisor (Task 7): flags the supervisor removed ----

def _removed_log(tmp_path):
    students, sids = _students()
    log = FlagLog(tmp_path / "flags-20261117T000000.jsonl", VERSION)
    for i, st in enumerate(students[:4]):
        res = _result(st, sids[st], [_g("9-10.W.3.A.a", "6+-writing", [1])])
        res.trace["supervisor"] = {"changes": [
            {"standard": "3.L.1.A.e", "step": "critic", "action": "dropped:met", "reason": "Verbs agree.",
             "segments": [2]},
            {"standard": "9-10.W.3.A.d", "step": "arbiter", "action": "absorbed", "into": "9-10.W.3.A.a",
             "reason": "Same sentence.", "segments": [1]}], "capped": [], "invalid": []}
        log(res, "ela9v2-a02", st, {})
    return log


def test_log_records_removed_flags_with_step_and_reason(tmp_path):
    rows = read_log(_removed_log(tmp_path).path)
    assert rows[0]["removed"][1] == {"standard": "9-10.W.3.A.d", "step": "arbiter", "action": "absorbed",
                                     "into": "9-10.W.3.A.a", "reason": "Same sentence.", "segments": [1]}
    plain = FlagLog(tmp_path / "flags-plain.jsonl", VERSION)
    students, sids = _students()
    plain(_result(students[0], sids[students[0]], []), "ela9v2-a02", students[0], {})
    assert read_log(plain.path)[0]["removed"] == []


def test_sample_removed_round_robins_by_step(tmp_path):
    picks = sample(read_log(_removed_log(tmp_path).path), n=4, removed=True)
    assert [p.flag["step"] for p in picks] == ["arbiter", "critic", "arbiter", "critic"]
    assert len({p.record["student"] for p in picks}) >= 2


def test_removed_file_shows_cited_segment_and_into_standard(tmp_path):
    s = lab_settings(None)
    path, items, _ = build_spot_check(_removed_log(tmp_path).path, s.data_dir, s.standards_file,
                                      tmp_path / "out.md", n=2, removed=True)
    md = path.read_text(encoding="utf-8")
    assert items == 2 and "Was removing it right?" in md and "Right to remove" in md
    assert "absorbed into `9-10.W.3.A.a`" in md and "◀ **cited**" in md


def test_log_still_has_no_essay_text(tmp_path):
    log = _removed_log(tmp_path)
    essay = (lab_settings(None).data_dir / "courses" / VERSION / "submissions" / "ela9v2-a02.jsonl").read_text(encoding="utf-8")
    first_sentence = json.loads(essay.splitlines()[0])["paragraphs"][0][:40]
    assert first_sentence not in log.path.read_text(encoding="utf-8")


def test_cli_spot_check_removed(tmp_path, capsys):
    from agentic_lab.__main__ import main
    log = _removed_log(tmp_path)
    out = tmp_path / "removed.md"
    assert main(["spot-check", "--log", str(log.path), "--removed", "--n", "2", "--out", str(out)]) == 0
    assert "Was removing it right?" in out.read_text(encoding="utf-8")

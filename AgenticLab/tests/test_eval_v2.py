"""evaluate --course: v2 metrics on hand-made results against a hand-made answer key (no network)."""
import agentic
import json
import shutil
from pathlib import Path

import pytest

from agentic.config import Settings
from agentic.llm.base import DailyQuotaExceeded, Usage
from agentic.models import FindingLink, ReviewResult, SubmissionRef, Verdict, Verification
from agentic.review.v2.options import ReviewOptions
from agentic_lab import eval_v2
from agentic_lab.__main__ import main

FIXTURE = Path(agentic.__file__).resolve().parents[2] / "tests" / "data" / "coursedir"
VERSION = "0.90-control"
COURSE = f"classroom_{VERSION}"
A1, B2 = "a1a1a1a1a1a1a1a1", "b2b2b2b2b2b2b2b2"
# "other" shares codes with both families but is planted for nobody
FAMILIES = {"evidence": ["8.RL.1.A", "9-10.RL.1.A"], "vocab": ["8.RL.1.B", "9-10.RL.1.B"],
            "other": ["9-10.RL.1.A", "9-10.RL.1.B"]}
SECRET = "The fox found a key"


def _write(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def _expected(sid_file: str, per_student: dict[str, list[tuple[str, int]]]) -> list[dict]:
    return [{"student_hash": h, "submission_id": f"sub-control-{sid_file}-{h[:8]}",
             "weaknesses": [{"standard": c, "level": lv} for c, lv in ws]} for h, ws in per_student.items()]


@pytest.fixture
def data(tmp_path) -> Path:
    shutil.copytree(FIXTURE / "curricula", tmp_path / "curricula")
    shutil.copytree(FIXTURE / "courses" / VERSION, tmp_path / "courses" / VERSION,
                    ignore=shutil.ignore_patterns("answer_key"))
    (tmp_path / "skill_families.json").write_text(json.dumps(FAMILIES), encoding="utf-8")
    key = tmp_path / "courses" / VERSION / "answer_key"
    _write(key / "students.jsonl", [
        {"student_hash": h, "pair_id": "p", "profile": "x", "quirks": [], "baseline": {}, "start_week": 1,
         "families": [{"family": f, "secure_through": 8, "at_grade_level": 2} for f in fams]}
        for h, fams in ((A1, ["evidence", "vocab"]), (B2, []))])
    _write(key / "trajectory.jsonl", [
        {"student_hash": A1, "week": 1, "standard": "9-10.RL.1.A", "level": 2},
        {"student_hash": A1, "week": 1, "standard": "9-10.W.3.A.c", "level": 1},
        {"student_hash": A1, "week": 2, "standard": "9-10.RL.1.A", "level": 2},
        {"student_hash": A1, "week": 3, "standard": "9-10.RL.1.B", "level": 3}])
    _write(key / "expected" / "ela9v2-a01.jsonl",
           _expected("ela9v2-a01", {A1: [("9-10.RL.1.A", 2), ("9-10.W.3.A.c", 1)], B2: []}))
    _write(key / "expected" / "ela9v2-a02.jsonl", _expected("ela9v2-a02", {A1: [("9-10.RL.1.A", 2)], B2: []}))
    _write(key / "expected" / "ela9v2-a03.jsonl", _expected("ela9v2-a03", {A1: [("9-10.RL.1.B", 3)], B2: []}))
    _write(key / "fidelity.jsonl", [{"submission_id": f"sub-control-ela9v2-a02-{A1[:8]}", "missing": [],
                                     "extra": ["9-10.RL.1.B"]}])
    return tmp_path


def _s(data, **kw) -> Settings:
    return Settings(fake_llm=True, data_dir=data, lms_source="coursedir", review_pipeline="v2",
                    reports_dir=data / "reports", llm_model="gemini-2.5-flash", **kw)


def _v(code, verdict, reviewer, level=None, wg=None, strength=False):
    return Verdict(standard_code=code, verdict=verdict, support_level=level, working_grade=wg,
                   notable_strength=strength, strength_note="Clear and precise." if strength else None,
                   reviewer=reviewer)


def _res(assignment, user, kind, verdicts, links=(), usage=None, calls=1, **kw) -> ReviewResult:
    ref = SubmissionRef(course_id=COURSE, assignment_id=assignment, user_id=user,
                        submission_id="x", attempt=1)
    return ReviewResult(submission_ref=ref, course_grade=9, strategy="full", standards=[], observations=[],
                        findings=[], met_standards=[], dropped=[], pipeline="v2", submission_type=kind,
                        verification=Verification(status="disabled"), verdicts=verdicts,
                        links=list(links), usage=usage or {}, llm_calls=calls, **kw)


def _link(a, b):
    return FindingLink(finding_code=a, root_code=b, rationale="Shared root skill.")


def _scripted():
    """Hand-made results, keyed by (file assignment id, student)."""
    big = {"6+-reading": Usage(input_tokens=1_000_000, output_tokens=100_000, estimated=False)}
    out = {
        ("ela9v2-a01", A1): _res("ela9v2-a01@" + VERSION, A1, "paragraph", [
            _v("9-10.RL.1.A", "growth_area", "6+-reading", "developing", wg=8),
            _v("9-10.W.3.A.d", "growth_area", "6+-writing", "approaching"),
            _v("9-10.W.3.A.c", "meets", "6+-writing", strength=True)],
            links=[_link("9-10.RL.1.A", "8.RL.1.A"), _link("9-10.RL.1.A", "8.RL.1.B")],
            usage=big, calls=3),
        ("ela9v2-a01", B2): _res("ela9v2-a01@" + VERSION, B2, "paragraph", []),
        ("ela9v2-a02", A1): _res("ela9v2-a02@" + VERSION, A1, "short_answer", [  # fidelity failed
            _v("9-10.RL.1.A", "growth_area", "6+-reading", "developing"),
            _v("9-10.RL.1.B", "growth_area", "6+-reading", "beginning")]),
        ("ela9v2-a02", B2): _res("ela9v2-a02@" + VERSION, B2, "short_answer", []),
        ("ela9v2-a03", A1): _res("ela9v2-a03@" + VERSION, A1, "multiple_choice", [
            _v("9-10.RL.1.B", "growth_area", "6+-reading", "beginning", wg=9)], calls=0),
        ("ela9v2-a03", B2): _res("ela9v2-a03@" + VERSION, B2, "multiple_choice", [
            _v("9-10.RL.1.B", "growth_area", "6+-reading", "approaching"),
            _v("9-10.RL.1.A", "meets", "6+-reading", strength=True)], calls=0),
    }

    def review(_s, _course, assignment_id, user, _options):
        return out[(assignment_id.split("@")[0], user)]
    return review


def _run(data, review=None, **kw):
    kw = {"through_week": None, "options": ReviewOptions(), "repeat": 1, "students": None, **kw}
    return eval_v2.evaluate_course(_s(data), VERSION, review_fn=review or _scripted(), **kw)


def test_growth_precision_and_recall_exact_and_concept(data):
    g = _run(data).growth
    assert (g.overall.exact.tp, g.overall.exact.fp, g.overall.exact.fn) == (2, 2, 1)
    assert g.overall.exact.precision == 0.5 and g.overall.exact.recall == pytest.approx(2 / 3)
    # 9-10.W.3.A.d is the right concept (9-10.W.3.A) but the wrong code
    assert (g.overall.concept.tp, g.overall.concept.fp, g.overall.concept.fn) == (3, 1, 0)


def test_growth_by_reviewer_and_by_type(data):
    g = _run(data).growth
    assert (g.by_reviewer["6+-reading"].exact.tp, g.by_reviewer["6+-reading"].exact.fp) == (2, 1)
    w = g.by_reviewer["6+-writing"].exact  # owns the missed W.3.A.c verdict and the wrong W.3.A.d
    assert (w.tp, w.fp, w.fn) == (0, 1, 1)
    assert g.by_type["paragraph"].exact.tp == 1 and g.by_type["multiple_choice"].exact.tp == 1
    assert g.by_type["short_answer"].exact.tp == 0  # the fidelity-failed submission is not counted


def test_support_confusion_counts_planned_by_reported_level(data):
    assert _run(data).support_confusion == {"developing": {"developing": 1}, "beginning": {"beginning": 1}}


def test_level_inversion_flags_are_counted_in_total_and_by_reviewer(data):
    from agentic.models import Dropped
    review = _scripted()

    def flagged(*a):
        res = review(*a)
        if a[3] != A1 or "a01" not in a[2]:
            return res
        flags = [Dropped(stage="band:6+-reading", reason="level_inversion:RL.1.A"),
                 Dropped(stage="band:6+-writing", reason="level_inversion:W.3.A"),
                 Dropped(stage="band:6+-reading", reason="bad_step_level:RL.1.A")]
        return res.model_copy(update={"dropped": flags})
    r = _run(data, review=flagged)
    assert r.level_inversions.total == 2
    assert r.level_inversions.by_reviewer == {"6+-reading": 1, "6+-writing": 1}
    assert "level_inversions" in r.model_dump()
    md = eval_v2.format_report_markdown(r)
    assert "inversion" in md.lower() and "6+-reading" in md
    assert _run(data).level_inversions.total == 0


def _with_drops(drops_by_key):
    """The scripted reviews with `Dropped` entries added to the (assignment, student) results named."""
    from agentic.models import Dropped
    review = _scripted()

    def f(*a):
        res = review(*a)
        extra = drops_by_key.get((a[2].split("@")[0], a[3]), [])
        return res.model_copy(update={"dropped": [Dropped(stage=s, reason=r) for s, r in extra]})
    return f


def test_drops_counted_by_reviewer_and_rule_with_suffixes_collapsed(data):
    r = _run(data, review=_with_drops({
        ("ela9v2-a01", A1): [("band:6+-reading", "no_valid_evidence:RL.1.A"),
                             ("band:6+-reading", "no_valid_evidence:RL.1.B"),
                             ("band:6+-writing", "unjudged"),
                             ("root_cause", "link_too_long"),
                             ("band:6+-reading", "level_inversion:RL.1.A")],  # a flag, not a drop
        ("ela9v2-a02", A1): [("band:6+-reading", "unjudged")]}))  # fidelity-failed: excluded
    assert r.drops.by_reviewer == {"6+-reading": {"no_valid_evidence": 2}, "6+-writing": {"unjudged": 1},
                                   "root_cause": {"link_too_long": 1}}
    assert r.drops.total == 4
    assert r.level_inversions.total == 1


def test_unreviewed_code_attributed_through_a_group_drop_else_unattributed(data):
    # A1 a01 plants 9-10.RL.1.A and 9-10.W.3.A.c; give A1 a01 no verdicts for either
    r = _run(data, review=_no_verdicts_a01((("band:6+-reading", "no_valid_evidence:RL.1.A"),
                                            ("band:6+-reading", "unjudged"))))  # not the writer's drop
    assert r.drops.unreviewed_by_reason == {"no_valid_evidence": 1, "unattributed": 1}
    assert r.drops.unreviewed_by_reviewer == {"6+-reading": {"no_valid_evidence": 1},
                                              "6+-writing": {"unattributed": 1}}
    assert r.drops.unreviewed_total == 2


def _no_verdicts_a01(drops=(("band:6+-reading", "no_valid_evidence:RL.1.A"),
                            ("band:6+-writing", "unjudged"))):
    from agentic.models import Dropped
    review = _scripted()

    def f(*a):
        res = review(*a)
        if a[3] == A1 and "a01" in a[2]:
            return res.model_copy(update={"verdicts": [], "dropped": [Dropped(stage=s, reason=r)
                                                                       for s, r in drops]})
        return res
    return f


def test_plain_unjudged_from_the_owning_reviewer_attributes_the_code(data):
    r = _run(data, review=_no_verdicts_a01())  # 9-10.W.3.A.c is owned by 6+-writing
    assert r.drops.unreviewed_by_reviewer["6+-writing"] == {"unjudged": 1}
    assert r.drops.unreviewed_by_reviewer["6+-reading"] == {"no_valid_evidence": 1}


def test_non_fatal_drop_does_not_mask_the_fatal_cause(data):
    r = _run(data, review=_no_verdicts_a01((("band:6+-reading", "strength_not_met:RL.1.A"),
                                            ("band:6+-reading", "bad_step_level:RL.1.A"),
                                            ("band:6+-reading", "no_valid_evidence:RL.1.A"))))
    assert r.drops.unreviewed_by_reviewer["6+-reading"] == {"no_valid_evidence": 1}
    assert r.drops.by_reviewer["6+-reading"]["strength_not_met"] == 1  # still counted as a drop


def test_group_drop_from_another_band_does_not_attribute(data):
    from agentic_lab.eval_v2 import _attribute
    from agentic.models import Dropped
    plan = eval_v2.tuning.plan_reviewers(eval_v2.tuning.load_profile(_s(data).config_dir, "graded"), 9)
    owner = eval_v2._owner_of("4.W.3.A.a", plan)
    assert owner == "4-writing"
    drops = [Dropped(stage="band:6+-writing", reason="no_valid_evidence:W.3.A")]  # same group, other band
    assert _attribute("4.W.3.A.a", drops, owner, True) == ("unattributed", "4-writing")
    assert _attribute("9-10.W.3.A.c", drops, eval_v2._owner_of("9-10.W.3.A.c", plan), True) == (
        "no_valid_evidence", "6+-writing")


def test_search_unjudged_group_and_per_standard_code_attribution():
    from agentic_lab.eval_v2 import _attribute
    from agentic.models import Dropped
    g = [Dropped(stage="band:6+-reading", reason="unjudged:RL.1.A")]
    for code in ("9-10.RL.1.A", "9-10.RL.1.A.b", "8.RL.1.A"):  # several codes, one group drop
        assert _attribute(code, g, "6+-reading", True) == ("unjudged", "6+-reading")
    c = [Dropped(stage="band:4-writing", reason="no_valid_evidence:4.W.3.A.a")]
    assert _attribute("4.W.3.A.a", c, "4-writing", True) == ("no_valid_evidence", "4-writing")
    assert _attribute("4.W.3.A.b", c, "4-writing", True) == ("unattributed", "4-writing")
    assert _attribute("4.W.3.A.b", c, None, True) == ("unattributed", "unknown")


def test_drops_block_in_markdown_and_old_reports_load(data):
    r = _run(data, review=_with_drops({("ela9v2-a01", A1): [("band:6+-reading", "unjudged")]}))
    md = eval_v2.format_report_markdown(r)
    assert "### Dropped by reviewer and reason" in md and "| 6+-reading | unjudged | 1 |" in md
    assert "Planted codes with no verdict" in md
    old = r.model_dump()
    del old["drops"]
    assert eval_v2.EvalV2Report.model_validate(old).drops.total == 0


def test_working_grade_exact_and_within_one(data):
    wg = _run(data).working_grade
    assert (wg.n, wg.exact, wg.within_1, wg.unreported) == (2, 1, 2, 0)
    assert wg.exact_rate == 0.5 and wg.within_1_rate == 1.0


def test_working_grade_and_links_use_only_the_students_planted_families(data):
    r = _run(data)
    assert r.working_grade.n == 2  # the "other" family shares 9-10.RL.1.A / B but nobody has it
    review = _scripted()

    def link_only_in_other(*a):
        res = review(*a)
        return res.model_copy(update={"links": [_link("9-10.RL.1.A", "9-10.RL.1.B")]}) if res.links else res
    lk = _run(data, review=link_only_in_other).links_family_consistent
    assert (lk.links, lk.consistent) == (1, 0)


def test_working_grade_without_a_key_grade_is_counted_separately(data):
    key = data / "courses" / VERSION / "answer_key"
    (data / "skill_families.json").write_text(json.dumps({**FAMILIES, "ghost": ["11-12.W.1.A"]}),
                                              encoding="utf-8")
    _write(key / "students.jsonl", [{"student_hash": A1, "pair_id": "p", "profile": "x", "quirks": [],
                                     "baseline": {}, "start_week": 1,
                                     "families": [{"family": "ghost", "secure_through": 8,
                                                   "at_grade_level": 2}]}])
    _write(key / "expected" / "ela9v2-a01.jsonl", _expected("ela9v2-a01", {A1: [("11-12.W.1.A", 1)], B2: []}))
    wg = _run(data).working_grade
    assert (wg.n, wg.unreported, wg.no_key_grade) == (0, 0, 1)


def test_mvp_pipeline_skips_verdict_based_blocks(data):
    r = _run(data, options=ReviewOptions(pipeline="mvp"), repeat=2)
    assert r.growth.by_reviewer == {} and r.support_confusion == {} and r.agreement is None
    assert "mvp" in eval_v2.format_report_markdown(r).lower()


def test_unplanted_confirmed_has_a_denominator(data):
    u = _run(data).unplanted_confirmed
    assert (u.count, u.unplanted_on_failed) == (1, 1)


def test_working_grade_below_the_band_when_the_reviewer_gives_no_grade_on_progression_verdicts(data):
    review = _scripted()

    def no_grades(*a):
        r = review(*a)
        return r.model_copy(update={"verdicts": [v.model_copy(update={"working_grade": None})
                                                 for v in r.verdicts]})
    wg = _run(data, review=no_grades).working_grade
    # key 8 against "below the band" (counted as 5): wrong, and not within 1
    assert (wg.n, wg.exact, wg.within_1, wg.unreported) == (2, 0, 0, 0)


def test_working_grade_unreported_when_the_review_judged_no_grade_6_plus_code(data):
    review = _scripted()

    def silent(*a):
        return review(*a).model_copy(update={"verdicts": []})
    wg = _run(data, review=silent).working_grade
    assert (wg.n, wg.unreported) == (0, 2) and wg.exact_rate is None


def test_links_family_consistent_share(data):
    lk = _run(data).links_family_consistent
    assert (lk.links, lk.consistent, lk.share) == (2, 1, 0.5)


def test_strength_contradictions_count_strengths_on_planted_growth_codes(data):
    sc = _run(data).strength_contradictions
    assert (sc.strengths, sc.contradictions, sc.rate) == (2, 1, 0.5)


def test_unplanted_confirmed_uses_the_generators_extra_list(data):
    u = _run(data).unplanted_confirmed
    assert u.count == 1 and u.codes == {"9-10.RL.1.B": 1}


def test_fidelity_failed_submissions_are_excluded_and_counted(data):
    sub = _run(data).submissions
    assert (sub.reviewed, sub.scored, sub.excluded_fidelity, sub.no_key) == (6, 5, 1, 0)


def test_cost_uses_pricing_toml_and_the_settings_model(data):
    c = _run(data).cost
    assert c.calls == 6 and c.input_tokens == 1_000_000 and c.output_tokens == 100_000
    assert c.usd == pytest.approx(0.30 + 0.25) and c.unpriced_models == []


def test_cost_is_unknown_for_a_model_missing_from_pricing(data):
    r = eval_v2.evaluate_course(_s(data).model_copy(update={"llm_model": "mystery-model"}), VERSION,
                                None, ReviewOptions(), review_fn=_scripted(), students=None)
    assert r.cost.usd is None and r.cost.unpriced_models == ["mystery-model"]


def test_cost_prices_each_reviewer_by_the_model_its_result_names(data):
    base = _scripted()

    def review(*a):
        r = base(*a)
        return r.model_copy(update={"models": {k: "gemini-2.5-pro" for k in r.usage}})
    c = eval_v2.evaluate_course(_s(data).model_copy(update={"llm_model": "mystery-model"}), VERSION, None,
                                ReviewOptions(), review_fn=review, students=None).cost
    assert c.usd == pytest.approx(1.25 + 1.00) and c.unpriced_models == []  # pro, not the settings model


def test_through_week_and_students_limit_what_is_reviewed(data):
    seen = []
    base = _scripted()

    def spy(s, course, aid, user, o):
        seen.append((aid.split("@")[0], user))
        return base(s, course, aid, user, o)
    _run(data, review=spy, through_week=2, students=1)
    assert seen == [("ela9v2-a01", A1), ("ela9v2-a02", A1)]


def test_agreement_across_repeats_is_per_standard(data):
    calls = {"n": 0}
    base = _scripted()

    def noisy(s, course, aid, user, o):
        r = base(s, course, aid, user, o)
        calls["n"] += 1
        if aid.startswith("ela9v2-a01") and user == A1 and calls["n"] % 2 == 0:  # the second run differs
            r = r.model_copy(update={"verdicts": [
                v.model_copy(update={"verdict": "not_evident", "support_level": None})
                if v.standard_code == "9-10.RL.1.A" else v for v in r.verdicts]})
        return r
    a = _run(data, review=noisy, repeat=2).agreement
    assert a.runs == 2 and a.by_standard["9-10.RL.1.A"] < 1.0 and a.by_standard["9-10.W.3.A.c"] == 1.0
    assert 0 < a.rate < 1


def test_repeat_one_has_no_agreement(data):
    assert _run(data).agreement is None


def test_review_errors_are_counted_by_class_only(data):
    base = _scripted()

    def boom(s, course, aid, user, o):
        if aid.startswith("ela9v2-a02"):
            raise ValueError(SECRET)
        return base(s, course, aid, user, o)
    r = _run(data, review=boom)
    assert r.submissions.errors == {"ValueError": 2} and SECRET not in r.model_dump_json()


def test_daily_quota_stops_the_run(data):
    def quota(*_a):
        raise DailyQuotaExceeded()
    r = _run(data, review=quota)
    assert r.stopped == "DailyQuotaExceeded" and r.submissions.reviewed == 0


def test_real_review_one_with_fakes_repeats_agree_fully(data):
    r = eval_v2.evaluate_course(_s(data), VERSION, None, ReviewOptions(), repeat=2, students=None)
    assert r.submissions.reviewed == 6 and r.agreement.rate == 1.0 and r.agreement.runs == 2
    assert r.cost.calls > 0


def test_report_files_hold_codes_and_counts_only(data):
    s = _s(data)
    r = eval_v2.evaluate_course(s, VERSION, None, ReviewOptions(), students=None)
    json_path, md_path = eval_v2.write_reports(r, s.reports_dir)
    text = json_path.read_text(encoding="utf-8") + md_path.read_text(encoding="utf-8")
    assert SECRET not in text and "weakness" not in text.lower() and "severity" not in text.lower()
    assert "growth" in text.lower() and json.loads(json_path.read_text(encoding="utf-8"))["course"] == VERSION


def test_cli_course_routes_to_v2(data, monkeypatch, capsys):
    for k, v in {"FAKE_LLM": "1", "DATA_DIR": str(data), "REPORTS_DIR": str(data / "reports")}.items():
        monkeypatch.setenv(k, v)
    assert main(["evaluate", "--course", VERSION, "--through-week", "1", "--students", "1"]) == 0
    out = capsys.readouterr().out
    assert SECRET not in out and list((data / "reports").glob("evalv2-*.json"))
    assert list((data / "reports").glob("evalv2-*.md"))


def test_cli_course_rejects_bad_arguments(data, monkeypatch, capsys):
    monkeypatch.setenv("DATA_DIR", str(data))
    assert main(["evaluate", "--course", VERSION, "--repeat", "0"]) == 2
    assert main(["evaluate", "--course", VERSION, "--classroom", "1.01"]) == 2
    assert main(["evaluate", "--course", "9.99-nothing"]) == 2


def _fam(r):
    return r.growth.by_family


def test_family_counts_on_the_fixture(data):
    f = _fam(_run(data))
    ev, vo = f.families["evidence"], f.families["vocab"]
    assert (ev.tp, ev.fp, ev.fn, ev.precision, ev.recall) == (1, 0, 0, 1.0, 1.0)
    # vocab: planted and found for A1 in a03 (tp); found for B2 in a03, who has no families (fp)
    assert (vo.tp, vo.fp, vo.fn, vo.precision, vo.recall) == (1, 1, 0, 0.5, 1.0)


def test_family_found_on_a_different_code_is_a_tp_while_the_standard_count_misses(data):
    base = _scripted()

    def other_code(*a):
        res = base(*a)
        if a[3] != A1 or "a01" not in a[2]:
            return res
        return res.model_copy(update={"verdicts": [
            v.model_copy(update={"standard_code": "8.RL.1.A"}) if v.standard_code == "9-10.RL.1.A" else v
            for v in res.verdicts]})
    r = _run(data, review=other_code)
    ev = _fam(r).families["evidence"]
    assert (ev.tp, ev.fp, ev.fn) == (1, 0, 0)
    # a01/A1 per standard: 8.RL.1.A is an fp and 9-10.RL.1.A an fn (the exact counts were (2, 2, 1))
    assert (r.growth.overall.exact.tp, r.growth.overall.exact.fp, r.growth.overall.exact.fn) == (1, 3, 2)


def test_family_found_but_not_planted_is_an_fp(data):
    f = _fam(_run(data)).families
    assert f["vocab"].fp == 1  # B2 in a03
    assert f["other"].tp == 0 and f["other"].fp == 3 and f["other"].precision == 0.0
    assert f["other"].recall is None  # never planted: no denominator


def test_family_planted_but_not_found_is_an_fn(data):
    base = _scripted()

    def silent(*a):
        res = base(*a)
        if a[3] != A1 or "a03" not in a[2]:
            return res
        return res.model_copy(update={"verdicts": []})
    vo = _fam(_run(data, review=silent)).families["vocab"]
    assert (vo.tp, vo.fp, vo.fn, vo.recall) == (0, 1, 1, 0.0)


def test_family_code_in_two_families_counts_toward_both(data):
    f = _fam(_run(data)).families
    # 9-10.RL.1.A is in evidence and other: A1's a01 finding is a tp for evidence and an fp for other
    assert f["evidence"].tp == 1 and f["other"].fp >= 1
    # 9-10.RL.1.B is in vocab and other: A1's a03 finding is a tp for vocab and an fp for other
    assert f["vocab"].tp == 1 and f["other"].fp == 3  # a01/A1 (RL.1.A), a03/A1 and a03/B2 (RL.1.B)


def test_family_growth_outside_every_family_is_counted_separately(data):
    f = _fam(_run(data))
    assert f.outside_families == 1  # 9-10.W.3.A.d in a01/A1
    assert all(f.families[n].fp + f.families[n].tp for n in ("evidence", "vocab"))


def test_family_overall_is_micro_averaged(data):
    f = _fam(_run(data))
    assert (f.overall.tp, f.overall.fp, f.overall.fn) == (2, 4, 0)
    assert f.overall.precision == pytest.approx(2 / 6) and f.overall.recall == 1.0


def test_family_excludes_fidelity_failed_submissions(data):
    f = _fam(_run(data)).families
    # a02/A1 (RL.1.A and RL.1.B found, failed fidelity) adds nothing: evidence fp stays 0
    assert f["evidence"].fp == 0 and f["other"].fp == 3


def test_family_block_is_computed_for_the_mvp_pipeline(data):
    from agentic.models import Finding
    base = _scripted()

    def mvp(*a):
        res = base(*a)
        found = [v.standard_code for v in res.verdicts if v.verdict == "growth_area"]
        return res.model_copy(update={"pipeline": "mvp", "verdicts": [], "findings": [
            Finding(standard_code=c, concept="c", description="d", paragraphs=[], reasons=[],
                    observation_count=1, summary="Short note.", severity="moderate") for c in found]})
    r = _run(data, review=mvp, options=ReviewOptions(pipeline="mvp"))
    assert _fam(r).families["evidence"].tp == 1 and _fam(r).outside_families == 1


def test_markdown_has_the_family_table(data):
    md = eval_v2.format_report_markdown(_run(data))
    assert "### By skill family" in md and "| evidence | 1 | 0 | 0 |" in md
    assert "more than one family" in md and "outside every family" in md.lower()
    assert "by_family" in _run(data).model_dump_json()


# ---- reasoning level and thinking tokens (Task R) ----

def test_cost_strips_the_reasoning_suffix_before_pricing(data):
    base = _scripted()

    def review(*a):
        r = base(*a)
        return r.model_copy(update={"models": {k: "gemini-2.5-pro@low" for k in r.usage}})
    c = eval_v2.evaluate_course(_s(data).model_copy(update={"llm_model": "mystery-model"}), VERSION, None,
                                ReviewOptions(), review_fn=review, students=None).cost
    assert c.usd == pytest.approx(1.25 + 1.00) and c.unpriced_models == []


def _thinking_scripted():
    base = _scripted()

    def review(*a):
        r = base(*a)
        usage = {k: Usage(input_tokens=u.input_tokens, output_tokens=u.output_tokens, thinking_tokens=70_000,
                          calls=2, estimated=False) for k, u in r.usage.items()}
        return r.model_copy(update={"usage": usage})
    return review


def test_report_counts_thinking_tokens_in_cost_and_per_reviewer(data):
    r = _run(data, review=_thinking_scripted())
    assert r.cost.thinking_tokens == 70_000 and r.cost.output_tokens == 100_000  # thinking is inside output
    row = r.usage_by_reviewer["6+-reading"]
    assert (row.calls, row.input_tokens, row.output_tokens, row.thinking_tokens) == (2, 1_000_000, 100_000, 70_000)
    text = eval_v2.format_report(r)
    assert "30000 answer" in text and "70000 thinking" in text
    assert "thinking" in eval_v2.format_report_markdown(r)


def test_cli_evaluate_reasoning_reaches_the_options(data, monkeypatch, capsys):
    for k, v in {"FAKE_LLM": "1", "DATA_DIR": str(data), "REPORTS_DIR": str(data / "reports")}.items():
        monkeypatch.setenv(k, v)
    seen = []
    real = eval_v2.evaluate_course
    monkeypatch.setattr(eval_v2, "evaluate_course", lambda s, v, w, options, **kw: seen.append(options) or real(
        s, v, w, options, **kw))
    argv = ["evaluate", "--course", VERSION, "--through-week", "1", "--students", "1", "--profile", "lean"]
    assert main([*argv, "--reasoning", "3-5-all=low", "--reasoning", "6+-all=high"]) == 0
    assert seen[0].reasoning == {"3-5-all": "low", "6+-all": "high"}


def test_cli_evaluate_bad_reasoning_exits_2_before_any_review(data, monkeypatch, capsys):
    monkeypatch.setenv("FAKE_LLM", "1")
    monkeypatch.setenv("DATA_DIR", str(data))
    base = ["evaluate", "--course", VERSION, "--profile", "lean"]
    assert main([*base, "--reasoning", "nope=low"]) == 2
    assert "nope" in capsys.readouterr().err
    assert main([*base, "--reasoning", "3-5-all=extreme"]) == 2
    assert "extreme" in capsys.readouterr().err
    assert main([*base, "--reasoning", "3-5-all"]) == 2


# ---- Task W: working grade on the 6+ band, unplanted flags, spend cap ----

def _wg_run(data, fam_codes, levels, verdicts, dropped=()):
    """One student (A1), one family, planted on its last code at week 1; `levels` is code -> key level."""
    (data / "skill_families.json").write_text(json.dumps({"fam": fam_codes}), encoding="utf-8")
    key = data / "courses" / VERSION / "answer_key"
    _write(key / "students.jsonl", [{"student_hash": A1, "pair_id": "p", "profile": "x", "quirks": [],
                                     "baseline": {}, "start_week": 1,
                                     "families": [{"family": "fam", "secure_through": 8, "at_grade_level": 2}]}])
    _write(key / "trajectory.jsonl", [{"student_hash": A1, "week": 1, "standard": c, "level": lv}
                                      for c, lv in levels.items()])
    _write(key / "expected" / "ela9v2-a01.jsonl",
           _expected("ela9v2-a01", {A1: [(fam_codes[-1], 2)], B2: []}))

    def review(_s, _course, aid, user, _o):
        return _res(aid, user, "paragraph", verdicts).model_copy(update={"dropped": list(dropped)})
    return _run(data, review=review, through_week=1, students=1).working_grade


def _gv(code, wg=None):
    return _v(code, "growth_area", "6+-reading", "developing", wg=wg)


LOW = ["4.RL.1.A", "6.RL.1.A", "9-10.RL.1.A"]
LOW_LEVELS = {"6.RL.1.A": 2, "9-10.RL.1.A": 2}  # grade 4 is clear: key 4, below the band
HIGH = ["8.RL.1.A", "9-10.RL.1.A"]
HIGH_LEVELS = {"9-10.RL.1.A": 2}  # key 8


def test_working_grade_key_below_band_and_reviewer_null_is_exact(data):
    wg = _wg_run(data, LOW, LOW_LEVELS, [_gv("9-10.RL.1.A", None)])
    assert (wg.n, wg.exact, wg.within_1, wg.unreported) == (1, 1, 1, 0)


def test_working_grade_key_below_band_and_reviewer_6_is_within_one_not_exact(data):
    wg = _wg_run(data, LOW, LOW_LEVELS, [_gv("9-10.RL.1.A", 6)])
    assert (wg.n, wg.exact, wg.within_1) == (1, 0, 1)


def test_working_grade_key_8_and_reviewer_7_is_within_one(data):
    wg = _wg_run(data, HIGH, HIGH_LEVELS, [_gv("9-10.RL.1.A", 7)])
    assert (wg.n, wg.exact, wg.within_1) == (1, 0, 1)


def test_working_grade_key_8_and_reviewer_null_is_wrong(data):
    wg = _wg_run(data, HIGH, HIGH_LEVELS, [_gv("9-10.RL.1.A", None)])
    assert (wg.n, wg.exact, wg.within_1, wg.unreported) == (1, 0, 0, 0)


def test_working_grade_uses_the_lowest_grade_the_reviewer_gave(data):
    wg = _wg_run(data, HIGH, HIGH_LEVELS, [_gv("9-10.RL.1.A", 9), _gv("8.RL.1.A", 8)])
    assert (wg.exact, wg.within_1) == (1, 1)


def test_working_grade_family_with_no_6_plus_verdicts_is_unreported(data):
    wg = _wg_run(data, LOW, LOW_LEVELS, [_gv("4.RL.1.A", None)])  # only a grade 4 verdict
    assert (wg.n, wg.unreported, wg.no_key_grade) == (0, 1, 0)


def test_working_grade_family_with_no_6_plus_codes_is_no_key_grade(data):
    wg = _wg_run(data, ["3.RL.1.A", "4.RL.1.A"], {"4.RL.1.A": 2}, [_gv("4.RL.1.A", None)])
    assert (wg.n, wg.unreported, wg.no_key_grade) == (0, 0, 1)


def _flagging():
    base = _scripted()

    def review(s, course, aid, user, o):
        res = base(s, course, aid, user, o)
        if aid.startswith("ela9v2-a01") and user == B2:  # unplanted: RL.1.B, W.3.A.e, W.3.A.d
            return res.model_copy(update={"verdicts": [
                _v("9-10.RL.1.B", "growth_area", "6+-reading", "beginning"),
                _v("9-10.W.3.A.e", "growth_area", "6+-writing", "approaching"),
                _v("9-10.W.3.A.d", "growth_area", "6+-writing", "approaching")]})
        return res
    return review


def test_unplanted_flags_count_by_concept_and_family(data):
    u = _run(data, review=_flagging()).unplanted_flags
    # scored, reported, not planted: a01/A1 W.3.A.d; a01/B2 RL.1.B, W.3.A.e, W.3.A.d; a03/B2 RL.1.B
    assert u.total == 5
    assert [(c.concept, c.count, c.submissions) for c in u.concepts] == [
        ("9-10.W.3.A", 3, 2), ("9-10.RL.1.B", 2, 2)]
    # RL.1.B is in vocab and other (counts toward each); the W codes are in no family
    assert [(f.family, f.count, f.submissions) for f in u.families] == [
        ("(none)", 3, 2), ("other", 2, 2), ("vocab", 2, 2)]


def test_unplanted_flags_leave_out_fidelity_failed_submissions(data):
    u = _run(data).unplanted_flags  # a02/A1 (RL.1.B, failed fidelity) adds nothing
    assert u.total == 2 and {c.concept for c in u.concepts} == {"9-10.W.3.A", "9-10.RL.1.B"}


def test_markdown_lists_the_unplanted_flags(data):
    md = eval_v2.format_report_markdown(_run(data, review=_flagging()))
    assert "### Most often flagged, not planted" in md
    assert "| 9-10.W.3.A | 3 | 2 |" in md and "(none) 3" in md
    assert "band" in md.lower() and "grade 6" in md


def test_max_usd_stops_starting_new_submissions_and_marks_stopped(data):
    seen = []
    base = _scripted()

    def spy(s, course, aid, user, o):
        seen.append((aid.split("@")[0], user))
        return base(s, course, aid, user, o)
    r = _run(data, review=spy, max_usd=0.10)  # the first review alone costs 0.55
    assert seen == [("ela9v2-a01", A1)]
    assert r.stopped == "max_usd" and r.submissions.reviewed == 1 and r.max_usd == 0.10
    assert r.cost.usd == pytest.approx(0.55)
    md = eval_v2.format_report_markdown(r)
    assert "max_usd" in md and "0.5500" in md


def test_max_usd_not_reached_runs_everything(data):
    r = _run(data, max_usd=100)
    assert r.stopped is None and r.submissions.reviewed == 6


def test_max_usd_does_not_count_unpriced_models_and_says_so(data):
    base = _scripted()

    def review(*a):
        r = base(*a)
        return r.model_copy(update={"models": {k: "jev" for k in r.usage}})
    r = _run(data, review=review, max_usd=0.01)
    assert r.stopped is None and r.submissions.reviewed == 6 and r.cost.unpriced_models == ["jev"]
    assert "not counted" in eval_v2.format_report_markdown(r)


def test_cli_max_usd_reaches_evaluate_and_must_be_positive(data, monkeypatch, capsys):
    for k, v in {"FAKE_LLM": "1", "DATA_DIR": str(data), "REPORTS_DIR": str(data / "reports")}.items():
        monkeypatch.setenv(k, v)
    seen = []
    real = eval_v2.evaluate_course
    monkeypatch.setattr(eval_v2, "evaluate_course", lambda s, v, w, options, **kw: seen.append(kw) or real(
        s, v, w, options, **kw))
    argv = ["evaluate", "--course", VERSION, "--through-week", "1", "--students", "1"]
    assert main([*argv, "--max-usd", "2.5"]) == 0 and seen[0]["max_usd"] == 2.5
    assert main([*argv, "--max-usd", "0"]) == 2


def test_working_grade_incomplete_search_is_unreported_not_below(data):
    from agentic.models import Dropped
    marker = [Dropped(stage="band:6+-reading", reason="search_incomplete:RL.1.A")]
    wg = _wg_run(data, LOW, LOW_LEVELS, [_gv("9-10.RL.1.A", None)], dropped=marker)
    assert (wg.n, wg.unreported) == (0, 1)
    other = [Dropped(stage="band:6+-reading", reason="search_incomplete:RL.2.A")]  # another group
    wg = _wg_run(data, LOW, LOW_LEVELS, [_gv("9-10.RL.1.A", None)], dropped=other)
    assert (wg.n, wg.exact, wg.unreported) == (1, 1, 0)


def test_working_grade_all_not_evident_family_is_unreported(data):
    verdicts = [_v("6.RL.1.A", "not_evident", "6+-reading"), _v("9-10.RL.1.A", "not_evident", "6+-reading"),
                _gv("4.RL.1.A", None)]  # a grade 4 growth area does not make the 6+ part reported
    wg = _wg_run(data, LOW, LOW_LEVELS, verdicts)
    assert (wg.n, wg.unreported) == (0, 1)


def _counts(c):
    return (c.tp, c.fp, c.fn)


def test_by_student_counts_exact_and_family_per_student(data):
    by = _run(data).growth.by_student
    assert set(by) == {A1, B2}
    assert _counts(by[A1].exact) == (2, 1, 1) and _counts(by[A1].family) == (2, 2, 0)
    assert _counts(by[B2].exact) == (0, 1, 0) and _counts(by[B2].family) == (0, 2, 0)


def test_by_student_sums_to_the_overall_counts(data):
    r = _run(data)
    for level, overall in (("exact", r.growth.overall.exact), ("family", r.growth.by_family.overall)):
        got = tuple(sum(getattr(getattr(s, level), k)
                        for s in r.growth.by_student.values()) for k in ("tp", "fp", "fn"))
        assert got == (overall.tp, overall.fp, overall.fn)


def test_by_student_is_in_the_json_report_and_not_in_the_markdown(data):
    r = _run(data)
    assert A1 in r.model_dump()["growth"]["by_student"]
    assert A1 not in eval_v2.format_report_markdown(r)


def test_students_limit_takes_the_first_roster_students_in_roster_order(data):
    r = _run(data, students=1)
    assert list(r.growth.by_student) == [eval_v2.CourseDir(data, VERSION).roster()[0].student_hash]

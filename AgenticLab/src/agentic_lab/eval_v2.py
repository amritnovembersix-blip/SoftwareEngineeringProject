"""v2 evaluation: score course reviews against the answer key (`evaluate --course`).

Every review goes through `review_one` with nothing persisted. The answer key is read from
`answer_key/expected/`, `trajectory.jsonl`, `fidelity.jsonl` and `skill_families.json`. Reports hold
codes and counts only; never submission text and never trace replies. Precision and recall here score the
reviewer (SASS) against the key; they are not a student score.

Reading choices for the metrics (see the Task 14 report): growth areas come from `growth_area` verdicts
(`findings` on the mvp pipeline); the support-level confusion counts exact matches only; a family's key
working grade is the highest family grade (up to the course grade) whose codes are all at level 0; the
reviewer's working grade for a family is the lowest `working_grade` among its verdicts.

Working grade compares on the progression band only: grades 6 up to the course grade. The reviewer gives a
working grade only from grade 6+ progressions, or none for "below the band" (BELOW). So a key grade under 6
is expected as BELOW; a family whose verdicts include grade 6+ codes but no working grade is BELOW; a
family with no grade 6+ verdicts, only not_evident ones, or an incomplete search on one of its groups
(`search_incomplete:<group>`) is unreported; a family with no grade 6+ codes is skipped (no key grade).
BELOW counts as 5 for the within-1 arithmetic and equals only BELOW for exact.
"""
from collections import Counter, defaultdict
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel

from agentic import tuning
from agentic.config import Settings
from agentic.coursedata import CourseDir, lms_assignment_id
from agentic.dataset import read_rows
from agentic.llm.base import DailyQuotaExceeded
from agentic.models import Dropped, ReviewResult
from agentic.review.v2.options import ReviewOptions
from agentic.review.v2.run import review_one
from agentic.review.v2.scope import group_of
from agentic.standards.strand_filter import strand_of
from agentic_lab.answer_key import ExpectedRow, FidelityRow, StudentKey, TrajectoryRow, answer_key_path
from agentic_lab.eval import Counts, concept_of, score
from agentic_lab.synth.families import SkillFamily, grade_key, load_families

_LEVELS = {1: "approaching", 2: "developing", 3: "beginning"}
BAND_START = 6  # the first progression grade
BELOW = BAND_START - 1  # "below the band", counted as 5 in the working-grade arithmetic


class Scores(BaseModel):
    tp: int
    fp: int
    fn: int
    precision: float
    recall: float


class GrowthBlock(BaseModel):
    exact: Scores
    concept: Scores


class FamilyScores(BaseModel):
    tp: int
    fp: int
    fn: int
    precision: float | None  # None when the denominator is 0
    recall: float | None


class FamilyGrowth(BaseModel):
    """Growth areas judged per skill family: planted and found as a family, whatever the code."""
    families: dict[str, FamilyScores]
    overall: FamilyScores  # pooled counts across families (micro-average)
    outside_families: int  # growth areas whose code is in no family (not an fp for any family)


class Tally3(BaseModel):
    tp: int
    fp: int
    fn: int


class StudentGrowth(BaseModel):
    """One student's growth counts, exact (by code) and family level, so runs can be compared on the same
    students. Codes and counts only."""
    exact: Tally3
    family: Tally3


class Growth(BaseModel):
    overall: GrowthBlock
    by_reviewer: dict[str, GrowthBlock]
    by_type: dict[str, GrowthBlock]
    by_family: FamilyGrowth
    by_student: dict[str, StudentGrowth] = {}  # JSON report only; not in the Markdown


class WorkingGrade(BaseModel):
    n: int  # comparisons made
    exact: int
    within_1: int
    unreported: int  # the reviewer gave no working grade for a planted family
    no_key_grade: int  # the family has no grade at or below the course grade
    exact_rate: float | None
    within_1_rate: float | None


class Links(BaseModel):
    links: int
    consistent: int
    share: float | None


class Strengths(BaseModel):
    strengths: int
    contradictions: int  # strengths on codes the key plants as growth areas
    rate: float | None


class ConceptFlags(BaseModel):
    concept: str
    count: int  # growth areas reported on this concept, not planted
    submissions: int  # distinct submissions they were reported on


class FamilyFlags(BaseModel):
    family: str  # "(none)" for codes in no family
    count: int
    submissions: int


class UnplantedFlags(BaseModel):
    """Growth areas the review reported that the key does not plant, over scored submissions."""
    total: int
    concepts: list[ConceptFlags]  # every concept, most-flagged first
    families: list[FamilyFlags]


class Unplanted(BaseModel):
    count: int  # reported, not planted, and in the generator's `extra` list
    codes: dict[str, int]
    unplanted_on_failed: int  # all unplanted growth areas on fidelity-failed submissions (the denominator)


class Cost(BaseModel):
    calls: int
    input_tokens: int
    output_tokens: int  # includes thinking_tokens
    thinking_tokens: int = 0  # the part of output_tokens the model spent thinking (reports before it: 0)
    usd: float | None  # None when a model is missing from pricing.toml
    priced_usd: float = 0.0  # the priced part alone (what --max-usd counts)
    unpriced_models: list[str]
    calls_per_submission: float | None
    usd_per_submission: float | None


class ReviewerUsage(BaseModel):
    """What one reviewer (or the root-cause step) used across the run."""
    calls: int
    input_tokens: int
    output_tokens: int  # includes thinking_tokens
    thinking_tokens: int
    model: str | None  # "<model>@<level>" when the level is not default


class Agreement(BaseModel):
    runs: int
    pairs: int  # (submission, standard) pairs
    unanimous: int
    rate: float | None
    by_standard: dict[str, float]


class SubmissionCounts(BaseModel):
    reviewed: int
    scored: int
    excluded_fidelity: int
    no_key: int
    errors: dict[str, int]  # exception class -> count


class Inversions(BaseModel):
    """`level_inversion` flags: a progression whose support level fell as the grade rose."""
    total: int
    by_reviewer: dict[str, int]


class Drops(BaseModel):
    """Reviewer drops on scored submissions (`level_inversion` is a flag, kept in `Inversions`), and why
    planted codes got no verdict. Rule names, codes and counts only."""
    total: int = 0
    by_reviewer: dict[str, dict[str, int]] = {}  # reviewer -> rule name -> count
    unreviewed_total: int = 0
    unreviewed_by_reason: dict[str, int] = {}  # rule name or "unattributed" -> planted codes
    unreviewed_by_reviewer: dict[str, dict[str, int]] = {}  # reviewer ("unknown") -> reason -> planted codes


class EvalV2Report(BaseModel):
    course: str
    through_week: int | None
    pipeline: str
    profile: str
    repeat: int
    submissions: SubmissionCounts
    growth: Growth
    support_confusion: dict[str, dict[str, int]]  # planned level -> reported support level -> count
    working_grade: WorkingGrade
    level_inversions: Inversions
    drops: Drops = Drops()
    links_family_consistent: Links
    strength_contradictions: Strengths
    unplanted_confirmed: Unplanted
    unplanted_flags: UnplantedFlags = UnplantedFlags(total=0, concepts=[], families=[])
    cost: Cost
    usage_by_reviewer: dict[str, ReviewerUsage] = {}
    agreement: Agreement | None
    stopped: str | None = None  # exception class, or "max_usd", that ended the run early
    max_usd: float | None = None  # the spend cap the run was given
    notes: list[str] = []


def _scores(c: Counts) -> Scores:
    return Scores(tp=c.tp, fp=c.fp, fn=c.fn, precision=c.precision, recall=c.recall)


def _rate(n: int, d: int) -> float | None:
    return n / d if d else None


class _Tally:
    """Exact and concept counts summed over submissions, per group key."""

    def __init__(self):
        self.groups: dict[str, list[Counts]] = defaultdict(lambda: [Counts(tp=0, fp=0, fn=0) for _ in range(2)])

    def add(self, key: str, found: set[str], expected: set[str]) -> None:
        for acc, level in zip(self.groups[key], ("exact", "concept")):
            s = score(found, expected, level)
            acc.tp, acc.fp, acc.fn = acc.tp + s.tp, acc.fp + s.fp, acc.fn + s.fn

    def block(self, key: str) -> GrowthBlock:
        exact, concept = self.groups[key]
        return GrowthBlock(exact=_scores(exact), concept=_scores(concept))


def _family_scores(c: Counter) -> FamilyScores:
    return FamilyScores(tp=c["tp"], fp=c["fp"], fn=c["fn"], precision=_rate(c["tp"], c["tp"] + c["fp"]),
                        recall=_rate(c["tp"], c["tp"] + c["fn"]))


def _growth(res: ReviewResult) -> dict[str, tuple[str | None, str]]:
    """code -> (support level, reviewer) for each growth area the review reported."""
    if res.pipeline == "v2":
        return {v.standard_code: (v.support_level, v.reviewer) for v in res.verdicts
                if v.verdict == "growth_area"}
    return {f.standard_code: (f.severity, "mvp") for f in res.findings}


def _reviewer_of(res: ReviewResult) -> dict[str, str]:
    return {v.standard_code: v.reviewer for v in res.verdicts}


def _reviewer_of_drop(d: Dropped) -> str:
    return d.stage.removeprefix("band:")


def _drop_rule(d: Dropped) -> str:
    return d.reason.split(":", 1)[0]


_NON_FATAL = {"bad_step_level", "duplicate_judgment", "level_inversion"}


def _fatal(d: Dropped) -> bool:
    """A drop that removes a verdict; strength and level-flag drops leave the verdict standing."""
    rule = _drop_rule(d)
    return not (rule.startswith("strength_") or rule in _NON_FATAL)


def _owner_of(code: str, plan: list[tuple]) -> str | None:
    """The reviewer id whose grade range and strands hold the code (reading strand not narrowed: planted
    codes are in the assignment's scope), or None without a plan or when none does."""
    grade = grade_key(code)
    return next((spec.id for spec, lo, hi in plan
                 if lo <= grade <= hi and strand_of(code) in spec.strands), None)


def _attribute(code: str, dropped: list[Dropped], owner: str | None, planned: bool) -> tuple[str, str]:
    """(reason, reviewer) for a planted code with no verdict. A fatal drop naming the code or its group,
    from the owning reviewer when the plan names one; else a plain `unjudged` from the owner; else
    "unattributed" under the owner ("unknown" when none). Without a plan (mvp) any stage may match."""
    names = {code, group_of(code)}
    mine = [d for d in dropped
            if _fatal(d) and (not planned or owner is None or _reviewer_of_drop(d) == owner)]
    for d in mine:
        rule, _, name = d.reason.partition(":")
        if name in names:
            return rule, _reviewer_of_drop(d)
    if planned and owner is not None and any(d.reason == "unjudged" for d in mine):
        return "unjudged", owner
    return "unattributed", owner or "unknown"


def _key_working_grade(fam: SkillFamily, levels: dict[str, int], course_grade: int) -> int | None:
    """The key's working grade on the band (BAND_START up to the course grade); BELOW when under it.
    None when the family has no code in the band."""
    grades = sorted({grade_key(c) for c in fam.codes if grade_key(c) <= course_grade})
    if not any(g >= BAND_START for g in grades):
        return None
    clear = [g for g in grades if all(levels.get(c, 0) == 0 for c in fam.codes if grade_key(c) == g)]
    key = max(clear) if clear else min(grades) - 1
    return key if key >= BAND_START else BELOW


def _given_working_grade(fam: SkillFamily, res: ReviewResult) -> int | None:
    """The reviewer's working grade for a family: the lowest grade it gave, BELOW when it judged grade 6+
    codes of the family but gave none, None (unreported) when it judged none, when every grade 6+ verdict
    is not_evident, or when the search on one of its 6+ groups was incomplete (`search_incomplete:<group>`,
    Ruling S1-4: a null grade there is not "below the band")."""
    mine = [v for v in res.verdicts if v.standard_code in fam.codes]
    given = [v.working_grade for v in mine if v.working_grade is not None]
    if given:
        return min(given)
    band = [v for v in mine if grade_key(v.standard_code) >= BAND_START]
    if not band or all(v.verdict == "not_evident" for v in band):
        return None
    groups = {".".join(c.split(".")[1:4]) for c in fam.codes if grade_key(c) >= BAND_START}
    if any(d.reason in {f"search_incomplete:{g}" for g in groups} for d in res.dropped):
        return None
    return BELOW


def _cost(s: Settings, results: list[ReviewResult], profile: str, submissions: int) -> Cost:
    pricing = tuning.load_pricing(s.config_dir)
    # Each result names the model each reviewer actually used (`models`). Runs saved before it did fall
    # back to the reviewer's profile model, else the configured chat model.
    profile_models = {}
    if any(set(r.usage) - set(r.models) for r in results):
        profile_models = {r.id: r.model for r in tuning.load_profile(s.config_dir, profile).reviewers}
    calls = in_tok = out_tok = thinking = 0
    priced = 0.0
    unpriced: set[str] = set()

    def spend(model: str, n_in: int, n_out: int) -> None:
        nonlocal priced
        model = model.partition("@")[0]  # "<model>@<level>" is priced as the model
        if model in pricing:
            priced += (n_in * pricing[model][0] + n_out * pricing[model][1]) / 1_000_000
        else:
            unpriced.add(model)

    for r in results:
        calls += r.llm_calls
        for reviewer, u in r.usage.items():
            model = r.models.get(reviewer) or profile_models.get(reviewer) or s.llm_model
            in_tok, out_tok = in_tok + u.input_tokens, out_tok + u.output_tokens
            thinking += u.thinking_tokens
            spend(model, u.input_tokens, u.output_tokens)
        if r.verification.input_tokens:  # the verifier's input tokens only
            in_tok += r.verification.input_tokens
            spend(r.models.get("verify") or r.verification.model or s.llm_model, r.verification.input_tokens, 0)
    usd = None if unpriced else priced
    return Cost(calls=calls, input_tokens=in_tok, output_tokens=out_tok, thinking_tokens=thinking, usd=usd,
                priced_usd=priced, unpriced_models=sorted(unpriced), calls_per_submission=_rate(calls, submissions),
                usd_per_submission=None if usd is None else _rate(usd, submissions))


def _usage_by_reviewer(results: list[ReviewResult]) -> dict[str, ReviewerUsage]:
    """Tokens per reviewer, so a mixed-level run shows where they went."""
    rows: dict[str, ReviewerUsage] = {}
    for r in results:
        for reviewer, u in r.usage.items():
            row = rows.setdefault(reviewer, ReviewerUsage(calls=0, input_tokens=0, output_tokens=0,
                                                          thinking_tokens=0, model=None))
            row.calls += u.calls
            row.input_tokens += u.input_tokens
            row.output_tokens += u.output_tokens
            row.thinking_tokens += u.thinking_tokens
            row.model = r.models.get(reviewer) or row.model
    return dict(sorted(rows.items()))


def evaluate_course(s: Settings, version: str, through_week: int | None, options: ReviewOptions,
                    repeat: int = 1, students: int | None = None, max_usd: float | None = None,
                    review_fn: Callable = review_one) -> EvalV2Report:
    """Review every released submission of `version` (assignments due by `through_week`, first `students`
    roster students) `repeat` times, and score the first run against the answer key. With `max_usd`, stop
    starting submissions once the priced spend so far exceeds it (the report is partial, `stopped="max_usd"`)."""
    cd = CourseDir(s.data_dir, version)
    course = cd.course()
    key_dir = answer_key_path(s.data_dir, version)
    families = load_families(Path(s.data_dir) / "skill_families.json")
    trajectory: dict[tuple[str, int], dict[str, int]] = {}
    for t in read_rows(key_dir / "trajectory.jsonl", TrajectoryRow):
        trajectory.setdefault((t.student_hash, t.week), {})[t.standard] = t.level
    student_families = {k.student_hash: {e.family for e in k.families}
                        for k in read_rows(key_dir / "students.jsonl", StudentKey)}
    resolved = options.pipeline or s.review_pipeline
    v2 = resolved == "v2"
    fid_path = key_dir / "fidelity.jsonl"
    fidelity = {r.submission_id: r for r in read_rows(fid_path, FidelityRow)} if fid_path.exists() else {}
    roster = [r.student_hash for r in cd.roster()]
    in_run = set(roster[:students] if students is not None else roster)

    plan = (tuning.plan_reviewers(tuning.load_profile(s.config_dir, options.profile or s.bands_profile),
                           course.grade) if v2 else [])
    tally = _Tally()
    drop_counts: dict[str, Counter] = defaultdict(Counter)
    unrev_reason: Counter[str] = Counter()
    unrev_reviewer: dict[str, Counter] = defaultdict(Counter)
    confusion: dict[str, Counter[str]] = defaultdict(Counter)
    wg = Counter()
    link_n = link_ok = strengths = contradictions = 0
    fam_counts: dict[str, Counter] = {n: Counter() for n in families}
    student_counts: dict[str, dict[str, Counter]] = defaultdict(lambda: {"exact": Counter(), "family": Counter()})
    outside = 0
    unplanted: Counter[str] = Counter()
    unplanted_failed = 0
    flag_concepts: Counter[str] = Counter()
    flag_families: Counter[str] = Counter()
    flag_concept_subs: dict[str, set[str]] = defaultdict(set)
    flag_family_subs: dict[str, set[str]] = defaultdict(set)
    spent = 0.0
    counts = Counter()
    errors: Counter[str] = Counter()
    every: list[ReviewResult] = []
    verdict_runs: list[list[dict[str, str]]] = []  # per submission, per run: code -> verdict
    stopped = None

    for assignment in cd.assignments():
        if through_week is not None and assignment.due_offset.week > through_week:
            continue
        expected = {e.submission_id: e for e in read_rows(key_dir / "expected" / f"{assignment.id}.jsonl",
                                                          ExpectedRow)}
        latest = {}
        for row in cd.submissions(assignment.id):
            if row.student_hash in in_run and row.attempt >= latest.get(row.student_hash, row).attempt:
                latest[row.student_hash] = row
        for row in latest.values():
            if max_usd is not None and spent > max_usd:
                stopped = "max_usd"
                break
            try:
                runs = [review_fn(s, course.id, lms_assignment_id(assignment.id, version), row.student_hash,
                                  options) for _ in range(repeat)]
            except DailyQuotaExceeded:
                stopped = "DailyQuotaExceeded"
                break
            except Exception as err:  # noqa: BLE001 - the message may quote submission text; class only
                errors[type(err).__name__] += 1
                continue
            res = runs[0]
            if max_usd is not None:
                spent += _cost(s, runs, options.profile or s.bands_profile, 1).priced_usd
            counts["reviewed"] += 1
            every += runs
            verdict_runs.append([{v.standard_code: v.verdict for v in r.verdicts} for r in runs])
            key = expected.get(row.submission_id)
            if key is None:
                counts["no_key"] += 1
                continue
            planted = {w.standard: w.level for w in key.weaknesses}
            found = _growth(res)
            fid = fidelity.get(row.submission_id)
            for code in found.keys() - planted.keys():  # reported, not planted, and the checker saw it too
                if fid is not None:
                    unplanted_failed += 1
                    if code in fid.extra:
                        unplanted[code] += 1
            if fid is not None:
                counts["excluded_fidelity"] += 1
                continue
            counts["scored"] += 1
            for code in found.keys() - planted.keys():  # over-flagging, by concept and by family
                names = [f.name for f in families.values() if code in f.codes] or ["(none)"]
                flag_concepts[concept_of(code)] += 1
                flag_concept_subs[concept_of(code)].add(row.submission_id)
                for name in names:
                    flag_families[name] += 1
                    flag_family_subs[name].add(row.submission_id)
            owner = _reviewer_of(res)
            for d in res.dropped:
                if _drop_rule(d) != "level_inversion":
                    drop_counts[_reviewer_of_drop(d)][_drop_rule(d)] += 1
            for code in planted.keys() - owner.keys():
                reason, who = _attribute(code, res.dropped, _owner_of(code, plan), v2)
                unrev_reason[reason] += 1
                unrev_reviewer[who][reason] += 1
            by_reviewer = defaultdict(lambda: (set(), set()))
            for code, (_, reviewer) in found.items():
                by_reviewer[reviewer][0].add(code)
            for code in planted:
                by_reviewer[owner.get(code, "unreviewed")][1].add(code)
            tally.add("all", set(found), set(planted))
            mine_counts = student_counts[row.student_hash]
            exact = score(set(found), set(planted), "exact")
            mine_counts["exact"].update(tp=exact.tp, fp=exact.fp, fn=exact.fn)
            tally.add(f"type:{res.submission_type}", set(found), set(planted))
            for reviewer, (f, e) in by_reviewer.items() if v2 else ():
                tally.add(f"reviewer:{reviewer}", f, e)
            for code in (found.keys() & planted.keys()) if v2 else ():
                confusion[_LEVELS[planted[code]]][found[code][0] or "none"] += 1
            levels = trajectory.get((row.student_hash, assignment.week), {})
            mine = [families[n] for n in sorted(student_families.get(row.student_hash, ())) if n in families]
            in_family = set()
            for fam in families.values():  # a code in several families counts toward each
                got = set(fam.codes) & found.keys()
                in_family |= got
                is_planted = fam in mine and bool(set(fam.codes) & planted.keys())
                outcome = "tp" if is_planted and got else "fp" if got else "fn" if is_planted else "none"
                fam_counts[fam.name][outcome] += 1
                mine_counts["family"][outcome] += 1
            outside += len(found.keys() - in_family)
            for fam in mine:  # only the families the key plants for this student
                if not set(fam.codes) & planted.keys():
                    continue
                truth = _key_working_grade(fam, levels, course.grade)
                if truth is None:
                    wg["no_key_grade"] += 1
                    continue
                given = _given_working_grade(fam, res)
                if given is None:
                    wg["unreported"] += 1
                    continue
                wg["n"] += 1
                wg["exact"] += given == truth
                wg["within_1"] += abs(given - truth) <= 1
            for link in res.links:
                link_n += 1
                link_ok += any(link.finding_code in f.codes and link.root_code in f.codes
                               for f in mine)
            for v in res.verdicts:
                if v.notable_strength:
                    strengths += 1
                    contradictions += v.standard_code in planted
        if stopped:
            break

    agreement = None
    if repeat > 1 and v2:
        pairs: Counter[str] = Counter()
        same: Counter[str] = Counter()
        for maps in verdict_runs:
            for code in set().union(*maps):
                pairs[code] += 1
                same[code] += len({m.get(code, "absent") for m in maps}) == 1
        agreement = Agreement(runs=repeat, pairs=sum(pairs.values()), unanimous=sum(same.values()),
                              rate=_rate(sum(same.values()), sum(pairs.values())),
                              by_standard={c: same[c] / pairs[c] for c in sorted(pairs)})

    drops = Drops(
        total=sum(sum(c.values()) for c in drop_counts.values()),
        by_reviewer={k: dict(sorted(c.items())) for k, c in sorted(drop_counts.items())},
        unreviewed_total=sum(unrev_reason.values()), unreviewed_by_reason=dict(sorted(unrev_reason.items())),
        unreviewed_by_reviewer={k: dict(sorted(c.items())) for k, c in sorted(unrev_reviewer.items())})
    inversions = Counter(d.stage.removeprefix("band:") for r in every for d in r.dropped
                         if d.reason.startswith("level_inversion:"))
    keys = tally.groups.keys()
    overall = tally.block("all")
    profile = options.profile or s.bands_profile
    return EvalV2Report(
        course=version, through_week=through_week, pipeline=resolved, profile=profile, repeat=repeat,
        submissions=SubmissionCounts(reviewed=counts["reviewed"], scored=counts["scored"],
                                     excluded_fidelity=counts["excluded_fidelity"], no_key=counts["no_key"],
                                     errors=dict(errors)),
        growth=Growth(
            overall=overall,
            by_reviewer={k[9:]: tally.block(k) for k in sorted(keys) if k.startswith("reviewer:")},
            by_type={k[5:]: tally.block(k) for k in sorted(keys) if k.startswith("type:")},
            by_family=FamilyGrowth(
                families={n: _family_scores(c) for n, c in fam_counts.items()},
                overall=_family_scores(sum((Counter({k: c[k] for k in ("tp", "fp", "fn")})
                                            for c in fam_counts.values()), Counter())),
                outside_families=outside),
            by_student={h: StudentGrowth(**{k: Tally3(tp=c[k]["tp"], fp=c[k]["fp"], fn=c[k]["fn"])
                                            for k in ("exact", "family")})
                        for h, c in ((h, student_counts[h]) for h in roster if h in student_counts)}),
        support_confusion={k: dict(v) for k, v in confusion.items()},
        working_grade=WorkingGrade(n=wg["n"], exact=wg["exact"], within_1=wg["within_1"],
                                   unreported=wg["unreported"], no_key_grade=wg["no_key_grade"],
                                   exact_rate=_rate(wg["exact"], wg["n"]),
                                   within_1_rate=_rate(wg["within_1"], wg["n"])),
        level_inversions=Inversions(total=sum(inversions.values()),
                                    by_reviewer=dict(sorted(inversions.items()))),
        drops=drops,
        links_family_consistent=Links(links=link_n, consistent=link_ok, share=_rate(link_ok, link_n)),
        strength_contradictions=Strengths(strengths=strengths, contradictions=contradictions,
                                          rate=_rate(contradictions, strengths)),
        unplanted_confirmed=Unplanted(count=sum(unplanted.values()), codes=dict(unplanted),
                                    unplanted_on_failed=unplanted_failed),
        unplanted_flags=UnplantedFlags(
            total=sum(flag_concepts.values()),
            concepts=[ConceptFlags(concept=c, count=n, submissions=len(flag_concept_subs[c]))
                      for c, n in sorted(flag_concepts.items(), key=lambda kv: (-kv[1], kv[0]))],
            families=[FamilyFlags(family=f, count=n, submissions=len(flag_family_subs[f]))
                      for f, n in sorted(flag_families.items(), key=lambda kv: (-kv[1], kv[0]))]),
        cost=_cost(s, every, profile, counts["reviewed"]), usage_by_reviewer=_usage_by_reviewer(every),
        agreement=agreement, stopped=stopped, max_usd=max_usd,
        notes=[] if v2 else ["pipeline mvp: no verdicts, so the by-reviewer, support-level and agreement "
                             "blocks are not computed"])


def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{x:.0%}"


def format_report(r: EvalV2Report) -> str:
    g = r.growth.overall
    sub = r.submissions
    lines = [f"Course {r.course}: {sub.reviewed} reviewed, {sub.scored} scored, {sub.excluded_fidelity} "
             f"excluded (fidelity), {sub.no_key} without a key, {sum(sub.errors.values())} errors",
             f"Growth areas, exact: precision {g.exact.precision:.2f}, recall {g.exact.recall:.2f}; "
             f"concept: precision {g.concept.precision:.2f}, recall {g.concept.recall:.2f}",
             f"Working grade: exact {_pct(r.working_grade.exact_rate)}, within 1 "
             f"{_pct(r.working_grade.within_1_rate)} (n={r.working_grade.n})",
             f"Level inversions: {r.level_inversions.total}",
             f"Links in one family: {_pct(r.links_family_consistent.share)}; strength contradictions: "
             f"{_pct(r.strength_contradictions.rate)}; unplanted but confirmed: {r.unplanted_confirmed.count}",
             f"Cost: {r.cost.calls} calls, {r.cost.input_tokens} in / {r.cost.output_tokens} out tokens "
             f"({r.cost.output_tokens - r.cost.thinking_tokens} answer + {r.cost.thinking_tokens} thinking), "
             + ("USD unknown" if r.cost.usd is None else f"USD {r.cost.usd:.4f}")]
    if r.agreement:
        lines.append(f"Agreement over {r.repeat} runs: {_pct(r.agreement.rate)}")
    if r.stopped:
        lines.append(f"Stopped early: {r.stopped}")
    return "\n".join(lines)


def format_report_markdown(r: EvalV2Report) -> str:
    def table(header: list[str], rows: list[list[str]]) -> list[str]:
        return (["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
                + ["| " + " | ".join(row) + " |" for row in rows])

    def block(name: str, b: GrowthBlock) -> list[str]:
        return [name, str(b.exact.tp), str(b.exact.fp), str(b.exact.fn), f"{b.exact.precision:.2f}",
                f"{b.exact.recall:.2f}", f"{b.concept.precision:.2f}", f"{b.concept.recall:.2f}"]

    sub = r.submissions
    out = [f"# Evaluation of course {r.course} (pipeline {r.pipeline}, profile {r.profile})", "",
           f"- Through week: {r.through_week if r.through_week is not None else 'all'}; runs per submission: "
           f"{r.repeat}",
           f"- Submissions: {sub.reviewed} reviewed, {sub.scored} scored, {sub.excluded_fidelity} excluded "
           f"(failed the fidelity check), {sub.no_key} without a key",
           f"- Errors by class: {', '.join(f'{k} x{v}' for k, v in sub.errors.items()) or '-'}"]
    if r.stopped:
        out.append(f"- Stopped early: {r.stopped}")
    out += [f"- Note: {n}" for n in r.notes]
    head = ["Group", "TP", "FP", "FN", "Precision", "Recall", "Concept precision", "Concept recall"]
    out += ["", "## Growth areas", ""]
    out += table(head, [block("all", r.growth.overall)])
    out += ["", "### By reviewer", ""]
    out += table(head, [block(k, v) for k, v in r.growth.by_reviewer.items()])
    out += ["", "### By submission type", ""]
    out += table(head, [block(k, v) for k, v in r.growth.by_type.items()])
    fam_head = ["Family", "TP", "FP", "FN", "Precision", "Recall"]

    def fam_row(name: str, s: FamilyScores) -> list[str]:
        return [name, str(s.tp), str(s.fp), str(s.fn), "n/a" if s.precision is None else f"{s.precision:.2f}",
                "n/a" if s.recall is None else f"{s.recall:.2f}"]

    bf = r.growth.by_family
    out += ["", "### By skill family", ""]
    out += table(fam_head, [fam_row(n, s) for n, s in bf.families.items()] + [fam_row("all (pooled)", bf.overall)])
    out += ["", "- A family is planted when the student's key includes it and the submission's expected growth "
            "codes include one of its codes; it is found when any reported growth area has one of its codes "
            "(the code need not match).",
            "- A code in more than one family counts toward each of them.",
            f"- {bf.outside_families} growth areas had a code outside every family; they are not an fp for "
            "any family."]
    out += ["", "## Support level (planned by reported, exact matches)", ""]
    levels = list(_LEVELS.values())
    out += table(["Planned", *levels], [[p, *[str(r.support_confusion.get(p, {}).get(x, 0)) for x in levels]]
                                        for p in levels])
    wg = r.working_grade
    out += ["", "## Working grade", "",
            f"- Comparisons: {wg.n}; exact {_pct(wg.exact_rate)}; within 1 {_pct(wg.within_1_rate)}; "
            f"not reported {wg.unreported}; no key grade {wg.no_key_grade}",
            "- Compared on the progression band, grade 6 up to the course grade. A key grade under 6 is "
            "expected as below the band; the reviewer is below the band when it judged grade 6+ codes of the "
            "family but gave no working grade, and not reported when it judged none. Below the band counts as "
            "5 for within 1. A family with no grade 6+ code has no key grade and is skipped.", "",
            "## Level inversions", "",
            f"- {r.level_inversions.total} progressions had a support level that fell as the grade rose "
            "(flagged, levels kept): "
            + (", ".join(f"{k} x{v}" for k, v in r.level_inversions.by_reviewer.items()) or "-"), "",
            "### Dropped by reviewer and reason", ""]
    out += table(["Reviewer", "Reason", "Count"],
                 [[who, rule, str(n)] for who, c in r.drops.by_reviewer.items() for rule, n in c.items()]
                 or [["-", "-", "0"]])
    out += ["", f"- Planted codes with no verdict: {r.drops.unreviewed_total}; by reason: "
            + (", ".join(f"{k} x{v}" for k, v in r.drops.unreviewed_by_reason.items()) or "-")
            + ". Reviewer is known only when a drop names the code or its group.", ""]
    out += ["## Root-cause links", "",
            f"- {r.links_family_consistent.consistent} of {r.links_family_consistent.links} links join two "
            f"codes of one family ({_pct(r.links_family_consistent.share)})", "", "## Strengths", "",
            f"- {r.strength_contradictions.contradictions} of {r.strength_contradictions.strengths} strengths "
            f"are on codes planted as growth areas ({_pct(r.strength_contradictions.rate)})", "",
            "## Unplanted growth areas the generator's check also reported", "",
            f"- {r.unplanted_confirmed.count} of {r.unplanted_confirmed.unplanted_on_failed} unplanted growth "
            "areas: "
            + (", ".join(f"{c} x{n}" for c, n in r.unplanted_confirmed.codes.items()) or "-"),
            "- This category comes only from fidelity-failed submissions (the generator records `extra` "
            "only for them); they are excluded from every other number."]
    uf = r.unplanted_flags
    out += ["", "### Most often flagged, not planted", "",
            f"- {uf.total} growth areas on scored submissions were reported but not planted, by concept "
            "(top 15 shown; the JSON has every concept):", ""]
    out += table(["Concept", "Flags", "Submissions"],
                 [[c.concept, str(c.count), str(c.submissions)] for c in uf.concepts[:15]])
    out += ["", "- By family (a code in several families counts toward each): "
            + (", ".join(f"{f.family} {f.count}" for f in uf.families) or "-")]
    c = r.cost
    out += ["", "## Cost", "",
            f"- Calls: {c.calls}; tokens: {c.input_tokens} in, {c.output_tokens} out "
            f"({c.output_tokens - c.thinking_tokens} answer, {c.thinking_tokens} thinking); USD: "
            + ("unknown" if c.usd is None else f"{c.usd:.4f}")
            + (f" (no price for {', '.join(c.unpriced_models)})" if c.unpriced_models else "")]
    if r.max_usd is not None:
        out.append(f"- Spend cap (max_usd): {r.max_usd:.4f}; priced spend {c.priced_usd:.4f}"
                   + ("; the run stopped at the cap" if r.stopped == "max_usd" else "")
                   + ("; models with no price are not counted toward the cap" if c.unpriced_models else ""))
    if r.usage_by_reviewer:
        out += ["", "### Tokens by reviewer", ""]
        out += table(["Reviewer", "Model", "Calls", "Input", "Output", "Thinking"],
                     [[k, u.model or "-", *map(str, (u.calls, u.input_tokens, u.output_tokens, u.thinking_tokens))]
                      for k, u in r.usage_by_reviewer.items()])
    if r.agreement:
        a = r.agreement
        out += ["", f"## Agreement across {a.runs} runs", "",
                f"- {a.unanimous} of {a.pairs} (submission, standard) pairs agree ({_pct(a.rate)})", ""]
        out += table(["Standard", "Agreement"], [[k, _pct(v)] for k, v in a.by_standard.items()])
    return "\n".join(out) + "\n"


def write_reports(r: EvalV2Report, reports_dir: Path) -> tuple[Path, Path]:
    """`evalv2-<stamp>.json` and `.md` (a name the MVP `eval-*.json` readers do not pick up)."""
    reports_dir = Path(reports_dir)
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / f"evalv2-{datetime.now().strftime('%Y%m%dT%H%M%S')}.json"
    path.write_text(r.model_dump_json(indent=2), encoding="utf-8")
    md = path.with_suffix(".md")
    md.write_text(format_report_markdown(r), encoding="utf-8")
    return path, md

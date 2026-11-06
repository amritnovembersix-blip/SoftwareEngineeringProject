"""Generate a v2 course's submissions: writing by LLM with a fidelity check, multiple choice by rule (lab only).

Reads the course folder plus its answer key (students.jsonl, trajectory.jsonl, written by build-course) and
writes `submissions/<file id>.jsonl`, `answer_key/expected/<file id>.jsonl` and `answer_key/fidelity.jsonl`.
See the synthetic-data v2 design spec, section 5. Errors and progress name ids only, never submission text.
"""
import hashlib
import json
import os
import random
import re
import tempfile
from collections.abc import Callable, Mapping
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import timedelta, timezone
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

from agentic.coursedata import AssignmentFile, CourseDir, ItemAnswer, SubmissionRow, due_at
from agentic.dataset import read_rows
from agentic.llm.base import ChatModel
from agentic_lab.answer_key import (
    ExpectedRow, FidelityRow, StandardLevel, StudentKey, TrajectoryRow, answer_key_path)
from agentic_lab.synth.evidence import choose_answers, concept_picks, evidenced, mc_fidelity
from agentic_lab.synth.families import SkillFamily, grade_key, load_families

ATTEMPTS = 3
_QUIRK_FALLBACK = "one standard writing convention, such as spelling, capitalization or punctuation"


@dataclass(frozen=True)
class _Band:
    name: str
    count: str  # how many mistakes the writer makes (also shown to the checker)
    minimum: str  # what the checker must see for `present`


# The one level -> (writer count, checker minimum) table; both prompts read it.
_BANDS = {
    1: _Band("Approaching", "1-2 clear mistakes; the other sentences where this skill applies are correct",
             "at least 1 clear mistake"),
    2: _Band("Developing", "several mistakes, in about half of the sentences where this skill applies",
             "at least 3 clear mistakes, or mistakes in about half of the sentences where this skill applies"),
    # Beginning: the writer aims above the checker's bar ("most" vs "about half"), never below its floor of 3.
    3: _Band("Beginning", "mistakes in most of the sentences where this skill applies (at least 3)",
             "mistakes in at least about half of the sentences where this skill applies (at least 3)"),
}
_QUIRK_BAND = _Band("quirk", "1-2 times in this assignment", "at least 1 clear sentence that fails to meet it")
_ACROSS_ANSWERS = "These counts apply across all answers together, not to each answer."
_SEPARATE = ("One sentence may hold mistakes of different kinds (for example an agreement error inside a run-on), "
             "but one mistake never counts toward two items.")

# Plain-words trouble area per skill family (skill_families.json).
_TROUBLE = {
    "agreement": "keeping subjects and verbs, pronouns, and verb tenses in agreement",
    "sentence_boundaries": "writing complete, correctly joined sentences (no fragments or run-ons)",
    "mechanics": "capitalization, apostrophes, quotation marks and spelling",
    "organization": "organizing the writing: a clear opening, a steady focus, transitions, and a conclusion",
    "evidence": "choosing evidence from the text that fits the claim and explaining how it supports it",
    "vocabulary": "choosing precise words and explaining what words and phrases mean in the text",
    "theme_central_idea": "stating and explaining a text's theme or central idea",
}

# Optional per-family detail that replaces the shared mistake definition (no level table of its own).
_MISTAKE = {
    "evidence": "A mistake is a claim with no evidence, or a quote that is only restated, loosely tied to the "
                "claim, or left unexplained.",
}

# One invented slip per family, about nothing in the curriculum. The writer sees it as illustration only.
_SLIP_EXAMPLES = {
    "agreement": "The players on the team was tired, so he walk home early.",
    "sentence_boundaries": "We waited for the bus. Because the rain was heavy and the stop had no roof.",
    "mechanics": "my brothers bike was parked by the library on main street.",
    "organization": "Gardening teaches patience. Also my cousin has a new phone. Tomatoes need sun.",
    "evidence": "The report says 'the pool stayed closed all summer.' This shows the pool was closed, so the "
                "council did not care about kids.",
    "vocabulary": "The museum trip was really good and stuff, and the guide called the room sterile, so it "
                  "was dirty.",
    "theme_central_idea": "The theme of the story is that a boy goes to the fair and buys a kite.",
}


class ParagraphOut(BaseModel):
    paragraphs: list[str] = Field(min_length=1, max_length=1)


class EssayOut2(BaseModel):
    paragraphs: list[str] = Field(min_length=4)


class AnswerText(BaseModel):
    item_id: str
    text: str


class ShortAnswerOut(BaseModel):
    answers: list[AnswerText]


class FamilyCheck(BaseModel):
    name: str
    present: bool


class FidelityOut(BaseModel):
    checks: list[FamilyCheck]
    extra_severe: list[str]


_WRITER_SCHEMA = {"paragraph": ParagraphOut, "essay": EssayOut2, "short_answer": ShortAnswerOut}


@dataclass
class GenerateCourseReport:
    written: int = 0  # new writing submissions (LLM)
    skipped: int = 0  # submissions already in the file (resume)
    failed_fidelity: int = 0  # new writing submissions kept after the last failed check
    mc: int = 0  # new multiple-choice submissions (by rule, no LLM)
    unusable: int = 0  # no usable writer reply after ATTEMPTS: no row written, a rerun retries them
    failed_mc: int = 0  # new multiple-choice submissions with a concept fidelity row (a quiz design error)
    # planted level (1-3) -> (pairs with >= 2 picks, pairs), over new (submission, concept) pairs
    mc_detect: dict[int, tuple[int, int]] = field(default_factory=dict)

    @property
    def pass_rate(self) -> float | None:
        """Share of new written submissions with no fidelity row (multiple choice is reported separately)."""
        return (self.written - self.failed_fidelity) / self.written if self.written else None

    def count_mc(self, stats: Mapping[str, tuple[int, int]]) -> None:
        """Add one multiple-choice submission's concept -> (planted, picks) to `mc_detect`."""
        for planted, picks in stats.values():
            if planted:
                hits, pairs = self.mc_detect.get(planted, (0, 0))
                self.mc_detect[planted] = (hits + (picks >= 2), pairs + 1)


# --- prompts -------------------------------------------------------------------------------------------------

def _grade_label(code: str) -> str:
    return code.split(".", 1)[0]


def _labels(codes: list[str]) -> str:
    return ", ".join(dict.fromkeys(_grade_label(c) for c in sorted(codes, key=grade_key)))


def _weak(family: SkillFamily, levels: Mapping[str, int], grade: int) -> list[str]:
    return [c for c in family.codes if grade_key(c) <= grade and levels.get(c, 0) > 0]


def _family_behavior(family: SkillFamily, levels: Mapping[str, int], standards: Mapping[str, str],
                     grade: int) -> list[str]:
    """The family as counted mistakes at the student's levels: the trouble area, secure grades, the grades
    with milder trouble, ONE count from the most severe band (`_BANDS`, as the checker scores), and two
    anchors framed as trouble, never as an instruction."""
    weak = _weak(family, levels, grade)
    trouble = _TROUBLE.get(family.name, family.name.replace("_", " "))
    mistake = _MISTAKE.get(family.name, "A mistake is a place where a sentence fails to do this correctly.")
    out = [f"Trouble with {trouble}. {mistake}",
           f"Secure through grade {min(grade_key(c) for c in weak) - 1}: simpler work at those grades is "
           "correct."]
    top = max(levels[c] for c in weak)  # one count per family, from the band the checker scores against
    lower = [c for c in weak if levels[c] < top]
    if lower:
        out.append(f"Milder trouble also at grade {_labels(lower)}.")
    band = _BANDS[top]
    out.append(f"At the grade {_labels([c for c in weak if levels[c] == top])} level ({band.name}): {band.count}.")
    anchors = [next((c for c in weak if 3 <= grade_key(c) <= 5), None),
               next((c for c in weak if grade_key(c) >= 9), None)]
    for code in anchors:
        if code and standards.get(code):
            out.append(f"You have trouble with (grade {_grade_label(code)}): {standards[code].rstrip('.')}.")
    return out


def _family_minimum(family: SkillFamily, levels: Mapping[str, int], grade: int) -> str:
    """The checker's bar for a family: the minimum of its most severe band."""
    return _BANDS[max(levels[c] for c in _weak(family, levels, grade))].minimum


def _quirk_text(code: str, standards: Mapping[str, str]) -> str:
    return (standards.get(code) or _QUIRK_FALLBACK).rstrip(".")


def _task(assignment: AssignmentFile) -> str:
    if assignment.type == "paragraph":
        return "Write exactly one paragraph."
    if assignment.type == "essay":
        return "Write an essay of at least four paragraphs. Return each paragraph as a list item."
    items = "\n".join(f"ITEM {i.id}: {i.prompt}" for i in assignment.items)
    return ("Answer each question in a few sentences. Return one answer per question, with the question's "
            f"item_id exactly as given.\n{items}")


_FRONT_MATTER = re.compile(r"---\n(.*?)\n---\n(.*)", re.S)


def split_text(raw: str, fallback_title: str) -> tuple[str, str]:
    """(title, body) of a curriculum text file: the front matter is dropped and its `title` kept."""
    m = _FRONT_MATTER.match(raw.replace("\r\n", "\n"))
    if not m:
        return fallback_title, raw.strip()
    title = fallback_title
    for line in m.group(1).splitlines():
        key, sep, value = line.partition(":")
        if sep and key.strip() == "title":
            title = value.strip().strip('"') or fallback_title
    return title, m.group(2).strip()


def build_writer_prompt(student_levels: Mapping[str, int], families: Mapping[str, SkillFamily],
                        quirks: list[str], assignment: AssignmentFile, texts: list[tuple[str, str]],
                        standards: Mapping[str, str], grade: int) -> tuple[str, str]:
    """`student_levels` are what this submission evidences; `families` and `quirks` are the active ones.
    `texts` are (title, excerpt) pairs: the assignment's `text_id` first, then each of `extra_text_ids`."""
    system = (f"You are a grade {grade} student completing an assignment for English class. "
              "Write the way a student at that grade really writes.")
    parts = [f"Assignment: {assignment.title}\n{assignment.prompt}"]
    parts += [f"Text: {title}\n{body.strip()}" for title, body in texts]
    parts.append(_task(assignment))
    if not families and not quirks:
        parts.append("Write clean, proficient work for your grade level, with no deliberate weaknesses.")
    else:
        lines = []
        for name, fam in families.items():
            lines.append(f"- {name.replace('_', ' ')}:")
            lines += [f"  {x}" for x in _family_behavior(fam, student_levels, standards, grade)]
            if name in _SLIP_EXAMPLES:
                lines.append(f'  Example of this kind of mistake (illustration only): "{_SLIP_EXAMPLES[name]}" '
                             "Do not reuse its words; make your own mistakes that fit your writing.")
        for code in quirks:
            lines.append(f"- {_QUIRK_BAND.count}, you write a sentence that fails to meet this skill: "
                         f"{_quirk_text(code, standards)}. Your other sentences meet this skill.")
        lines.append(_SEPARATE)
        if assignment.type == "short_answer":
            lines.append(_ACROSS_ANSWERS)
        parts.append("You tried to write well, but sometimes you have trouble with these parts of writing and "
                     "will make mistakes in your attempt to complete the assignment, as counted below:\n"
                     + "\n".join(lines))
    parts.append("Otherwise your writing is proficient for your grade. "
                 "Never mention standards and never point out or label your own flaws.")
    return system, "\n\n".join(parts)


_CHECK_PREFIX = re.compile(r"^check\b[\s:]*", re.I)


def _check_key(name: str) -> str:
    """A check name as the checker may echo it ("CHECK vocabulary:", "Vocabulary"): no CHECK prefix, no
    colons or outer spaces, lower case."""
    return _CHECK_PREFIX.sub("", name.strip()).strip(" :").lower()


def _submission_text(assignment: AssignmentFile, body: list[str] | list[ItemAnswer]) -> str:
    if assignment.type == "short_answer":
        return "\n\n".join(f"{a.item_id}: {a.text}" for a in body)
    return "\n\n".join(body)


def build_checker_prompt(student_levels: Mapping[str, int], families: Mapping[str, SkillFamily],
                         quirks: list[str], assignment: AssignmentFile, submission: str,
                         standards: Mapping[str, str], grade: int) -> tuple[str, str]:
    system = (
        "You check a synthetic grade-level student submission against the weaknesses it was meant to show. "
        "Return one check per CHECK line, with the name exactly as given: present is true only when you can "
        "see at least the number of clear mistakes that the line's \"Present if\" states. One sentence may hold "
        "mistakes of different kinds, but each mistake counts toward only one CHECK line: never use the same "
        "mistake for two lines. In extra_severe, "
        "list the Missouri Learning Standards codes of any other severe (Beginning-level) weakness you see "
        "that is not listed; return an empty list when there is none.")
    lines = []
    for name, fam in families.items():
        members = ", ".join(f"{c} ({_BANDS[student_levels[c]].name})" for c in _weak(fam, student_levels, grade))
        lines.append(f"CHECK {name}: " + " ".join(_family_behavior(fam, student_levels, standards, grade))
                     + f" Member standards: {members}."
                     + f" Present if: {_family_minimum(fam, student_levels, grade)}.")
    for code in quirks:
        lines.append(f"CHECK {code}: {_QUIRK_BAND.count}, a sentence fails to meet this skill: "
                     f"{_quirk_text(code, standards)} ({_BANDS[student_levels[code]].name}, quirk). "
                     f"Present if: {_QUIRK_BAND.minimum}.")
    if lines and assignment.type == "short_answer":
        lines.append(_ACROSS_ANSWERS)
    planned = "\n".join(lines) or "(none: the submission should be proficient)"
    user = (f"Grade {grade} assignment ({assignment.type}): {assignment.title}\n{assignment.prompt}\n\n"
            f"Submission:\n{submission}\n\nIntended weaknesses:\n{planned}")
    return system, user


# --- generation ----------------------------------------------------------------------------------------------

def _rng(seed: int, *parts: str) -> random.Random:
    digest = hashlib.sha256(":".join([str(seed), *parts]).encode()).digest()
    return random.Random(int.from_bytes(digest[:8], "big"))


def submission_id(arm: str, file_id: str, student_hash: str) -> str:
    return f"sub-{arm}-{file_id}-{student_hash[:8]}"


def _replace(path: Path, text: str) -> None:
    """Write via a temp file in the same folder, then replace (no half-written answer-key files)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def read_standard_texts(path: Path) -> dict[str, str]:
    """code -> sub-expectation text (the description when a code has none), from a standards xlsx."""
    from agentic.standards.loader import normalize_rows, read_xlsx
    return {r.code: r.sub_expectation or r.description for r in normalize_rows(read_xlsx(path))}


def _line(row: SubmissionRow) -> str:
    """Like the MVP generator's `_line`, but without null fields: exactly one of paragraphs/answers appears."""
    d = row.model_dump(mode="json", exclude_none=True)
    d["submitted_at"] = row.submitted_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return json.dumps(d, ensure_ascii=False) + "\n"


def _valid_body(assignment: AssignmentFile, out: BaseModel) -> list[str] | list[ItemAnswer] | None:
    """The body to store, or None when the response doesn't fit the assignment (a failed attempt)."""
    if assignment.type != "short_answer":
        return list(out.paragraphs)
    ids = [i.id for i in assignment.items]
    got = {a.item_id: a.text for a in out.answers}
    if len(got) != len(out.answers) or set(got) != set(ids):
        return None
    return [ItemAnswer(item_id=i, text=got[i]) for i in ids]


def planned_checks(student: StudentKey, ev: Mapping[str, int],
                   families: Mapping[str, SkillFamily]) -> tuple[dict[str, SkillFamily], list[str]]:
    """(active families, quirk codes) a submission evidencing `ev` is checked for: the student's families with
    a member in `ev`, then its quirks in `ev`. The expected check names are `list(active) + quirks`."""
    active = {e.family: families[e.family] for e in student.families
              if any(c in ev for c in families[e.family].codes)}
    quirks = [q.standard for q in student.quirks if q.standard in ev]
    return active, quirks


def fidelity_verdict(check: FidelityOut, expected: list[str],
                     levels: Mapping[str, int]) -> tuple[list[str], list[str]]:
    """(missing, extra) for a checker reply: expected names not reported present (names compared via
    `_check_key`), and extra_severe codes, stripped and de-duplicated, that aren't blank or any planted code
    in `levels` (case-insensitive)."""
    present = {_check_key(c.name) for c in check.checks if c.present}
    missing = [n for n in expected if _check_key(n) not in present]
    planted = {c.lower() for c in levels}  # the checker may echo a code in another case
    extra = list(dict.fromkeys(x for x in (c.strip() for c in check.extra_severe)
                               if x and x.lower() not in planted))
    return missing, extra


def _write_one(chat: ChatModel, assignment: AssignmentFile, student: StudentKey, levels: dict[str, int],
               ev: dict[str, int], families: Mapping[str, SkillFamily], texts: list[tuple[str, str]],
               standards: Mapping[str, str], grade: int, sub_id: str):
    """Up to ATTEMPTS writer + checker rounds. Returns (body, fidelity row or None when it passed);
    body is None when no writer reply was usable (the caller writes nothing for it)."""
    active, quirks = planned_checks(student, ev, families)
    expected = list(active) + quirks
    writer = build_writer_prompt(ev, active, quirks, assignment, texts, standards, grade)
    body = failure = None
    for _ in range(ATTEMPTS):
        try:
            out = chat.structured(*writer, _WRITER_SCHEMA[assignment.type])
        except ValidationError:
            continue  # the message quotes the response, so it is dropped
        candidate = _valid_body(assignment, out)
        if candidate is None:
            continue
        body = candidate
        try:
            check = chat.structured(*build_checker_prompt(ev, active, quirks, assignment,
                                                          _submission_text(assignment, body), standards, grade),
                                    FidelityOut)
        except ValidationError:  # a malformed check is a failed attempt; every expected check counts as missing
            failure = FidelityRow(submission_id=sub_id, missing=expected, extra=[])
            continue
        missing, extra = fidelity_verdict(check, expected, levels)
        if not missing and not extra:
            return body, None
        failure = FidelityRow(submission_id=sub_id, missing=missing, extra=extra)
    return body, failure if body is not None else None


@dataclass
class _Task:
    """One (assignment, student) submission still to write; `future` runs `_write_one` (writing only).
    Multiple choice has no future: its answers are in `row`, with `failure` and `concepts` decided by rule."""
    student_hash: str
    sub_id: str
    row: dict
    future: Future | None = None
    failure: FidelityRow | None = None
    concepts: dict[str, tuple[int, int]] = field(default_factory=dict)  # concept -> (planted, picks)


@dataclass
class _Plan:
    assignment: AssignmentFile
    strand: str
    tasks: list[_Task | str]  # a str is a submission id already in the file (skipped)


def generate_course(chat: ChatModel | None, data_dir: Path, version: str, seed: int,
                    through_week: int | None = None, students: int | None = None, overwrite: bool = False,
                    progress: Callable[[str], None] | None = None,
                    standards: Mapping[str, str] | None = None, workers: int = 1,
                    mc_only: bool = False) -> GenerateCourseReport:
    """Write submissions for assignments due by `through_week`, for the first `students` roster students.

    `mc_only` plans multiple-choice assignments only: no chat calls (`chat` may be None), and every writing
    assignment's submissions, `expected/` rows and fidelity rows are left as they are (byte-identical).

    Resumable: rows already in a submissions file are skipped unless `overwrite`. After each assignment,
    `expected/<file id>.jsonl` is rewritten from the plan for every row in the file; `fidelity.jsonl` is kept
    as one row per failed submission id.

    Writing tasks run on `workers` threads that share `chat` (and so its one RPM limiter). All tasks are
    queued up front; results are written in assignment, then roster order, so the files don't depend on
    `workers`. Only this thread touches the files and the report. The default here is 1; the CLI's default
    comes from SYNTH_WORKERS (8 when unset)."""
    if workers < 1:
        raise ValueError("workers must be at least 1")
    if standards is None:
        from agentic_lab.standards_v2 import REVIEWABLE_XLSX
        standards = read_standard_texts(REVIEWABLE_XLSX)
    data_dir = Path(data_dir)
    cd = CourseDir(data_dir, version)
    course, curriculum = cd.course(), cd.curriculum()
    families = load_families(data_dir / "skill_families.json")
    key_dir = answer_key_path(data_dir, version)
    keys = {k.student_hash: k for k in read_rows(key_dir / "students.jsonl", StudentKey)}
    trajectory: dict[tuple[str, int], dict[str, int]] = {}
    for t in read_rows(key_dir / "trajectory.jsonl", TrajectoryRow):
        trajectory.setdefault((t.student_hash, t.week), {})[t.standard] = t.level
    strands = {m.id: m.strand for m in curriculum.modules}
    roster = [r.student_hash for r in cd.roster()]
    roster = roster[:students] if students is not None else roster
    in_run = set(roster)
    fid_path = key_dir / "fidelity.jsonl"
    fidelity = {r.submission_id: r for r in read_rows(fid_path, FidelityRow)} if fid_path.exists() else {}

    def save_fidelity() -> None:
        _replace(fid_path, "".join(fidelity[k].model_dump_json() + "\n" for k in sorted(fidelity)))

    def plan(assignment: AssignmentFile, pool: ThreadPoolExecutor) -> _Plan:
        """The assignment's rows to write, in roster order, with each writing task queued on `pool`."""
        due = due_at(course.start_date, assignment.due_offset)
        strand = strands[assignment.module]
        texts = [] if assignment.type == "multiple_choice" else [
            split_text(cd.text(t), t) for t in [assignment.text_id, *assignment.extra_text_ids]]
        existing = set() if overwrite else {r.submission_id for r in cd.submissions(assignment.id)}
        tasks: list[_Task | str] = []
        for h in roster:
            if assignment.assigned_to is not None and h not in assignment.assigned_to:
                continue
            sub_id = submission_id(course.arm, assignment.id, h)
            if sub_id in existing:
                tasks.append(sub_id)
                continue
            levels = trajectory.get((h, assignment.week), {})
            hours = _rng(seed, sub_id, "submitted_at").uniform(1, 48)
            submitted = (due - timedelta(hours=hours)).replace(microsecond=0)
            task = _Task(h, sub_id, dict(student_hash=h, submission_id=sub_id, attempt=1,
                                         assignment_id=assignment.id, course_id=course.id,
                                         submitted_at=submitted))
            if assignment.type == "multiple_choice":
                answers = choose_answers(assignment, levels, _rng(seed, sub_id))
                task.row["answers"] = answers
                task.failure = mc_fidelity(assignment, levels, answers, sub_id)
                task.concepts = concept_picks(assignment, levels, answers)
            else:
                task.future = pool.submit(_write_one, chat, assignment, keys[h], levels,
                                          evidenced(assignment, strand, levels), families, texts, standards,
                                          course.grade, sub_id)
            tasks.append(task)
        return _Plan(assignment, strand, tasks)

    def write(p: _Plan) -> None:
        """Write one assignment's rows (waiting on each writing task in turn), then its answer-key files."""
        assignment = p.assignment
        out_path = cd.path / "submissions" / f"{assignment.id}.jsonl"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        pending: dict[str, FidelityRow | None] = {}  # overwrite: applied once the new file replaces the old
        tmp_name = None
        if overwrite:
            fd, tmp_name = tempfile.mkstemp(dir=out_path.parent, suffix=".tmp")
            fh = os.fdopen(fd, "w", encoding="utf-8", newline="\n")
            for old in cd.submissions(assignment.id):  # students outside this run's slice keep their rows
                if old.student_hash not in in_run:
                    fh.write(_line(old))
        else:
            fh = out_path.open("a", encoding="utf-8", newline="\n")
        try:
            with fh:
                for task in p.tasks:
                    if isinstance(task, str):
                        report.skipped += 1
                        continue
                    h, sub_id, row = task.student_hash, task.sub_id, task.row
                    if task.future is None:
                        failure = task.failure
                        report.mc += 1
                        report.failed_mc += failure is not None
                        report.count_mc(task.concepts)
                    else:
                        body, failure = task.future.result()  # re-raises the task's exception here
                        if body is None:
                            report.unusable += 1
                            if progress:
                                progress(f"unusable {sub_id} ({h[:8]})")
                            continue
                        row["paragraphs" if assignment.type != "short_answer" else "answers"] = body
                        report.written += 1
                        report.failed_fidelity += failure is not None
                    line = _line(SubmissionRow(**row))
                    if overwrite:
                        pending[sub_id] = failure
                    else:
                        # fidelity first: a crash in between leaves an orphan row, pruned below on rerun
                        fidelity.pop(sub_id, None)
                        if failure:
                            fidelity[sub_id] = failure
                        save_fidelity()
                    fh.write(line)
                    fh.flush()
                    if progress:
                        progress(f"{assignment.id} {h[:8]}" + (" (fidelity failed)" if failure else ""))
            if tmp_name:
                os.replace(tmp_name, out_path)
                tmp_name = None
        finally:
            if tmp_name and os.path.exists(tmp_name):
                os.unlink(tmp_name)
        rows = cd.submissions(assignment.id)
        prefix = submission_id(course.arm, assignment.id, "")
        ids = {r.submission_id for r in rows}
        for sub_id in [k for k in fidelity if k.startswith(prefix) and k not in ids]:
            del fidelity[sub_id]  # e.g. rows dropped by a smaller --overwrite run
        for sub_id, failure in pending.items():
            fidelity.pop(sub_id, None)
            if failure:
                fidelity[sub_id] = failure
        save_fidelity()
        _replace(key_dir / "expected" / f"{assignment.id}.jsonl", "".join(
            ExpectedRow(student_hash=r.student_hash, submission_id=r.submission_id,
                        weaknesses=[StandardLevel(standard=c, level=lv) for c, lv in sorted(evidenced(
                            assignment, p.strand, trajectory.get((r.student_hash, assignment.week), {})).items())]
                        ).model_dump_json() + "\n" for r in rows))

    report = GenerateCourseReport()
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="generate-course") as pool:
        try:
            plans = [plan(a, pool) for a in cd.assignments()
                     if (through_week is None or a.due_offset.week <= through_week)
                     and (not mc_only or a.type == "multiple_choice")]
            for p in plans:
                write(p)
        except BaseException:
            pool.shutdown(wait=True, cancel_futures=True)  # queued tasks never start; running ones finish
            raise
    return report

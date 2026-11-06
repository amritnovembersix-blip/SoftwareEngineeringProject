import json
import shutil
from pathlib import Path

import pytest

from agentic.coursedata import AssignmentFile, CourseDir, due_at
from agentic.llm.fake import FakeChatModel
from agentic_lab.answer_key import ExpectedRow, FidelityRow, StudentKey, answer_key_path
from agentic_lab.config import SYNTHETIC_DIR
from agentic_lab.synth.evidence import evidenced
from agentic_lab.synth.families import load_families
from agentic_lab.synth.generate_course import (
    EssayOut2, FamilyCheck, FidelityOut, ParagraphOut, ShortAnswerOut, build_checker_prompt, build_writer_prompt,
    generate_course)
from agentic_lab.synth.students import CoursePlan, build_course, levels_at

FIXTURE = Path(__file__).resolve().parents[1] / "data" / "coursedir"
FAMILIES = load_families(SYNTHETIC_DIR / "skill_families.json")
WEAK = "Strong reader, weak conventions"
SHALLOW = "Fluent writer, shallow analysis"
CTRL, IV = "0.95-control", "0.95-intervention"
STANDARDS = {"3.L.1.A.e": "Form and use regular and irregular verbs.",
             "9-10.W.3.A.c": "Edit for sentence formation and agreement.",
             "9-10.RL.1.A": "Cite the strongest textual evidence."}


def _plan() -> CoursePlan:
    return CoursePlan.model_validate({
        "curriculum": "ela9-fixture", "version": "0.95", "start_date": "2027-01-11", "teacher_id": "teacher-test",
        "courses": {"control": {"name": "Test 1", "period": 1}, "intervention": {"name": "Test 2", "period": 2}},
        "profiles": [
            {"name": "On track", "count": 1, "families": [], "quirks": [1]},
            {"name": WEAK, "count": 1, "quirks": [0], "jitter": False,
             "families": [{"family": "agreement", "secure_through": 2, "at_grade_level": 3}]},
            {"name": SHALLOW, "count": 1, "quirks": [0], "jitter": False,
             "families": [{"family": "evidence", "secure_through": 7, "at_grade_level": 2}]},
        ],
        "interventions": [
            {"id": "iv2", "week": 2, "kind": "individual", "profile": WEAK, "families": ["agreement"], "rate": 1,
             "description": "Conferences.",
             "assignment": {"id": "ela9v2-i01", "module": "rl", "week": 2, "kind": "intervention",
                            "type": "paragraph", "title": "Revise", "prompt": "Revise your paragraph.",
                            "text_id": "fixture-story", "due_offset": {"week": 2, "day": "fri"}}}],
    })


@pytest.fixture
def data(tmp_path) -> Path:
    shutil.copytree(FIXTURE, tmp_path, dirs_exist_ok=True)
    shutil.copy(SYNTHETIC_DIR / "skill_families.json", tmp_path / "skill_families.json")
    build_course(_plan(), tmp_path, 2027)
    return tmp_path


def _keys(data, version) -> list[StudentKey]:
    path = answer_key_path(data, version) / "students.jsonl"
    return [StudentKey.model_validate_json(x) for x in path.read_text(encoding="utf-8").splitlines()]


def _rows(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def _subs(data, version, file_id):
    return _rows(data / "courses" / version / "submissions" / f"{file_id}.jsonl")


def _fidelity(data, version):
    return [FidelityRow.model_validate(r) for r in _rows(answer_key_path(data, version) / "fidelity.jsonl")]


def _names(user: str) -> list[str]:
    return [line.split()[1].rstrip(":") for line in user.splitlines() if line.startswith("CHECK ")]


def _items(user: str) -> list[str]:
    return [line.split()[1].rstrip(":") for line in user.splitlines() if line.startswith("ITEM ")]


def _chat(checker=None, writer=None):
    """Writers return placeholder text; the checker passes every check unless `checker` says otherwise."""
    def para(_s, _u):
        if writer:
            writer()
        return ParagraphOut(paragraphs=["A placeholder paragraph."])

    def short(_s, u):
        return ShortAnswerOut(answers=[{"item_id": i, "text": "A placeholder answer."} for i in _items(u)])

    def check(s, u):
        if checker:
            return checker(s, u)
        return FidelityOut(checks=[FamilyCheck(name=n, present=True) for n in _names(u)], extra_severe=[])

    return FakeChatModel({ParagraphOut: para, ShortAnswerOut: short, FidelityOut: check,
                          EssayOut2: lambda s, u: EssayOut2(paragraphs=["p"] * 4)})


def _run(chat, data, version=CTRL, **kw):
    kw = {"seed": 2027, "through_week": 1, "students": None, "overwrite": False, "standards": STANDARDS} | kw
    return generate_course(chat, data, version, **kw)


def _schemas(chat):
    return [c[0] for c in chat.calls]


def test_mc_rows_written_without_chat_calls(data):
    chat = _chat()
    report = _run(chat, data)
    a01 = _subs(data, CTRL, "ela9v2-a01")
    assert len(a01) == 3 and report.mc == 3
    assert all(r["answers"][0]["choice"] in ("A", "B") and "paragraphs" not in r for r in a01)
    # every chat call is for the paragraph (a02): one writer and one checker per student
    assert _schemas(chat) == [ParagraphOut, FidelityOut] * 3
    assert all("ela9v2-a01" not in u and "Fixture multiple choice" not in u for _, _, u in chat.calls)


def test_submission_ids_course_and_submitted_at(data):
    _run(_chat(), data)
    cd = CourseDir(data, CTRL)
    a02 = cd.assignment("ela9v2-a02")
    due = due_at(cd.course().start_date, a02.due_offset)
    for key, row in zip(_keys(data, CTRL), cd.submissions("ela9v2-a02")):
        assert row.submission_id == f"sub-control-ela9v2-a02-{key.student_hash[:8]}"
        assert row.course_id == "classroom_0.95-control" and row.assignment_id == "ela9v2-a02"
        assert 3600 <= (due - row.submitted_at).total_seconds() <= 48 * 3600
    raw = (cd.path / "submissions" / "ela9v2-a02.jsonl").read_bytes()
    assert b"\r\n" not in raw and b'"submitted_at": "2027-01-' in raw


def test_checker_fails_twice_then_passes(data):
    n = {"calls": 0}

    def checker(_s, u):
        n["calls"] += 1
        return FidelityOut(checks=[FamilyCheck(name=x, present=n["calls"] > 2) for x in _names(u)],
                           extra_severe=[])

    chat = _chat(checker=checker)
    report = _run(chat, data, students=2)  # p02 has the agreement family, so its checks are non-empty
    writers = [c for c in chat.calls if c[0] is ParagraphOut]
    # p01 (one quirk): 3 attempts until the checker passes; p02: passes first time
    assert len(writers) == 4
    assert report.failed_fidelity == 0 and _fidelity(data, CTRL) == []


def test_checker_always_fails_writes_fidelity_row(data):
    def checker(_s, u):
        return FidelityOut(checks=[FamilyCheck(name=x, present=False) for x in _names(u)], extra_severe=[])

    chat = _chat(checker=checker)
    report = _run(chat, data, students=3)
    assert _schemas(chat).count(ParagraphOut) == 9
    rows = {r.submission_id: r for r in _fidelity(data, CTRL)}
    keys = _keys(data, CTRL)
    weak = rows[f"sub-control-ela9v2-a02-{keys[1].student_hash[:8]}"]
    shallow = rows[f"sub-control-ela9v2-a02-{keys[2].student_hash[:8]}"]
    on_track = rows[f"sub-control-ela9v2-a02-{keys[0].student_hash[:8]}"]
    assert weak.missing == ["agreement"] and weak.extra == []
    assert shallow.missing == ["evidence"]
    assert on_track.missing == [q.standard for q in keys[0].quirks]
    assert report.failed_fidelity == 3 and len(_subs(data, CTRL, "ela9v2-a02")) == 3


def test_unplanned_severe_weakness_fails_planned_one_passes(data):
    def checker(_s, u):
        return FidelityOut(checks=[FamilyCheck(name=x, present=True) for x in _names(u)],
                           extra_severe=["9-10.W.3.A.c", "9-10.W.2.A"])

    _run(_chat(checker=checker), data, students=2)
    rows = {r.submission_id: r for r in _fidelity(data, CTRL)}
    keys = _keys(data, CTRL)
    # p02 has 9-10.W.3.A.c planned (agreement), so only 9-10.W.2.A is extra; p01 has neither planned
    assert rows[f"sub-control-ela9v2-a02-{keys[1].student_hash[:8]}"].extra == ["9-10.W.2.A"]
    assert rows[f"sub-control-ela9v2-a02-{keys[0].student_hash[:8]}"].extra == ["9-10.W.3.A.c", "9-10.W.2.A"]


def test_planted_code_echoed_in_another_case_is_not_extra(data):
    def checker(_s, u):
        return FidelityOut(checks=[FamilyCheck(name=x, present=True) for x in _names(u)],
                           extra_severe=["9-10.w.3.a.C", " 9-10.W.2.a "])

    _run(_chat(checker=checker), data, students=2)
    rows = {r.submission_id: r for r in _fidelity(data, CTRL)}
    keys = _keys(data, CTRL)
    # p02 has 9-10.W.3.A.c planned: its lower-case echo is not extra; the unplanned code is kept as reported
    assert rows[f"sub-control-ela9v2-a02-{keys[1].student_hash[:8]}"].extra == ["9-10.W.2.a"]


def test_resume_after_crash_has_no_duplicates(data):
    n = {"writer": 0}

    def crash():
        n["writer"] += 1
        if n["writer"] == 7:  # third student's first attempt (3 attempts each)
            raise RuntimeError("boom")

    def failing(_s, u):
        return FidelityOut(checks=[FamilyCheck(name=x, present=False) for x in _names(u)], extra_severe=[])

    with pytest.raises(RuntimeError):
        _run(_chat(checker=failing, writer=crash), data, students=3)
    assert len(_subs(data, CTRL, "ela9v2-a02")) == 2 and len(_fidelity(data, CTRL)) == 2
    report = _run(_chat(checker=failing), data, students=3)
    assert report.skipped == 5 and report.written == 1 and report.mc == 0
    for file_id in ("ela9v2-a01", "ela9v2-a02"):
        ids = [r["submission_id"] for r in _subs(data, CTRL, file_id)]
        assert len(ids) == len(set(ids)) == 3
        expected = _rows(answer_key_path(data, CTRL) / "expected" / f"{file_id}.jsonl")
        assert [r["submission_id"] for r in expected] == ids
    fid = [r.submission_id for r in _fidelity(data, CTRL)]
    assert len(fid) == len(set(fid)) == 3


def test_overwrite_regenerates_without_duplicates(data):
    def failing(_s, u):
        return FidelityOut(checks=[FamilyCheck(name=x, present=False) for x in _names(u)], extra_severe=[])

    _run(_chat(checker=failing), data)
    first = _subs(data, CTRL, "ela9v2-a01")
    report = _run(_chat(), data, overwrite=True)
    assert report.written == 3 and report.skipped == 0
    assert _subs(data, CTRL, "ela9v2-a01") == first  # MC answers and timestamps are seeded
    assert len(_subs(data, CTRL, "ela9v2-a02")) == 3
    assert _fidelity(data, CTRL) == []  # the regenerated submissions passed
    assert not list((data / "courses" / CTRL / "submissions").glob("*.tmp"))


def test_expected_for_rl_paragraph_matches_evidenced(data):
    _run(_chat(), data)
    cd = CourseDir(data, CTRL)
    a02 = cd.assignment("ela9v2-a02")
    rows = [ExpectedRow.model_validate(r)
            for r in _rows(answer_key_path(data, CTRL) / "expected" / "ela9v2-a02.jsonl")]
    keys = {k.student_hash: k for k in _keys(data, CTRL)}
    assert len(rows) == 3
    for row in rows:
        levels = levels_at(keys[row.student_hash], a02.week, [], FAMILIES, 9)
        want = evidenced(a02, "RL", levels)
        assert {w.standard: w.level for w in row.weaknesses} == want
        assert [w.standard for w in row.weaknesses] == sorted(want)
    shallow = next(r for r in rows if keys[r.student_hash].profile == SHALLOW)
    assert {w.standard for w in shallow.weaknesses} == {"8.RL.1.A", "9-10.RL.1.A"}  # RI members excluded


def test_students_and_through_week_limit(data):
    _run(_chat(), data, students=2, through_week=1)
    subs = data / "courses" / CTRL / "submissions"
    assert sorted(p.name for p in subs.glob("*.jsonl")) == ["ela9v2-a01.jsonl", "ela9v2-a02.jsonl"]
    hashes = [k.student_hash for k in _keys(data, CTRL)[:2]]
    for file_id in ("ela9v2-a01", "ela9v2-a02"):
        assert [r["student_hash"] for r in _subs(data, CTRL, file_id)] == hashes
    assert sorted(p.name for p in (answer_key_path(data, CTRL) / "expected").glob("*")) == [
        "ela9v2-a01.jsonl", "ela9v2-a02.jsonl"]


def test_short_answer_wrong_item_id_is_a_failed_attempt(data):
    n = {"calls": 0}

    def short(_s, u):
        n["calls"] += 1
        ids = _items(u)
        if n["calls"] == 1:
            ids = ["q1", "q9"]
        return ShortAnswerOut(answers=[{"item_id": i, "text": "An answer."} for i in ids])

    chat = _chat()
    chat._handlers[ShortAnswerOut] = short
    _run(chat, data, students=1, through_week=2)
    schemas = _schemas(chat)
    assert schemas.count(ShortAnswerOut) == 2
    assert schemas[-3:] == [ShortAnswerOut, ShortAnswerOut, FidelityOut]  # no checker for the bad attempt
    rows = _subs(data, CTRL, "ela9v2-a03")
    assert [a["item_id"] for a in rows[0]["answers"]] == ["q1", "q2"]
    assert _fidelity(data, CTRL) == []


def test_intervention_assignment_only_for_assigned_students(data):
    _run(_chat(), data, version=IV, through_week=2)
    keys = _keys(data, IV)
    rows = _subs(data, IV, "ela9v2-i01")
    assert [r["student_hash"] for r in rows] == [k.student_hash for k in keys if k.profile == WEAK]
    assert rows[0]["submission_id"].startswith("sub-intervention-ela9v2-i01-")
    assert _subs(data, CTRL, "ela9v2-i01") == []


def test_writer_prompt_describes_families_and_quirks():
    a = AssignmentFile.model_validate(json.loads(
        (FIXTURE / "curricula" / "ela9-fixture" / "assignments" / "ela9v2-a02.json").read_text("utf-8")))
    levels = {"3.L.1.A.e": 1, "4.L.1.A.e": 1, "6.W.3.A.c": 2, "9-10.W.3.A.c": 3, "5.L.3.A.a": 1}
    system, user = build_writer_prompt(
        levels, {"agreement": FAMILIES["agreement"]}, ["5.L.3.A.a"], a, [("The Story", "THE STORY TEXT")],
        STANDARDS, grade=9)
    assert "grade 9" in system
    assert "Never mention standards" in user
    assert "Text: The Story\nTHE STORY TEXT" in user and "Write one paragraph about the story." in user
    assert "Secure through grade 2" in user
    assert "Form and use regular and irregular verbs." in user  # lowest weak grade 3-5 anchor
    assert "Edit for sentence formation and agreement." in user  # 9-10 anchor
    assert "Milder trouble also at grade 3, 4, 6." in user
    assert "most of the sentences where this skill applies" in user
    assert "9-10.W.3.A.c" not in user and "3.L.1.A.e" not in user  # no codes
    assert "5.L.3.A.a" not in user  # quirk given by its text (falls back to a generic line)
    csys, cuser = build_checker_prompt(levels, {"agreement": FAMILIES["agreement"]}, ["5.L.3.A.a"], a,
                                       "SUBMITTED TEXT", STANDARDS, grade=9)
    assert _names(cuser) == ["agreement", "5.L.3.A.a"]
    assert "SUBMITTED TEXT" in cuser and "Beginning" in cuser


def _assignment(kind: str = "paragraph") -> AssignmentFile:
    a = json.loads((FIXTURE / "curricula" / "ela9-fixture" / "assignments" / "ela9v2-a02.json").read_text("utf-8"))
    if kind == "short_answer":
        a |= {"type": "short_answer", "items": [{"id": "q1", "prompt": "Why?", "standard": "9-10.RL.1.A"}]}
    return AssignmentFile.model_validate(a)


def _framed(user: str, text: str) -> bool:
    """Every line carrying the standard text frames it as trouble or failure, never as a bare instruction."""
    lines = [x for x in user.splitlines() if text in x]
    return bool(lines) and all("trouble with" in x or "fails to" in x for x in lines)


def test_writer_quirk_is_a_counted_mistake():
    _s, user = build_writer_prompt({"3.L.1.A.e": 1}, {}, ["3.L.1.A.e"], _assignment(), [("T", "X")],
                                   STANDARDS, grade=9)
    quirk = next(x for x in user.splitlines() if "Form and use regular and irregular verbs." in x)
    assert "1-2" in quirk and "fails to" in quirk
    assert _framed(user, "Form and use regular and irregular verbs.")
    assert "Occasionally" not in user
    assert "tried to write well" in user
    assert "One sentence may hold mistakes of different kinds" in user  # e.g. agreement inside a run-on
    assert "one mistake never counts toward two items" in user
    assert "separate sentence" not in user  # the old rule made two Beginning families infeasible
    assert "Your other sentences meet this skill." in user and "are fine" not in user


_COUNTS = {1: ("1-2 clear mistakes", "at least 1 clear mistake"),
           2: ("about half of the sentences where this skill applies", "at least 3 clear mistakes"),
           3: ("most of the sentences where this skill applies",
               "mistakes in at least about half of the sentences where this skill applies (at least 3)")}


@pytest.mark.parametrize("level", [1, 2, 3])
def test_family_level_counts_in_writer_and_checker(level):
    levels = {"9-10.W.3.A.c": level}
    fam = {"agreement": FAMILIES["agreement"]}
    _s, user = build_writer_prompt(levels, fam, [], _assignment(), [("T", "X")], STANDARDS, grade=9)
    csys, cuser = build_checker_prompt(levels, fam, [], _assignment(), "SUB", STANDARDS, grade=9)
    count, minimum = _COUNTS[level]
    assert count in user and count in cuser
    if level == 3:  # the writer aims above the checker's bar, never below its floor
        assert "most of the sentences where this skill applies (at least 3)." in user
    assert minimum in cuser
    assert _framed(user, "Edit for sentence formation and agreement.")
    # the family is one CHECK line, so assert the exact anchor sentence rather than the whole line
    assert cuser.count("Edit for sentence formation and agreement.") == 1
    assert "You have trouble with (grade 9-10): Edit for sentence formation and agreement." in cuser


def test_mixed_level_family_gets_one_count_matching_the_checker():
    """Approaching at grade 3-5, Developing at 6-8, Beginning at 9-10: one count, from the most severe band."""
    levels = {"3.L.1.A.e": 1, "4.L.1.A.e": 1, "6.W.3.A.c": 2, "9-10.W.3.A.c": 3}
    fam = {"agreement": FAMILIES["agreement"]}
    _s, user = build_writer_prompt(levels, fam, [], _assignment(), [("T", "X")], STANDARDS, grade=9)
    _c, cuser = build_checker_prompt(levels, fam, [], _assignment(), "SUB", STANDARDS, grade=9)
    for prompt in (user, cuser):
        counts = prompt.split("Present if: ", 1)[0]  # the checker's bar is not a writer count
        assert [count in counts for count, _m in _COUNTS.values()] == [False, False, True]
        assert "are correct" not in prompt
        assert "Milder trouble also at grade 3, 4, 6." in prompt
        assert "trouble starts" not in prompt
        # a mistake is a place, so one sentence can hold mistakes of different kinds
        assert "A mistake is a place where a sentence fails to do this correctly." in prompt
        assert "A mistake is a sentence" not in prompt
    assert _COUNTS[3][1] in cuser.split("Present if: ", 1)[1]  # the checker bar is the same (Beginning) band


def test_checker_quirk_minimum_and_no_vague_severity():
    csys, cuser = build_checker_prompt({"3.L.1.A.e": 1}, {}, ["3.L.1.A.e"], _assignment(), "SUB", STANDARDS,
                                       grade=9)
    assert "roughly the intended severity" not in csys
    assert "One sentence may hold mistakes of different kinds" in csys
    assert "each mistake counts toward only one CHECK line" in csys
    assert "extra_severe" in csys and "Beginning-level" in csys
    check = next(x for x in cuser.splitlines() if x.startswith("CHECK 3.L.1.A.e"))
    assert "1-2" in check and "at least 1 clear" in check and "fails to" in check


def test_every_family_has_a_slip_example_and_writer_says_not_to_copy():
    from agentic_lab.synth.generate_course import _SLIP_EXAMPLES
    assert set(_SLIP_EXAMPLES) == set(FAMILIES)
    assert all(_SLIP_EXAMPLES.values())
    assert "sterile" in _SLIP_EXAMPLES["vocabulary"]  # also misreads a word, the second half of the trouble
    for name, fam in FAMILIES.items():
        code = next(c for c in fam.codes if c.startswith("9-10"))
        _s, user = build_writer_prompt({code: 2}, {name: fam}, [], _assignment(), [("T", "X")], STANDARDS, grade=9)
        assert _SLIP_EXAMPLES[name] in user
        assert "illustration only" in user and "Do not reuse its words" in user
    _s, quirk_only = build_writer_prompt({"3.L.1.A.e": 1}, {}, ["3.L.1.A.e"], _assignment(), [("T", "X")],
                                         STANDARDS, grade=9)
    assert "illustration only" not in quirk_only  # quirks get no example


def test_short_answer_counts_apply_across_answers():
    levels = {"9-10.W.3.A.c": 2}
    fam = {"agreement": FAMILIES["agreement"]}
    _s, user = build_writer_prompt(levels, fam, [], _assignment("short_answer"), [("T", "X")], STANDARDS, grade=9)
    _c, cuser = build_checker_prompt(levels, fam, [], _assignment("short_answer"), "SUB", STANDARDS, grade=9)
    assert "across all answers together" in user and "across all answers together" in cuser
    _s, para = build_writer_prompt(levels, fam, [], _assignment(), [("T", "X")], STANDARDS, grade=9)
    assert "across all answers together" not in para


def test_proficient_writer_prompt_is_clean_work():
    _s, user = build_writer_prompt({}, {}, [], _assignment(), [("T", "X")], STANDARDS, grade=9)
    assert "Write clean, proficient work for your grade level, with no deliberate weaknesses." in user
    assert "Never mention standards and never point out or label your own flaws." in user
    assert "mistake" not in user and "trouble" not in user
    _c, cuser = build_checker_prompt({}, {}, [], _assignment(), "SUB", STANDARDS, grade=9)
    assert "(none: the submission should be proficient)" in cuser


def test_writer_prompt_includes_every_listed_excerpt(data):
    """A comparison assignment's prompt carries the main text, then each extra, each labeled by its title."""
    cur = data / "curricula" / "ela9-fixture"
    (cur / "texts" / "second-story.md").write_text(
        '---\ntitle: "The Second Story"\nauthor: "Test"\n---\n\nSECOND EXCERPT BODY\n', encoding="utf-8")
    path = cur / "assignments" / "ela9v2-a02.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps(raw | {"extra_text_ids": ["second-story"]}), encoding="utf-8")
    chat = _chat()
    _run(chat, data)
    users = [u for schema, _s, u in chat.calls if schema is ParagraphOut]
    assert users
    for user in users:
        main = user.index("Text: fixture-story\nA short invented story for tests.")  # no front matter: its id
        extra = user.index("Text: The Second Story\nSECOND EXCERPT BODY")
        assert main < extra
        assert "author:" not in user  # front matter is not passed to the writer


def test_split_text_reads_title_and_drops_front_matter():
    from agentic_lab.synth.generate_course import split_text
    assert split_text('---\ntitle: "A: B"\nyear: 1863\n---\n\nBody.\n', "x") == ("A: B", "Body.")
    assert split_text("Just a body.\n", "fallback") == ("fallback", "Just a body.")


def test_synth_settings_falls_back_to_llm(monkeypatch, tmp_path):
    from agentic.config import Settings
    from agentic_lab.config import synth_settings
    env = tmp_path / ".env"
    env.write_text("SYNTH_LLM_MODEL=model-from-file\nSYNTH_LLM_RPM=\n", encoding="utf-8")
    monkeypatch.setenv("SYNTH_LLM_API_KEY", "synth-key")
    monkeypatch.delenv("SYNTH_LLM_PROVIDER", raising=False)
    s = Settings(llm_provider="p1", llm_model="m1", llm_api_key="k1", llm_rpm=7)
    out = synth_settings(s, env_file=env)
    assert (out.llm_provider, out.llm_model, out.llm_api_key, out.llm_rpm) == ("p1", "model-from-file",
                                                                               "synth-key", 7)


def test_synth_fake_chat_handles_new_schemas():
    from agentic.config import Settings
    from agentic_lab.wiring import build_synth_chat
    chat = build_synth_chat(Settings(fake_llm=True))
    assert len(chat.structured("s", "u", ParagraphOut).paragraphs) == 1
    assert len(chat.structured("s", "u", EssayOut2).paragraphs) >= 4
    out = chat.structured("s", "ITEM q1: a\nITEM q2: b", ShortAnswerOut)
    assert [a.item_id for a in out.answers] == ["q1", "q2"]
    check = chat.structured("s", "CHECK agreement: x\nCHECK 5.L.3.A.a: y", FidelityOut)
    assert [(c.name, c.present) for c in check.checks] == [("agreement", True), ("5.L.3.A.a", True)]


def test_cli_generate_course_fake(data, monkeypatch, capsys):
    from agentic_lab.__main__ import main
    monkeypatch.setenv("DATA_DIR", str(data))
    monkeypatch.setenv("FAKE_LLM", "1")
    monkeypatch.setattr("agentic_lab.__main__.read_standard_texts", lambda: STANDARDS)
    assert main(["generate-course", "--arm", "both", "--version", "0.95", "--through-week", "1",
                 "--students", "2"]) == 0
    out = capsys.readouterr().out
    assert "0.95-control: 2 written, 0 skipped, 0 failed fidelity, 2 mc" in out
    assert "fidelity pass rate 100%" in out
    assert "placeholder" not in out.lower()
    assert len(_subs(data, IV, "ela9v2-a02")) == 2


def test_cli_generate_course_failure_prints_class_only(data, monkeypatch, capsys):
    from agentic_lab import __main__ as cli
    monkeypatch.setenv("DATA_DIR", str(data))
    monkeypatch.setenv("FAKE_LLM", "1")
    monkeypatch.setattr(cli, "read_standard_texts", lambda: STANDARDS)

    def boom(*_a, **_k):
        raise ValueError("SECRET STUDENT TEXT")

    monkeypatch.setattr(cli, "generate_course", boom)
    assert cli.main(["generate-course", "--arm", "control", "--version", "0.95"]) == 1
    err = capsys.readouterr().err
    assert "ValueError" in err and "SECRET" not in err


def test_cli_unknown_course_is_an_error(data, monkeypatch, capsys):
    from agentic_lab import __main__ as cli
    monkeypatch.setenv("DATA_DIR", str(data))
    monkeypatch.setenv("FAKE_LLM", "1")
    monkeypatch.setattr(cli, "read_standard_texts", lambda: STANDARDS)
    assert cli.main(["generate-course", "--arm", "control", "--version", "9.99"]) == 2
    assert "course.json" in capsys.readouterr().err


def test_unusable_writer_replies_are_recorded_and_skipped(data):
    def two_paragraphs(_s, _u):
        return ParagraphOut(paragraphs=["One.", "Two."])  # ValidationError, as a too-long model reply would

    chat = _chat()
    chat._handlers[ParagraphOut] = two_paragraphs
    report = _run(chat, data, students=3, through_week=2)
    assert report.unusable == 3 and report.mc == 3 and report.written == 3  # a03 short answers still written
    assert _schemas(chat).count(ParagraphOut) == 9 and _schemas(chat).count(FidelityOut) == 3
    assert _subs(data, CTRL, "ela9v2-a02") == [] and _fidelity(data, CTRL) == []
    assert len(_subs(data, CTRL, "ela9v2-a03")) == 3
    assert _rows(answer_key_path(data, CTRL) / "expected" / "ela9v2-a02.jsonl") == []
    again = _run(_chat(), data, students=3, through_week=2)  # a rerun retries just those
    assert (again.written, again.unusable, again.skipped) == (3, 0, 6)
    assert len(_subs(data, CTRL, "ela9v2-a02")) == 3


def test_malformed_checker_reply_is_one_failed_attempt(data):
    n = {"calls": 0}

    def checker(_s, u):
        n["calls"] += 1
        if n["calls"] == 1:
            return FidelityOut.model_validate({"checks": "not a list", "extra_severe": []})
        return FidelityOut(checks=[FamilyCheck(name=x, present=True) for x in _names(u)], extra_severe=[])

    chat = _chat(checker=checker)
    report = _run(chat, data, students=1)
    assert _schemas(chat) == [ParagraphOut, FidelityOut, ParagraphOut, FidelityOut]
    assert (report.written, report.failed_fidelity, report.unusable) == (1, 0, 0)
    assert _fidelity(data, CTRL) == []


def test_malformed_checker_on_last_attempt_keeps_body_all_missing(data):
    def checker(_s, _u):
        return FidelityOut.model_validate({"checks": None, "extra_severe": []})

    report = _run(_chat(checker=checker), data, students=2)
    keys = _keys(data, CTRL)
    rows = {r.submission_id: r for r in _fidelity(data, CTRL)}
    assert report.failed_fidelity == 2 and len(_subs(data, CTRL, "ela9v2-a02")) == 2
    assert rows[f"sub-control-ela9v2-a02-{keys[1].student_hash[:8]}"].missing == ["agreement"]
    assert rows[f"sub-control-ela9v2-a02-{keys[0].student_hash[:8]}"].missing == [q.standard
                                                                                   for q in keys[0].quirks]


def test_cli_unusable_exits_1_with_rerun_hint(data, monkeypatch, capsys):
    from agentic_lab import __main__ as cli
    from agentic_lab.synth.generate_course import GenerateCourseReport
    monkeypatch.setenv("DATA_DIR", str(data))
    monkeypatch.setenv("FAKE_LLM", "1")
    monkeypatch.setattr(cli, "read_standard_texts", lambda: STANDARDS)
    monkeypatch.setattr(cli, "generate_course", lambda *_a, **_k: GenerateCourseReport(written=1, unusable=2))
    assert cli.main(["generate-course", "--arm", "both", "--version", "0.95"]) == 1
    assert "4 unusable; rerun to retry" in capsys.readouterr().out


def test_overwrite_slice_keeps_rows_outside_the_slice(data):
    def failing(_s, u):
        return FidelityOut(checks=[FamilyCheck(name=x, present=False) for x in _names(u)], extra_severe=[])

    _run(_chat(checker=failing), data, students=2)
    second = CourseDir(data, CTRL).roster()[1].student_hash
    ids = {f: [r for r in _subs(data, CTRL, f) if r["student_hash"] == second] for f in ("ela9v2-a01", "ela9v2-a02")}
    fid = [f for f in _fidelity(data, CTRL) if f.submission_id in {r["submission_id"] for rs in ids.values() for r in rs}]
    expected_dir = answer_key_path(data, CTRL) / "expected"
    exp = {f: [r for r in _rows(expected_dir / f"{f}.jsonl") if r["student_hash"] == second] for f in ids}
    assert fid and all(ids.values())

    _run(_chat(), data, students=1, overwrite=True)
    for f, rows in ids.items():
        assert [r for r in _subs(data, CTRL, f) if r["student_hash"] == second] == rows
        assert [r for r in _rows(expected_dir / f"{f}.jsonl") if r["student_hash"] == second] == exp[f]
    assert [f for f in _fidelity(data, CTRL) if f in fid] == fid


def test_overwrite_through_week_leaves_later_assignments_untouched(data):
    _run(_chat(), data, through_week=2)
    later = [p for p in (data / "courses" / CTRL / "submissions").glob("*.jsonl") if p.name != "ela9v2-a01.jsonl"
             and p.name != "ela9v2-a02.jsonl"]
    before = {p.name: p.read_bytes() for p in later}
    assert before
    _run(_chat(), data, through_week=1, students=1, overwrite=True)
    assert {p.name: p.read_bytes() for p in later} == before


# --- parallel writing tasks (SYNTH_WORKERS) ---------------------------------------------------------------------

def _course_files(data: Path) -> dict[str, bytes]:
    out = {}
    for v in (CTRL, IV):
        for folder in (data / "courses" / v / "submissions", answer_key_path(data, v) / "expected"):
            out |= {f"{v}/{folder.name}/{p.name}": p.read_bytes() for p in sorted(folder.glob("*.jsonl"))}
        fid = answer_key_path(data, v) / "fidelity.jsonl"
        out[f"{v}/fidelity"] = fid.read_bytes() if fid.exists() else b""
    return out


def _stateless_checker(_s, u):
    """Fails every check for some submissions, chosen by the prompt alone (no call-order state)."""
    import hashlib
    fail = hashlib.sha256(u.encode()).digest()[0] % 2 == 0
    return FidelityOut(checks=[FamilyCheck(name=n, present=not fail) for n in _names(u)], extra_severe=[])


def _h(text: str) -> str:
    import hashlib
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def _per_prompt_chat(jitter: bool) -> FakeChatModel:
    """Writers reply with text derived from the prompt (so every task's body differs); with `jitter`, each
    call sleeps a prompt-seeded 0-30 ms so tasks finish out of plan order."""
    import time

    def wait(u):
        if jitter:
            time.sleep(int(_h(u)[:2], 16) / 255 * 0.03)

    def para(_s, u):
        wait(u)
        return ParagraphOut(paragraphs=[_h(u)])

    def short(_s, u):
        wait(u)
        return ShortAnswerOut(answers=[{"item_id": i, "text": _h(u + i)} for i in _items(u)])

    def essay(_s, u):
        wait(u)
        return EssayOut2(paragraphs=[_h(u + str(i)) for i in range(4)])

    return FakeChatModel({ParagraphOut: para, ShortAnswerOut: short, EssayOut2: essay,
                          FidelityOut: _stateless_checker})


def _body(row: dict):
    return row.get("paragraphs") or [(a["item_id"], a["text"]) for a in row.get("answers", [])]


def test_workers_1_and_4_write_identical_files(data, tmp_path, monkeypatch):
    from agentic_lab.synth import generate_course as gc
    real = gc._write_one
    produced: dict[tuple[str, str], object] = {}  # (data dir, submission id) -> body its own task wrote

    def recording(chat, assignment, student, levels, ev, families, texts, standards, grade, sub_id):
        body, failure = real(chat, assignment, student, levels, ev, families, texts, standards, grade, sub_id)
        produced[(chat.model_name, sub_id)] = body
        return body, failure

    monkeypatch.setattr(gc, "_write_one", recording)
    other = tmp_path.parent / (tmp_path.name + "-w4")
    shutil.copytree(data, other)
    reports = []
    for d, workers in ((data, 1), (other, 4)):
        for v in (CTRL, IV):
            chat = _per_prompt_chat(jitter=workers > 1)
            chat.model_name = str(d)  # tags `produced` by run
            reports.append(_run(chat, d, v, through_week=None, workers=workers))
    assert reports[0] == reports[2] and reports[1] == reports[3]
    assert reports[0].written and reports[0].failed_fidelity  # both outcomes exercised
    a, b = _course_files(data), _course_files(other)
    assert a.keys() == b.keys() and any(b.values())
    assert a == b
    # Per row: the body in the file is the one that row's own task produced (not another task's).
    checked, bodies = 0, set()
    for d in (data, other):
        for v in (CTRL, IV):
            for path in sorted((d / "courses" / v / "submissions").glob("*.jsonl")):
                for row in _rows(path):
                    key = (str(d), row["submission_id"])
                    if key not in produced:
                        continue  # multiple choice
                    got = _body(row)
                    want = produced[key]
                    want = want if not want or isinstance(want[0], str) else [(x.item_id, x.text) for x in want]
                    assert got == want, row["submission_id"]
                    checked += 1
                    bodies.add(json.dumps(got))
    assert checked == 2 * reports[0].written + 2 * reports[1].written
    assert len(bodies) > 1  # the bodies really differ per task


def test_writing_calls_overlap_when_workers_above_1(data):
    import threading
    import time
    lock = threading.Lock()
    state = {"now": 0, "max": 0}

    def writer():
        with lock:
            state["now"] += 1
            state["max"] = max(state["max"], state["now"])
        time.sleep(0.05)
        with lock:
            state["now"] -= 1

    report = _run(_chat(writer=writer), data, through_week=None, workers=4)
    assert report.written and state["max"] > 1


def test_workers_1_never_overlaps(data):
    import threading
    import time
    lock = threading.Lock()
    state = {"now": 0, "max": 0}

    def writer():
        with lock:
            state["now"] += 1
            state["max"] = max(state["max"], state["now"])
        time.sleep(0.01)
        with lock:
            state["now"] -= 1

    _run(_chat(writer=writer), data, through_week=None, workers=1)
    assert state["max"] == 1


def test_task_error_surfaces_and_does_not_hang(data):
    calls = {"n": 0}

    def writer():
        calls["n"] += 1
        if calls["n"] >= 2:  # every later call fails too, whatever order the threads run in
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        _run(_chat(writer=writer), data, through_week=None, workers=4)


def test_workers_below_1_rejected(data):
    with pytest.raises(ValueError, match="workers"):
        _run(_chat(), data, workers=0)


def test_synth_workers_setting(monkeypatch, tmp_path):
    from agentic_lab.config import synth_workers
    env = tmp_path / ".env"
    monkeypatch.delenv("SYNTH_WORKERS", raising=False)
    env.write_text("SYNTH_WORKERS=\n", encoding="utf-8")
    assert synth_workers(env_file=env) == 8  # blank = default
    env.write_text("SYNTH_WORKERS=3\n", encoding="utf-8")
    assert synth_workers(env_file=env) == 3
    monkeypatch.setenv("SYNTH_WORKERS", "5")  # the environment wins, as for SYNTH_LLM_*
    assert synth_workers(env_file=env) == 5
    monkeypatch.setenv("SYNTH_WORKERS", "0")
    with pytest.raises(ValueError, match="SYNTH_WORKERS"):
        synth_workers(env_file=env)
    monkeypatch.setenv("SYNTH_WORKERS", "many")
    with pytest.raises(ValueError, match="SYNTH_WORKERS"):
        synth_workers(env_file=env)


def _cli_capture(data, monkeypatch):
    from agentic_lab import __main__ as cli
    from agentic_lab.synth.generate_course import GenerateCourseReport
    monkeypatch.setenv("DATA_DIR", str(data))
    monkeypatch.setenv("FAKE_LLM", "1")
    monkeypatch.setattr(cli, "read_standard_texts", lambda: STANDARDS)
    seen = []
    monkeypatch.setattr(cli, "generate_course",
                        lambda *_a, **k: seen.append(k["workers"]) or GenerateCourseReport())
    return cli, seen


def test_cli_workers_flag_and_setting(data, monkeypatch):
    cli, seen = _cli_capture(data, monkeypatch)
    monkeypatch.setenv("SYNTH_WORKERS", "3")
    assert cli.main(["generate-course", "--arm", "control", "--version", "0.95"]) == 0
    assert cli.main(["generate-course", "--arm", "control", "--version", "0.95", "--workers", "2"]) == 0
    monkeypatch.delenv("SYNTH_WORKERS")
    monkeypatch.setattr("agentic_lab.config.find_env_file", lambda: None)
    assert cli.main(["generate-course", "--arm", "control", "--version", "0.95"]) == 0
    assert seen == [3, 2, 8]


def test_cli_workers_zero_is_an_error(data, monkeypatch, capsys):
    cli, seen = _cli_capture(data, monkeypatch)
    assert cli.main(["generate-course", "--arm", "control", "--version", "0.95", "--workers", "0"]) == 2
    assert "--workers" in capsys.readouterr().err and seen == []
    monkeypatch.setenv("SYNTH_WORKERS", "0")
    assert cli.main(["generate-course", "--arm", "control", "--version", "0.95"]) == 2
    assert "SYNTH_WORKERS" in capsys.readouterr().err and seen == []


def test_check_key_tolerates_prompt_prefix_case_and_spaces():
    from agentic_lab.synth.generate_course import _check_key
    for raw in ["CHECK vocabulary", "check: Vocabulary ", " CHECK  vocabulary:", "vocabulary", "VOCABULARY"]:
        assert _check_key(raw) == "vocabulary"
    assert _check_key("CHECK 9-10.W.3.A.c:") == _check_key("9-10.W.3.A.c")
    assert _check_key("checkpoint") == "checkpoint"  # only a whole leading word CHECK is a prefix


def test_prefixed_check_names_count_as_present(data):
    def prefixed(_s, u):
        return FidelityOut(checks=[FamilyCheck(name=f"CHECK {n.upper()}:", present=True) for n in _names(u)],
                           extra_severe=[])
    _run(_chat(checker=prefixed), data)
    assert _fidelity(data, CTRL) == []


def test_unrelated_check_names_are_missing(data):
    def unrelated(_s, u):
        return FidelityOut(checks=[FamilyCheck(name="CHECK something else", present=True)], extra_severe=[])
    _run(_chat(checker=unrelated), data)
    rows = _fidelity(data, CTRL)
    assert rows and all(r.missing for r in rows)


def _family_prompts(name: str, level: int = 2) -> tuple[str, str]:
    code = next(c for c in FAMILIES[name].codes if c.startswith("9-10"))
    fam = {name: FAMILIES[name]}
    _s, user = build_writer_prompt({code: level}, fam, [], _assignment(), [("T", "X")], STANDARDS, grade=9)
    _c, cuser = build_checker_prompt({code: level}, fam, [], _assignment(), "SUB", STANDARDS, grade=9)
    return user, cuser


def test_evidence_mistake_includes_quotes_left_unexplained():
    """A quote that is only restated or left unexplained is the evidence weakness, not just a missing quote."""
    from agentic_lab.synth.generate_course import _SLIP_EXAMPLES
    for prompt in _family_prompts("evidence"):
        assert "explaining how it supports" in prompt
        assert "a claim with no evidence" in prompt
        assert "a quote that is only restated" in prompt and "left unexplained" in prompt
    # the example quotes (single quotes, so it nests inside the prompt's double quotes), then explains too little
    assert _SLIP_EXAMPLES["evidence"].count("'") >= 2 and '"' not in _SLIP_EXAMPLES["evidence"]


def test_other_families_keep_the_shared_mistake_line():
    for name in FAMILIES:
        if name == "evidence":
            continue
        for prompt in _family_prompts(name):
            assert "A mistake is a place where a sentence fails to do this correctly. Secure" in prompt.replace(
                "\n  ", " ")
            assert "quote" not in prompt


# --- multiple choice: concept fidelity, detectability, --mc-only ------------------------------------------------

def _student_levels(data, version=CTRL, week=1) -> list[dict[str, int]]:
    return [levels_at(k, week, [], FAMILIES, 9) for k in _keys(data, version)]


def _write_quiz(data, items, file_id="ela9v2-a01"):
    path = data / "curricula" / "ela9-fixture" / "assignments" / f"{file_id}.json"
    asg = json.loads(path.read_text(encoding="utf-8"))
    asg["items"] = items
    path.write_text(json.dumps(asg), encoding="utf-8")


def _quiz_item(iid, std, *distractor_stds, correct="A"):
    others = iter(distractor_stds)
    choices = []
    for key in "ABCD"[:len(distractor_stds) + 1]:
        if key == correct:
            choices.append({"key": key, "text": f"right {iid}", "correct": True})
        else:
            choices.append({"key": key, "text": f"wrong {key}", "misconception": "m", "standard": next(others)})
    return {"id": iid, "stem": "s", "standard": std, "choices": choices}


def test_mc_concept_fidelity_row_for_a_quiz_design_error(data):
    """A planted (>= 2) code that is a distractor in only one item can never reach 2 picks: a fidelity row."""
    levels = _student_levels(data)
    code = next(c for c, lv in sorted(levels[2].items()) if lv >= 2 and ".RL." in c)  # the shallow student
    concept = ".".join(code.split(".")[:4])
    _write_quiz(data, [_quiz_item("q1", code, code), _quiz_item("q2", code, "5.R.1.B.b")])
    report = _run(_chat(), data)
    keys = _keys(data, CTRL)
    rows = {r.submission_id: r for r in _fidelity(data, CTRL)}
    bad = rows[f"sub-control-ela9v2-a01-{keys[2].student_hash[:8]}"]
    assert bad.missing == [f"concept:{concept}"] and bad.extra == []
    assert report.mc == 3 and report.failed_mc == 1
    assert report.pass_rate == 1.0  # written submissions only; multiple choice is reported on its own line


def test_mc_detectability_counts_concept_pairs_by_planted_level(data):
    levels = _student_levels(data)
    code = next(c for c, lv in sorted(levels[2].items()) if lv >= 2 and ".RL." in c)
    _write_quiz(data, [_quiz_item(f"q{n}", code, code, code) for n in range(1, 4)])
    report = _run(_chat(), data)
    lv = levels[2][code]
    # one (submission, concept) pair is planted: the shallow student's, with 3 picks; the others are unplanted
    assert report.mc_detect == {lv: (1, 1)}
    assert report.failed_mc == 0


def test_mc_rows_count_in_fidelity_without_overwrite(data):
    """Non-overwrite runs record MC fidelity rows too, and a passing rerun of the same id removes them."""
    levels = _student_levels(data)
    code = next(c for c, lv in sorted(levels[2].items()) if lv >= 2 and ".RL." in c)
    _write_quiz(data, [_quiz_item("q1", code, code)])
    _run(_chat(), data)
    assert [r.submission_id[:22] for r in _fidelity(data, CTRL)] == ["sub-control-ela9v2-a01"]
    _write_quiz(data, [_quiz_item(f"q{n}", code, code) for n in (1, 2)])
    _run(_chat(), data, overwrite=True, mc_only=True)
    assert _fidelity(data, CTRL) == []


def _no_chat() -> FakeChatModel:
    def never(*_a):
        raise AssertionError("--mc-only must not call the chat model")
    return FakeChatModel({ParagraphOut: never, ShortAnswerOut: never, FidelityOut: never, EssayOut2: never})


def test_mc_only_overwrite_rewrites_only_multiple_choice(data):
    def failing(_s, u):
        return FidelityOut(checks=[FamilyCheck(name=x, present=False) for x in _names(u)], extra_severe=[])

    _run(_chat(checker=failing), data, through_week=2)  # a02 and a03 get fidelity rows
    sub_dir, exp_dir = data / "courses" / CTRL / "submissions", answer_key_path(data, CTRL) / "expected"
    written = {p.name: p.read_bytes() for d in (sub_dir, exp_dir) for p in d.glob("*.jsonl")
               if "a01" not in p.name}
    fid_path = answer_key_path(data, CTRL) / "fidelity.jsonl"
    written_fid = [x for x in fid_path.read_bytes().splitlines(keepends=True) if b"-a01-" not in x]
    assert written and written_fid
    old_mc = _subs(data, CTRL, "ela9v2-a01")

    _write_quiz(data, [_quiz_item("q1", "9-10.RL.1.B", "9-10.RL.1.B", correct="B")])  # moves the key
    chat = _no_chat()
    report = _run(chat, data, through_week=2, overwrite=True, mc_only=True)
    assert chat.calls == []
    assert (report.mc, report.written, report.skipped) == (3, 0, 0)
    assert {p.name: p.read_bytes() for d in (sub_dir, exp_dir) for p in d.glob("*.jsonl")
            if "a01" not in p.name} == written
    assert [x for x in fid_path.read_bytes().splitlines(keepends=True) if b"-a01-" not in x] == written_fid
    new_mc = _subs(data, CTRL, "ela9v2-a01")
    assert [r["submission_id"] for r in new_mc] == [r["submission_id"] for r in old_mc]
    assert [r["submitted_at"] for r in new_mc] == [r["submitted_at"] for r in old_mc]
    assert all(r["answers"][0]["choice"] in ("A", "B") for r in new_mc) and new_mc != old_mc


def test_mc_only_without_overwrite_fills_missing_mc_rows_only(data):
    chat = _no_chat()
    report = _run(chat, data, through_week=2, mc_only=True)
    assert chat.calls == [] and report.mc == 3 and report.written == 0
    assert len(_subs(data, CTRL, "ela9v2-a01")) == 3
    assert _subs(data, CTRL, "ela9v2-a02") == [] and _subs(data, CTRL, "ela9v2-a03") == []


def test_cli_mc_only_builds_no_chat_model(data, monkeypatch, capsys):
    from agentic_lab import __main__ as cli
    monkeypatch.setenv("DATA_DIR", str(data))
    monkeypatch.setenv("FAKE_LLM", "1")
    monkeypatch.setattr(cli, "read_standard_texts", lambda: STANDARDS)

    def no_model(*_a, **_k):
        raise AssertionError("no chat model for --mc-only")

    monkeypatch.setattr(cli, "build_synth_chat", no_model)
    assert cli.main(["generate-course", "--arm", "control", "--version", "0.95", "--mc-only",
                     "--overwrite"]) == 0
    out = capsys.readouterr().out
    assert "0.95-control: 0 written, 0 skipped, 0 failed fidelity, 3 mc" in out
    assert "0.95-control: multiple choice 3/3 passed concept fidelity; detectability" in out


def test_cli_pass_rate_is_written_only_and_mc_has_its_own_line(data, monkeypatch, capsys):
    from agentic_lab import __main__ as cli
    from agentic_lab.synth.generate_course import GenerateCourseReport
    monkeypatch.setenv("DATA_DIR", str(data))
    monkeypatch.setenv("FAKE_LLM", "1")
    monkeypatch.setattr(cli, "read_standard_texts", lambda: STANDARDS)
    report = GenerateCourseReport(written=4, failed_fidelity=1, mc=10, failed_mc=2,
                                  mc_detect={1: (3, 6), 2: (4, 4)})
    assert report.pass_rate == 0.75
    monkeypatch.setattr(cli, "generate_course", lambda *_a, **_k: report)
    assert cli.main(["generate-course", "--arm", "control", "--version", "0.95"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert "0.95-control: 4 written, 0 skipped, 1 failed fidelity, 10 mc; fidelity pass rate 75%" in lines
    assert ("0.95-control: multiple choice 8/10 passed concept fidelity; detectability (pairs with >= 2 picks): "
            "level 1 3/6 (50%), level 2 4/4 (100%), level 3 n/a") in lines

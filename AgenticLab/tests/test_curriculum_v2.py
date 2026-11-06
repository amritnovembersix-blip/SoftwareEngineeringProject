"""Curriculum ela9-v2 content: the spec §3 table, reviewable codes, item shapes and public-domain texts."""
import json
import re
from pathlib import Path

import pytest

from agentic.coursedata import AssignmentFile, Curriculum, curriculum_path, read_json
from agentic_lab.standards_v2 import REVIEWABILITY_CSV, reviewable_codes

DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "synthetic"
CUR_DIR = curriculum_path(DATA_DIR, "ela9-v2")

# Spec §3: (id, week, type, text_id, due day). The first assignment each week is due Wednesday, the second Friday.
TABLE = [
    ("ela9v2-a01", 1, "multiple_choice", "most-dangerous-game", "wed"),
    ("ela9v2-a02", 1, "paragraph", "most-dangerous-game", "fri"),
    ("ela9v2-a03", 2, "short_answer", "most-dangerous-game", "wed"),
    ("ela9v2-a04", 2, "paragraph", "most-dangerous-game", "fri"),
    ("ela9v2-a05", 3, "multiple_choice", "the-necklace", "wed"),
    ("ela9v2-a06", 3, "paragraph", "the-necklace", "fri"),
    ("ela9v2-a07", 4, "short_answer", "gift-of-the-magi", "wed"),
    ("ela9v2-a08", 4, "paragraph", "gift-of-the-magi", "fri"),  # Necklace + Magi
    ("ela9v2-a09", 5, "multiple_choice", "monkeys-paw", "wed"),
    ("ela9v2-a10", 5, "essay", "monkeys-paw", "fri"),  # theme across two stories
    ("ela9v2-a11", 6, "multiple_choice", "gettysburg-address", "wed"),
    ("ela9v2-a12", 6, "paragraph", "gettysburg-address", "fri"),
    ("ela9v2-a13", 7, "short_answer", "give-me-liberty", "wed"),
    ("ela9v2-a14", 7, "paragraph", "give-me-liberty", "fri"),
    ("ela9v2-a15", 8, "multiple_choice", "aint-i-a-woman", "wed"),
    ("ela9v2-a16", 8, "paragraph", "aint-i-a-woman", "fri"),
    ("ela9v2-a17", 9, "short_answer", "fourth-of-july-douglass", "wed"),
    ("ela9v2-a18", 9, "paragraph", "fourth-of-july-douglass", "fri"),
    ("ela9v2-a19", 10, "multiple_choice", "womens-right-to-vote", "wed"),
    ("ela9v2-a20", 10, "paragraph", "womens-right-to-vote", "fri"),  # Anthony + Douglass
    ("ela9v2-a21", 11, "short_answer", "four-freedoms", "wed"),
    ("ela9v2-a22", 11, "essay", "fourth-of-july-douglass", "fri"),  # rhetorical analysis of Douglass
]

TARGETS = {
    "ela9v2-a01": ["RL.1.A", "RL.1.D"], "ela9v2-a02": ["RL.1.A"], "ela9v2-a03": ["RL.2.D", "RL.1.A"],
    "ela9v2-a04": ["RL.2.A"], "ela9v2-a05": ["RL.1.D", "RL.1.A"], "ela9v2-a06": ["RL.1.D"],
    "ela9v2-a07": ["RL.2.C", "RL.2.B"], "ela9v2-a08": ["RL.2.D", "RL.3.B"], "ela9v2-a09": ["RL.1.A"],
    "ela9v2-a10": ["RL.1.A", "RL.1.D", "RL.3.B", "W.2.A"], "ela9v2-a11": ["RI.1.D", "RI.1.A"],
    "ela9v2-a12": ["RI.1.D"], "ela9v2-a13": ["RI.2.B", "RI.1.A"], "ela9v2-a14": ["RI.2.B"],
    "ela9v2-a15": ["RI.1.A", "RI.2.D"], "ela9v2-a16": ["RI.2.D"], "ela9v2-a17": ["RI.3.C", "RI.1.A"],
    "ela9v2-a18": ["RI.2.C"], "ela9v2-a19": ["RI.2.D", "RI.1.A"], "ela9v2-a20": ["RI.3.B"],
    "ela9v2-a21": ["RI.1.A", "RI.3.A"], "ela9v2-a22": ["RI.1.A", "RI.2.B", "RI.2.D", "RI.3.C", "W.2.A"],
}

# Spec §3 concept plan B (concepts the 2.00 cohort plants): each quiz tests these concepts (a code's first 4 segments) with this many items.
CONCEPTS = {
    "ela9v2-a01": {"9-10.RL.1.A": 4, "8.RL.1.A": 3, "9-10.RL.1.D": 3},
    "ela9v2-a05": {"9-10.RL.1.D": 4, "9-10.RL.1.A": 3, "6.RL.1.B": 3},
    "ela9v2-a09": {"9-10.RL.1.A": 4, "8.RL.1.A": 3, "6.RL.1.A": 3},
    "ela9v2-a11": {"9-10.RI.1.D": 4, "9-10.RI.1.A": 3, "6.RI.1.B": 3},
    "ela9v2-a15": {"9-10.RI.1.A": 4, "8.RI.1.A": 3, "9-10.RI.2.D": 3},
    "ela9v2-a19": {"9-10.RI.2.D": 4, "9-10.RI.1.A": 3, "7.RI.1.A": 3},
}

# Comparison assignments carry every compared text: text_id first, then these.
EXTRA_TEXTS = {"ela9v2-a08": ["the-necklace"], "ela9v2-a10": ["the-necklace"],
               "ela9v2-a17": ["give-me-liberty"], "ela9v2-a20": ["fourth-of-july-douglass"]}

TEXT_IDS = ["most-dangerous-game", "the-necklace", "gift-of-the-magi", "monkeys-paw", "gettysburg-address",
            "give-me-liberty", "aint-i-a-woman", "fourth-of-july-douglass", "womens-right-to-vote",
            "four-freedoms"]

CODE = re.compile(r"\b(?:\d|9-10|11-12)\.(?:R|RL|RI|W|L)\.\d\.[A-Z]")


@pytest.fixture(scope="module")
def curriculum() -> Curriculum:
    return read_json(CUR_DIR / "curriculum.json", Curriculum)


@pytest.fixture(scope="module")
def assignments(curriculum) -> list[AssignmentFile]:
    return [read_json(CUR_DIR / "assignments" / f"{a}.json", AssignmentFile) for a in curriculum.assignments]


def read_json_raw(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _grade(code: str) -> int:
    return int(code.split(".")[0].split("-")[0])


def _strand(code: str) -> str:
    return code.split(".")[1]


def test_curriculum_header(curriculum):
    assert curriculum.id == "ela9-v2" and curriculum.grade == 9
    assert [(m.id, m.title, m.strand, m.weeks) for m in curriculum.modules] == [
        ("rl", "Choices and Consequences", "RL", (1, 5)), ("ri", "Voices of Freedom", "RI", (6, 11))]
    assert curriculum.assignments == [f"ela9v2-a{n:02d}" for n in range(1, 23)]


def test_table_matches_spec(assignments):
    got = [(a.id, a.week, a.type, a.text_id, a.due_offset.day) for a in assignments]
    assert got == TABLE
    for a in assignments:
        assert a.due_offset.week == a.week
        assert a.module == ("rl" if a.week <= 5 else "ri")
        assert a.kind == "regular" and a.assigned_to is None


def test_targets_match_spec(assignments):
    assert {a.id: a.targets for a in assignments} == {k: [f"9-10.{c}" for c in v] for k, v in TARGETS.items()}


def test_every_code_is_reviewable(assignments):
    reviewable = reviewable_codes(REVIEWABILITY_CSV)
    codes = set()
    for a in assignments:
        codes |= set(a.targets)
        for item in a.items:
            codes |= {item.standard} | {c.standard for c in item.choices if c.standard}
    assert sorted(codes - reviewable) == []


def _concept(code: str) -> str:
    return ".".join(code.split(".")[:4])


def test_multiple_choice_shape(assignments):
    for a in (a for a in assignments if a.type == "multiple_choice"):
        assert [i.id for i in a.items] == [f"q{n}" for n in range(1, 11)], a.id
        for item in a.items:
            assert item.stem and not item.prompt, (a.id, item.id)
            assert len(item.choices) == 4 and [c.key for c in item.choices] == ["A", "B", "C", "D"], (a.id, item.id)
            assert sum(c.correct for c in item.choices) == 1, (a.id, item.id)
            assert all(c.misconception and c.standard for c in item.choices if not c.correct), (a.id, item.id)
            assert len({c.text for c in item.choices}) == 4, (a.id, item.id)


def test_quizzes_follow_the_concept_plan(assignments):
    """10 items over 2-3 concepts, at least 3 items each; an item and all its distractors share one concept
    (grade included), so per-concept pick counts never split across concepts or grades."""
    got = {}
    for a in (a for a in assignments if a.type == "multiple_choice"):
        counts: dict[str, int] = {}
        for item in a.items:
            concept = _concept(item.standard)
            assert {_concept(c.standard) for c in item.choices if not c.correct} == {concept}, (a.id, item.id)
            counts[concept] = counts.get(concept, 0) + 1
        assert 2 <= len(counts) <= 3 and min(counts.values()) >= 3, (a.id, counts)
        got[a.id] = counts
    assert got == CONCEPTS


def test_concept_items_ask_about_their_own_standard(assignments):
    """Plan B concepts are 4-segment codes: every distractor carries its item's own standard, so a wrong
    answer reads as "missed the item's standard"."""
    for a in (a for a in assignments if a.type == "multiple_choice"):
        for item in a.items:
            assert {c.standard for c in item.choices if not c.correct} == {item.standard}, (a.id, item.id)


def test_quiz_codes_can_be_planted(assignments):
    """Every quiz code is a skill-family member, so planted students produce picks."""
    family_codes = {c for codes in read_json_raw(DATA_DIR / "skill_families.json").values() for c in codes}
    for a in (a for a in assignments if a.type == "multiple_choice"):
        codes = {i.standard for i in a.items} | {c.standard for i in a.items for c in i.choices if c.standard}
        assert sorted(codes - family_codes) == [], a.id


def test_every_tested_code_is_a_distractor_in_two_items(assignments):
    """A student at level >= 2 on any code a concept tests then picks at least twice (the MC fidelity guard)."""
    for a in (a for a in assignments if a.type == "multiple_choice"):
        tested = {i.standard for i in a.items} | {c.standard for i in a.items for c in i.choices if c.standard}
        for code in tested:
            items = [i.id for i in a.items if any(c.standard == code for c in i.choices if not c.correct)]
            assert len(items) >= 2, (a.id, code, items)


def test_short_answer_shape(assignments, curriculum):
    strand = {m.id: m.strand for m in curriculum.modules}
    for a in (a for a in assignments if a.type == "short_answer"):
        assert len(a.items) == 3, a.id
        for item in a.items:
            assert item.prompt and item.standard and not item.choices, (a.id, item.id)
            assert item.standard.startswith(f"9-10.{strand[a.module]}."), (a.id, item.id)


def test_prompts_never_mention_codes(assignments):
    for a in assignments:
        texts = [a.title, a.prompt] + [t for i in a.items for t in (i.prompt, i.stem) if t]
        texts += [c.text for i in a.items for c in i.choices]
        assert not [t for t in texts if CODE.search(t)], a.id


def _front_matter(path: Path) -> tuple[dict[str, str], str]:
    raw = path.read_text(encoding="utf-8")
    m = re.match(r"---\n(.*?)\n---\n(.*)", raw, re.S)
    assert m, f"{path.name} has no front matter"
    meta = dict(line.split(": ", 1) for line in m.group(1).splitlines())
    return {k: v.strip().strip('"') for k, v in meta.items()}, m.group(2)


@pytest.mark.parametrize("text_id", TEXT_IDS)
def test_texts_have_front_matter_and_length(text_id):
    meta, body = _front_matter(CUR_DIR / "texts" / f"{text_id}.md")
    assert set(meta) >= {"title", "author", "year", "source_url", "excerpt"}
    assert meta["source_url"].startswith("https://")
    assert meta["excerpt"] in ("true", "false")
    assert 300 <= len(body.split()) <= 700, len(body.split())


def test_every_assignment_text_exists(assignments):
    assert {a.text_id for a in assignments} == set(TEXT_IDS)
    for a in assignments:
        for t in [a.text_id, *a.extra_text_ids]:
            assert (CUR_DIR / "texts" / f"{t}.md").exists(), (a.id, t)


def test_comparisons_list_their_extra_texts(assignments):
    assert {a.id: a.extra_text_ids for a in assignments if a.extra_text_ids} == EXTRA_TEXTS
    for a in assignments:
        assert a.text_id not in a.extra_text_ids, a.id


def test_answer_keys_are_balanced(assignments):
    """Constrained shuffle (seed 2027): per quiz no letter more than 3 times and no three in a row; the six
    sequences pairwise distinct; over all 60 items each letter 13-17 times."""
    keys = ["".join(c.key for i in a.items for c in i.choices if c.correct)
            for a in assignments if a.type == "multiple_choice"]
    assert len(keys) == 6 and all(len(k) == 10 for k in keys)
    assert len(set(keys)) == len(keys)  # quiz sequences pairwise distinct
    for seq in keys:
        assert max(seq.count(k) for k in "ABCD") <= 3, seq
        assert not any(seq[i] == seq[i + 1] == seq[i + 2] for i in range(len(seq) - 2)), seq
    overall = "".join(keys)
    assert all(13 <= overall.count(k) <= 17 for k in "ABCD"), overall


def test_aint_i_a_woman_names_the_gage_version():
    meta, _ = _front_matter(CUR_DIR / "texts" / "aint-i-a-woman.md")
    assert meta["year"] == "1863"
    assert "Gage" in meta["note"] and "1851" in meta["note"]
    assert sorted(p.stem for p in (CUR_DIR / "texts").glob("*.md")) == sorted(TEXT_IDS)

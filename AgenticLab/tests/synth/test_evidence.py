import json
import random
from pathlib import Path

from agentic.coursedata import AssignmentFile
from agentic_lab.synth.evidence import choose_answers, evidenced

CURRICULA = Path(__file__).resolve().parents[2] / "data" / "synthetic" / "curricula" / "ela9-v2" / "assignments"


def _asg(type_, items=()):
    return AssignmentFile.model_validate({
        "id": "a", "module": "m", "week": 1, "kind": "regular", "type": type_, "title": "t",
        "prompt": "p", "text_id": "x", "due_offset": {"week": 1, "day": "fri"}, "items": list(items)})


def _mc(*items):
    return _asg("multiple_choice", items)


def _item(iid, std, *distractors):
    choices = [{"key": "A", "text": "right", "correct": True}]
    for key, dstd in distractors:
        choices.append({"key": key, "text": "x", "misconception": "m", "standard": dstd})
    return {"id": iid, "standard": std, "stem": "s", "choices": choices}


def test_level_two_distractor_chosen():
    a = _mc(_item("i1", "9-10.RL.1.A", ("B", "8.RL.1.A")))
    assert choose_answers(a, {"8.RL.1.A": 2}, random.Random(0))[0].choice == "B"


def test_no_levels_all_correct():
    a = _mc(_item("i1", "9-10.RL.1.A", ("B", "8.RL.1.A")), _item("i2", "9-10.RL.1.A", ("B", "7.RL.1.A")))
    assert [x.choice for x in choose_answers(a, {}, random.Random(0))] == ["A", "A"]
    assert [x.item_id for x in choose_answers(a, {}, random.Random(0))] == ["i1", "i2"]


def test_highest_level_wins():
    a = _mc(_item("i1", "9-10.RL.1.A", ("B", "8.RL.1.A"), ("C", "7.RL.1.A")))
    assert choose_answers(a, {"8.RL.1.A": 2, "7.RL.1.A": 3}, random.Random(0))[0].choice == "C"


def test_tie_broken_by_key():
    a = _mc(_item("i1", "9-10.RL.1.A", ("C", "8.RL.1.A"), ("B", "7.RL.1.A")))
    assert choose_answers(a, {"8.RL.1.A": 2, "7.RL.1.A": 2}, random.Random(0))[0].choice == "B"


def test_level_one_is_seeded_coin_flip():
    a = _mc(*[_item(f"i{n}", "9-10.RL.1.A", ("B", "8.RL.1.A")) for n in range(20)])
    levels = {"8.RL.1.A": 1}
    first = [x.choice for x in choose_answers(a, levels, random.Random(7))]
    assert first == [x.choice for x in choose_answers(a, levels, random.Random(7))]
    rng = random.Random(7)
    assert first == ["B" if rng.random() < 0.5 else "A" for _ in range(20)]
    assert {"A", "B"} == set(first)


def test_rl_module_essay_excludes_ri_keeps_lower_r_and_l():
    levels = {"9-10.RI.1.A": 2, "9-10.RL.1.A": 2, "5.R.1.A.b": 1, "3.L.1.A.e": 3}
    got = evidenced(_asg("essay"), "RL", levels)
    assert "9-10.RI.1.A" not in got
    assert got == {"9-10.RL.1.A": 2, "5.R.1.A.b": 1, "3.L.1.A.e": 3}


def test_short_answer_rule():
    levels = {"9-10.RL.1.A": 2, "9-10.RL.2.A": 2, "5.R.1.A.b": 1, "3.L.1.A.e": 3,
              "9-10.W.3.A.c": 2, "8.RI.1.A": 1}
    a = _asg("short_answer", [{"id": "i1", "standard": "9-10.RL.1.A", "prompt": "q"}])
    assert set(evidenced(a, "RL", levels)) == {"9-10.RL.1.A", "5.R.1.A.b", "3.L.1.A.e", "9-10.W.3.A.c"}


def test_mc_evidences_only_item_and_distractor_codes():
    a = _mc(_item("i1", "9-10.RL.1.A", ("B", "8.RL.1.A")))
    levels = {"9-10.RL.1.A": 1, "8.RL.1.A": 2, "3.L.1.A.e": 3, "9-10.W.3.A.c": 2}
    assert evidenced(a, "RL", levels) == {"9-10.RL.1.A": 1, "8.RL.1.A": 2}


def test_real_mc_file_answers_every_item():
    for path in sorted(CURRICULA.glob("*.json")):
        a = AssignmentFile.model_validate(json.loads(path.read_text(encoding="utf-8")))
        if a.type == "multiple_choice":
            out = choose_answers(a, {}, random.Random(1))
            assert [x.item_id for x in out] == [i.id for i in a.items]
            # no weak levels: every answer is the item's correct key, wherever the file puts it
            assert [x.choice for x in out] == [next(c.key for c in i.choices if c.correct) for i in a.items]
            return
    raise AssertionError("no multiple_choice assignment found")


# --- concept quizzes: one decision per item, concept fidelity (spec 5.2, 5.3) ------------------------------------

class _CountingRandom(random.Random):
    def __init__(self, seed):
        super().__init__(seed)
        self.rolls = 0

    def random(self):
        self.rolls += 1
        return super().random()


def _concept_item(iid, std, *distractor_stds):
    return _item(iid, std, *zip("BCD", distractor_stds))


def test_developing_concept_with_three_items_gives_at_least_two_picks():
    a = _mc(*[_concept_item(f"q{n}", "4.R.1.B.b", "4.R.1.B.b", "4.R.1.B.d", "4.R.1.B.b") for n in range(1, 4)])
    for seed in range(20):
        out = choose_answers(a, {"4.R.1.B.b": 2, "4.R.1.B.d": 2}, random.Random(seed))
        assert sum(x.choice != "A" for x in out) >= 2
        assert [x.choice for x in out] == ["B", "B", "B"]  # tie at level 2: the lowest key


def test_highest_level_distractor_wins_within_an_item():
    a = _mc(_concept_item("q1", "4.R.1.B.b", "4.R.1.B.b", "4.R.1.B.d", "4.R.1.B.b"))
    assert choose_answers(a, {"4.R.1.B.b": 2, "4.R.1.B.d": 3}, random.Random(0))[0].choice == "C"


def test_approaching_concept_rolls_exactly_once_per_item():
    items = [_concept_item(f"q{n}", "9-10.RL.1.B", "9-10.RL.1.B", "9-10.RL.1.B", "9-10.RL.1.B") for n in range(40)]
    a = _mc(*items)
    rng = _CountingRandom(5)
    out = choose_answers(a, {"9-10.RL.1.B": 1}, rng)
    assert rng.rolls == 40
    assert {x.choice for x in out} == {"A", "B"}  # a successful roll picks the lowest key among level-1 ones
    replay = random.Random(5)
    assert [x.choice for x in out] == ["B" if replay.random() < 0.5 else "A" for _ in range(40)]


def test_level_two_item_does_not_roll():
    a = _mc(_concept_item("q1", "4.R.1.B.b", "4.R.1.B.b", "4.R.1.B.d", "4.R.1.B.b"))
    rng = _CountingRandom(1)
    assert choose_answers(a, {"4.R.1.B.b": 1, "4.R.1.B.d": 2}, rng)[0].choice == "C"
    assert rng.rolls == 0


def test_unplanted_concept_gives_no_picks_and_no_rolls():
    a = _mc(*[_concept_item(f"q{n}", "9-10.RL.1.A", "9-10.RL.1.A", "9-10.RL.1.A", "9-10.RL.1.A") for n in range(5)])
    rng = _CountingRandom(3)
    out = choose_answers(a, {"9-10.RL.1.B": 3, "4.R.1.B.b": 2}, rng)
    assert [x.choice for x in out] == ["A"] * 5 and rng.rolls == 0


def _concept_quiz():
    return _mc(
        *[_concept_item(f"q{n}", "9-10.RL.1.B", "9-10.RL.1.B", "9-10.RL.1.B", "9-10.RL.1.B") for n in (1, 2, 3)],
        _concept_item("q4", "4.R.1.B.b", "4.R.1.B.b", "4.R.1.B.d", "4.R.1.B.b"),
        _concept_item("q5", "4.R.1.B.d", "4.R.1.B.d", "4.R.1.B.b", "4.R.1.B.d"),
        _concept_item("q6", "4.R.1.B.b", "4.R.1.B.b", "4.R.1.B.b", "4.R.1.B.d"))


def test_mc_expected_labels_are_the_planted_tested_codes_of_a_concept_quiz():
    levels = {"9-10.RL.1.B": 1, "4.R.1.B.d": 2, "4.R.1.B.a": 3, "5.R.1.B.b": 2, "9-10.RL.1.A": 2}
    # 4.R.1.B.a shares the concept but is never tested; 5.R.1.B.b and 9-10.RL.1.A are other concepts
    assert evidenced(_concept_quiz(), "RL", levels) == {"9-10.RL.1.B": 1, "4.R.1.B.d": 2}


def _answers(*choices):
    from agentic.coursedata import ItemAnswer
    return [ItemAnswer(item_id=f"q{n}", choice=c) for n, c in enumerate(choices, 1)]


def test_concept_picks_counts_planted_level_and_picks_per_concept():
    from agentic_lab.synth.evidence import concept_picks
    levels = {"9-10.RL.1.B": 1, "4.R.1.B.d": 2}
    got = concept_picks(_concept_quiz(), levels, _answers("A", "B", "A", "C", "B", "A"))
    assert got == {"9-10.RL.1.B": (1, 1), "4.R.1.B": (2, 2)}


def test_mc_fidelity_flags_planted_concept_with_too_few_picks():
    from agentic_lab.synth.evidence import mc_fidelity
    levels = {"4.R.1.B.d": 2}
    row = mc_fidelity(_concept_quiz(), levels, _answers("A", "A", "A", "C", "A", "A"), "sub-1")
    assert row.submission_id == "sub-1" and row.missing == ["concept:4.R.1.B"] and row.extra == []


def test_mc_fidelity_flags_picks_on_an_unplanted_concept():
    from agentic_lab.synth.evidence import mc_fidelity
    row = mc_fidelity(_concept_quiz(), {}, _answers("B", "A", "A", "A", "A", "A"), "sub-2")
    assert row.missing == [] and row.extra == ["concept:9-10.RL.1.B"]


def test_mc_fidelity_level_one_never_fails_and_a_good_run_passes():
    from agentic_lab.synth.evidence import mc_fidelity
    quiz = _concept_quiz()
    assert mc_fidelity(quiz, {"9-10.RL.1.B": 1}, _answers(*"AAAAAA"), "s") is None
    assert mc_fidelity(quiz, {"9-10.RL.1.B": 1}, _answers(*"BBBAAA"), "s") is None
    levels = {"4.R.1.B.b": 3}
    assert mc_fidelity(quiz, levels, choose_answers(quiz, levels, random.Random(0)), "s") is None


def test_plan_b_quiz_one_four_segment_concept_distractors_carry_the_item_standard():
    """Plan-B shape: one 4-segment concept, every distractor on the item's own standard. A wrong answer is
    "missed the item's standard": level 2 picks every item, level 1 rolls once per item, unplanted never picks."""
    from agentic_lab.synth.evidence import concept_picks, mc_fidelity
    std = "8.RL.1.A"
    quiz = _mc(*[_concept_item(f"q{n}", std, std, std, std) for n in (1, 2, 3)])
    assert {c.standard for i in quiz.items for c in i.choices if not c.correct} == {std}
    developing = choose_answers(quiz, {std: 2}, random.Random(0))
    assert [x.choice for x in developing] == ["B", "B", "B"]
    assert concept_picks(quiz, {std: 2}, developing) == {std: (2, 3)}
    assert mc_fidelity(quiz, {std: 2}, developing, "s") is None
    rng = _CountingRandom(4)
    approaching = choose_answers(quiz, {std: 1}, rng)
    assert rng.rolls == 3 and {x.choice for x in approaching} <= {"A", "B"}
    assert evidenced(quiz, "RL", {std: 1, "9-10.RL.1.A": 2}) == {std: 1}
    assert [x.choice for x in choose_answers(quiz, {}, random.Random(0))] == ["A", "A", "A"]


def test_real_quizzes_pass_mc_fidelity_for_every_single_planted_code():
    """The guard by construction: any one tested code at level 2 or 3 yields >= 2 picks in its concept."""
    from agentic_lab.synth.evidence import mc_fidelity
    for path in sorted(CURRICULA.glob("*.json")):
        a = AssignmentFile.model_validate(json.loads(path.read_text(encoding="utf-8")))
        if a.type != "multiple_choice":
            continue
        codes = {i.standard for i in a.items} | {c.standard for i in a.items for c in i.choices if c.standard}
        for code in codes:
            for level in (2, 3):
                levels = {code: level}
                assert mc_fidelity(a, levels, choose_answers(a, levels, random.Random(0)), "s") is None, (a.id, code)

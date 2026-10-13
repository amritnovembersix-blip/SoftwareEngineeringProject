from agentic.lms.base import Assignment
from agentic.models import StandardRef
from agentic.review.prompts import RULE, analyze_prompt, assess_prompt, assignment_block, judge_prompt
from agentic.review.strategies import IssueOut

PARAS = ["One.", "Two."]
STD = [StandardRef(code="9-10.RL.1.D", concept="Summarize/Theme", description="two or more themes",
                   grade_min=9, grade_max=10)]
ASG = Assignment(id="a1", name="Theme in Animal Farm", description="Choose ONE theme and trace it.")


def test_rule_text_is_verbatim_from_spec():
    assert RULE == ("Use the assignment only to decide which standards, and which parts of each standard, "
                    "the assignment called for. Judge quality only against the standard's own wording. "
                    "Never report a weakness for not following the assignment's instructions. If a "
                    "standard (or part of it) was not called for, or the essay gives no evidence for it, "
                    "answer not_evident.")


def test_assess_with_context_includes_assignment_and_rule():
    system, user = assess_prompt(PARAS, STD, ASG)
    assert RULE in system
    assert "Assignment (context only)" in user and "Theme in Animal Farm" in user and "Choose ONE theme" in user


def test_assess_without_context_has_no_assignment_text():
    system, user = assess_prompt(PARAS, STD, None)
    assert RULE not in system and "Assignment" not in user and "Choose ONE theme" not in system + user
    assert "not_evident" in system and "developing" in system  # verdicts and severities still explained


def test_assignment_without_prompt_gives_no_block():
    assert assignment_block(Assignment(id="a", name="x", description=None)) == ""
    system, user = assess_prompt(PARAS, STD, Assignment(id="a", name="x", description=None))
    assert RULE not in system and "Assignment" not in user


def test_rag_prompts_carry_the_context():
    _, user = analyze_prompt(PARAS, 8, ASG)
    assert "Choose ONE theme" in user
    _, user = judge_prompt([IssueOut(paragraph=1, issue="vague")], {1: STD}, ASG)
    assert "Choose ONE theme" in user


def test_assignment_block_includes_focus_notes():
    asg = Assignment(id="a", name="T", description="Choose ONE theme.",
                     notes={"9-10.RL.1.D": "Assignment asks for one theme only."})
    block = assignment_block(asg)
    assert "Assignment focus by standard:" in block
    assert "- 9-10.RL.1.D: Assignment asks for one theme only." in block

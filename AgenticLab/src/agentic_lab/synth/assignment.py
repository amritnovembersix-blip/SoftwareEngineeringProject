"""Assignment prompts for synthetic essays, one per assignment type (argument, rl, ri)."""
from agentic.dataset import AssignmentInfo

ASSIGNMENT_PROMPT = (
    "Write an argumentative essay of about five paragraphs answering this question: "
    "Should schools start the day later to give students more sleep? "
    "State a claim, support it with reasons and evidence, address an opposing view, "
    "and finish with a conclusion."
)

DEFAULT_TEXTS = {
    "rl": "Animal Farm (George Orwell)",
    "ri": "George Orwell's preface to the Ukrainian edition of Animal Farm (1947)",
}

_TEXT_PROMPTS = {
    "rl": (
        "Write a literary analysis essay of about five paragraphs about {text}. "
        "Make a claim about a theme of the novel (for example, how power corrupts or how language "
        "is used to control the animals), support it by quoting and paraphrasing the text, "
        "explain how the evidence supports your claim, and finish with a conclusion."
    ),
    "ri": (
        "Write an essay of about five paragraphs analyzing the informational text {text}. "
        "Explain the author's central idea and purpose, how he develops his argument, and what the "
        "text reveals about why the novel was written. Cite and explain evidence from the text, "
        "and finish with a conclusion."
    ),
}


def assignment_prompt(assignment: AssignmentInfo | None = None) -> str:
    if assignment is not None and assignment.prompt:
        return assignment.prompt  # the teacher's own wording
    if assignment is None or assignment.type == "argument":
        return ASSIGNMENT_PROMPT
    text = assignment.text or DEFAULT_TEXTS[assignment.type]
    return _TEXT_PROMPTS[assignment.type].format(text=text)

"""Lab composition: production wiring plus the generators' fake responses."""
from agentic import wiring
from agentic.config import Settings
from agentic.llm.base import ChatModel
from agentic_lab.synth.generate_course import (
    EssayOut2, FamilyCheck, FidelityOut, ParagraphOut, ShortAnswerOut)
from agentic_lab.synth.generate_essays import EssayOut


def _fake_essay(_system: str, _user: str) -> EssayOut:
    return EssayOut(paragraphs=[f"Placeholder paragraph {i} of a synthetic essay." for i in (1, 2, 3)])


def build_chat(s: Settings) -> ChatModel:
    """Like agentic.wiring.build_chat, but the fake also writes placeholder essays."""
    if s.fake_llm or s.llm_provider == "fake":
        return wiring.demo_fake_chat({EssayOut: _fake_essay})
    return wiring.build_chat(s)


def _marked(user: str, marker: str) -> list[str]:
    return [line.split()[1].rstrip(":") for line in user.splitlines() if line.startswith(marker + " ")]


def _fake_course_handlers() -> dict:
    """Placeholder writing that always passes the fidelity check (one check per CHECK line)."""
    return {
        EssayOut: _fake_essay,
        ParagraphOut: lambda _s, _u: ParagraphOut(paragraphs=["Placeholder paragraph of a synthetic answer."]),
        EssayOut2: lambda _s, _u: EssayOut2(
            paragraphs=[f"Placeholder paragraph {i} of a synthetic essay." for i in (1, 2, 3, 4)]),
        ShortAnswerOut: lambda _s, u: ShortAnswerOut(
            answers=[{"item_id": i, "text": "Placeholder answer."} for i in _marked(u, "ITEM")]),
        FidelityOut: lambda _s, u: FidelityOut(
            checks=[FamilyCheck(name=n, present=True) for n in _marked(u, "CHECK")], extra_severe=[]),
    }


def build_synth_chat(s: Settings) -> ChatModel:
    """The generate-course chat model (pass `config.synth_settings(...)`); the fake writes placeholders."""
    if s.fake_llm or s.llm_provider == "fake":
        return wiring.demo_fake_chat(_fake_course_handlers())
    return wiring.build_chat(s)

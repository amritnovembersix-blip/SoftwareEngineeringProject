"""Lab composition: production wiring plus the essay generator's fake response."""
from agentic import wiring
from agentic.config import Settings
from agentic.llm.base import ChatModel
from agentic_lab.synth.generate_essays import EssayOut


def _fake_essay(_system: str, _user: str) -> EssayOut:
    return EssayOut(paragraphs=[f"Placeholder paragraph {i} of a synthetic essay." for i in (1, 2, 3)])


def build_chat(s: Settings) -> ChatModel:
    """Like agentic.wiring.build_chat, but the fake also writes placeholder essays."""
    if s.fake_llm or s.llm_provider == "fake":
        return wiring.demo_fake_chat({EssayOut: _fake_essay})
    return wiring.build_chat(s)

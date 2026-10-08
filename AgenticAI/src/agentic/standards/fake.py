"""In-memory standards (no database) for tests and the fake-LLM demo."""
import math

from agentic.llm.base import Embedder
from agentic.models import StandardRef


def _text(s: StandardRef) -> str:
    return f"{s.concept}: {s.description}".strip()


def _cosine_distance(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return 1.0 - dot / (na * nb)


class InMemoryStandards:
    def __init__(self, rows: list[StandardRef], embedder: Embedder):
        self._rows = list(rows)
        self._vectors = embedder.embed([_text(r) for r in self._rows], "document") if self._rows else []

    def at_grade(self, grade: int) -> list[StandardRef]:
        return sorted((r for r in self._rows if r.grade_min <= grade <= r.grade_max),
                      key=lambda r: r.code)

    def search(self, query_embedding: list[float], grade: int, k: int) -> list[StandardRef]:
        scored = [(_cosine_distance(query_embedding, v), r)
                  for r, v in zip(self._rows, self._vectors)
                  if r.grade_min <= grade <= r.grade_max]
        scored.sort(key=lambda t: (t[0], t[1].code))
        return [r for _, r in scored[:k]]


def _s(code: str, concept: str, description: str) -> StandardRef:
    return StandardRef(code=code, concept=concept, description=description, grade_min=9, grade_max=10)


def demo_standards() -> list[StandardRef]:
    """Grade 9-10 writing standards, copied from the MLS ELA corpus (sub expectation appended)."""
    return [
        _s('9-10.W.1.A.a', 'Research',
           'Conduct research to answer a question (including a self-generated question) or solve a problem; narrow or broaden the inquiry when appropriate; gather multiple relevant, credible sources, print and digital; integrate information using a standard citation system.'),
        _s('9-10.W.1.A.b', 'Research',
           'Gather relevant information from multiple authoritative print and digital sources, using advanced searches effectively; assess the usefulness of each source in answering the research question; integrate information into the text selectively to maintain the flow of ideas, avoiding plagiarism and following a standard format for citation.'),
        _s('9-10.W.2.A', 'Development',
           'Follow a writing process to produce clear and coherent writing in which the development, organization, style, and voice are appropriate to the task, purpose and audience; self-select and blend (when appropriate) previously learned narrative, expository, and argumentative writing techniques.'),
        _s('9-10.W.3.A.a', 'Revise and Edit',
           "Review, revise, and edit writing with consideration for the task, purpose, and audience. Organization and Content: Introduce the topic, maintain a clear focus throughout the text,and provide a conclusion that follows from the text.  Achieve the writer's purpose and demonstrate an awareness of audience by making choices regarding organization and content."),
        _s('9-10.W.3.A.b', 'Revise and Edit',
           'Review, revise, and edit writing with consideration for the task, purpose, and audience. Word choice, syntax, and style: Choose precise language and make syntactical choices to reflect an understanding of how language contributes to meaning'),
        _s('9-10.W.3.A.c', 'Revise and Edit',
           'Review, revise, and edit writing with consideration for the task, purpose, and audience. Conventions of standard English and usage: Demonstrate a command of the conventions of standard English grammar and usage including spelling and punctuation'),
        _s('9-10.W.3.A.d', 'Revise and Edit',
           'Review, revise, and edit writing with consideration for the task, purpose, and audience. Use a variety of appropriate transitions to clarify relationships and connect ideas, claims and signal time shifts'),
        _s('9-10.W.3.A.e', 'Revise and Edit',
           'Review, revise, and edit writing with consideration for the task, purpose, and audience. Use technology, including the Internet, to produce, publish, and update individual or shared writing products, taking advantage of technology’s capacity to link to other information and to display information flexibly and dynamically'),
    ]

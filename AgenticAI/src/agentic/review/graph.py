"""LangGraph workflow: fetch -> number_paragraphs -> select_and_assess -> aggregate -> verify."""
import time
from collections.abc import Iterator

from langgraph.graph import END, START, StateGraph

from agentic.decisions.base import DecisionModel
from agentic.lms.base import LMSClient
from agentic.models import ReviewResult, SubmissionRef
from agentic.review import nodes
from agentic.review.state import ReviewState
from agentic.review.strategies import StandardsStrategy


def _timed(name, fn):
    def wrapper(state):
        start = time.perf_counter()
        update = fn(state)
        return {**update, "timings": {name: time.perf_counter() - start}}
    return wrapper


def build_graph(lms: LMSClient, strategy: StandardsStrategy, decision: DecisionModel | None,
                miss_threshold: float, use_context: bool = True):
    steps = [
        ("fetch", nodes.make_fetch(lms, use_context)),
        ("number_paragraphs", nodes.make_number_paragraphs()),
        ("select_and_assess", nodes.make_select_and_assess(strategy)),
        ("aggregate", nodes.make_aggregate()),
        ("verify", nodes.make_verify(decision, miss_threshold)),
    ]
    g = StateGraph(ReviewState)
    for name, fn in steps:
        g.add_node(name, _timed(name, fn))
    g.add_edge(START, steps[0][0])
    for (a, _), (b, _) in zip(steps, steps[1:]):
        g.add_edge(a, b)
    g.add_edge(steps[-1][0], END)
    return g.compile()


class Reviewer:
    def __init__(self, graph, strategy_name: str, models: dict | None = None,
                 strands: list[str] | None = None, targets: list[str] | None = None,
                 assignment_context: bool = True):
        self.models = models
        self.assignment_context = assignment_context
        self.strands = strands or ["W"]
        self.targets = targets or []
        self._graph = graph
        self._strategy_name = strategy_name
        self.last_result: ReviewResult | None = None

    def stream(self, ref: SubmissionRef) -> Iterator[tuple[str, float]]:
        self.last_result = None
        final: dict = {}
        for mode, chunk in self._graph.stream(
            {"submission_ref": ref, "dropped": [], "timings": {}}, stream_mode=["updates", "values"]
        ):
            if mode == "values":
                final = chunk
            else:
                for node, update in chunk.items():
                    yield node, update["timings"][node]
        state = final
        self.last_result = ReviewResult(
            submission_ref=state["submission_ref"],
            course_grade=state["course_grade"],
            strategy=state.get("strategy", self._strategy_name),
            standards=state["standards"],
            observations=state["observations"],
            findings=state["findings"],
            met_standards=state["met_standards"],
            not_evident=state.get("not_evident", []),
            dropped=state["dropped"],
            verification=state["verification"],
            timings=state["timings"],
            llm_calls=state["llm_calls"],
            trace=state["trace"],
        )

    def run(self, ref: SubmissionRef) -> ReviewResult:
        for _ in self.stream(ref):
            pass
        return self.last_result

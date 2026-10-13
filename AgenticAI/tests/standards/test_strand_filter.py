from agentic.llm.fake import FakeEmbedder
from agentic.models import StandardRef
from agentic.standards.fake import InMemoryStandards
from agentic.standards.strand_filter import StrandFilter, strand_of


def _std(code: str) -> StandardRef:
    return StandardRef(code=code, concept=f"concept {code}", description=f"desc {code}",
                       grade_min=9, grade_max=10)


ROWS = [_std(c) for c in ("9-10.W.1.A.a", "9-10.W.2.A", "9-10.RL.1.A", "9-10.RL.1.B",
                          "9-10.RI.1.A", "9-10.RI.1.B", "9-10.RI.1.C")]


def test_strand_of_reads_the_second_code_part():
    assert strand_of("9-10.RL.1.A") == "RL"
    assert strand_of("3.W.2.A.b") == "W"


def test_at_grade_keeps_only_chosen_strands():
    f = StrandFilter(InMemoryStandards(ROWS, FakeEmbedder()), ["W", "RL"])
    assert [s.code for s in f.at_grade(9)] == ["9-10.RL.1.A", "9-10.RL.1.B", "9-10.W.1.A.a", "9-10.W.2.A"]


def test_search_returns_k_from_chosen_strands_even_when_others_rank_higher():
    emb = FakeEmbedder()
    inner = InMemoryStandards(ROWS, emb)
    f = StrandFilter(inner, ["W"])
    # query with an RI standard's own text: RI rows rank first in the inner source
    q = emb.embed(["concept 9-10.RI.1.A: desc 9-10.RI.1.A"], "query")[0]
    got = f.search(q, 9, 2)
    assert len(got) == 2 and all(strand_of(s.code) == "W" for s in got)


def test_writing_is_always_included():
    f = StrandFilter(InMemoryStandards(ROWS, FakeEmbedder()), ["RI"])
    assert {strand_of(s.code) for s in f.at_grade(9)} == {"W", "RI"}
    assert f.strands == ["W", "RI"]


# --- TargetFilter: assignment targets as the outer boundary ----------------------------------

from agentic.standards.strand_filter import TargetFilter  # noqa: E402


def test_target_filter_keeps_only_targets():
    f = TargetFilter(InMemoryStandards(ROWS, FakeEmbedder()), ["9-10.RL.1.B", "9-10.W.2.A"])
    assert [s.code for s in f.at_grade(9)] == ["9-10.RL.1.B", "9-10.W.2.A"]


def test_target_filter_ignores_codes_missing_from_the_source():
    f = TargetFilter(InMemoryStandards(ROWS, FakeEmbedder()), ["9-10.W.2.A", "9-10.RL.9.Z"])
    assert [s.code for s in f.at_grade(9)] == ["9-10.W.2.A"]


def test_target_filter_search_returns_only_targets():
    emb = FakeEmbedder()
    f = TargetFilter(InMemoryStandards(ROWS, emb), ["9-10.W.1.A.a", "9-10.W.2.A"])
    q = emb.embed(["concept 9-10.RI.1.A: desc 9-10.RI.1.A"], "query")[0]
    assert {s.code for s in f.search(q, 9, 2)} == {"9-10.W.1.A.a", "9-10.W.2.A"}


def test_target_filter_stacks_on_strand_filter():
    inner = StrandFilter(InMemoryStandards(ROWS, FakeEmbedder()), ["W"])
    f = TargetFilter(inner, ["9-10.RL.1.A", "9-10.W.2.A"])
    assert [s.code for s in f.at_grade(9)] == ["9-10.W.2.A"]

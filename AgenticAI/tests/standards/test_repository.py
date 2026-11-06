import pytest
from fakes import clear_standards, std_row, xlsx_fixture

from agentic.llm.fake import FakeEmbedder
from agentic.standards.loader import embed_text, load, normalize_rows, read_xlsx
from agentic.standards.repository import PostgresStandards

pytestmark = pytest.mark.db


@pytest.fixture
def repo(db_conn, tmp_path):
    clear_standards(db_conn)  # the dev DB may hold real standards and saved reviews
    specs = [std_row(f"9-10.W.{i}", concept=f"c{i}", description=f"d{i}") for i in range(5)]
    specs += [std_row("8.W.1", grade="8", description="eight"),
              std_row("11-12.W.1", grade="11 to 12", description="eleven")]
    rows = normalize_rows(read_xlsx(xlsx_fixture(tmp_path, specs)))
    emb = FakeEmbedder()
    load(db_conn, rows, emb)
    return PostgresStandards(db_conn), rows, emb


def test_at_grade_returns_only_band(repo):
    r, _, _ = repo
    assert [s.code for s in r.at_grade(9)] == [f"9-10.W.{i}" for i in range(5)]
    assert [s.code for s in r.at_grade(8)] == ["8.W.1"]
    assert r.at_grade(2) == []


def test_search_returns_at_most_k_within_grade(repo):
    r, rows, emb = repo
    target = next(x for x in rows if x.code == "9-10.W.3")
    q = emb.embed([embed_text(target)], "query")[0]
    hits = r.search(q, grade=9, k=3)
    assert len(hits) == 3
    assert hits[0].code == "9-10.W.3"
    assert all(h.grade_min <= 9 <= h.grade_max for h in hits)
    assert len(r.search(q, grade=8, k=3)) == 1


def test_in_range_matches_the_in_memory_source(repo):
    from agentic.standards.fake import InMemoryStandards
    from agentic.models import StandardRef

    r, rows, emb = repo
    refs = [StandardRef(code=x.code, concept=x.concept or "", description=x.description,
                        grade_min=x.grade_min, grade_max=x.grade_max, strand=x.strand_code,
                        sub_expectation=x.sub_expectation) for x in rows]
    mem = InMemoryStandards(refs, emb)
    for lo, hi in ((8, 8), (9, 10), (8, 10), (3, 12)):
        assert r.in_range(lo, hi) == mem.in_range(lo, hi)
    assert [s.code for s in r.in_range(9, 10)] == [f"9-10.W.{i}" for i in range(5)]
    assert r.in_range(9, 10)[0].strand == "W"

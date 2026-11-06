"""Demo reviews built from the answer key: rows the team can build against, labelled as demo data."""
import pytest

from agentic.models import word_count
from agentic.tuning import load_conditions
from agentic_lab.config import lab_settings
from agentic_lab.fill_demo import REVIEWER, demo_results, fill_demo
from agentic_lab.synth.families import load_families


@pytest.fixture
def results():
    s = lab_settings(None)
    families = load_families(s.data_dir / "skill_families.json")
    return list(demo_results(s.data_dir, "2.00-control", 3, families, load_conditions(s.config_dir)))


def test_one_result_per_submission_through_the_week(results):
    assert len(results) == 18 * 6  # 2 assignments a week, weeks 1-3
    assert len({r.submission_ref.submission_id for r in results}) == len(results)


def test_every_row_is_labelled_and_fits_the_schema(results):
    for r in results:
        assert r.pipeline == "v2" and r.models == {REVIEWER: "answer-key"}
        assert all(v.reviewer == REVIEWER for v in r.verdicts)
        assert all(1 <= word_count(o.reason) <= 12 for o in r.observations)
        assert all(f.summary.startswith("Demo data") and 1 <= word_count(f.summary) <= 40 for f in r.findings)
        codes = [v.standard_code for v in r.verdicts]
        assert len(codes) == len(set(codes))
        assert {f.standard_code for f in r.findings} == {
            v.standard_code for v in r.verdicts if v.verdict == "growth_area"}


def test_links_join_findings_of_the_same_submission_down_to_grades_3_to_5(results):
    links = [(r, k) for r in results for k in r.links]
    assert links, "layered students should produce root-cause links"
    for r, k in links:
        found = {f.standard_code for f in r.findings}
        assert k.finding_code in found and k.root_code in found
        assert int(k.root_code.split(".")[0]) <= 5


def test_students_differ_and_some_have_strengths(results):
    assert len({len(r.findings) for r in results}) > 1
    assert any(v.notable_strength for r in results for v in r.verdicts)


@pytest.mark.db
def test_fill_gives_the_team_rows_in_every_view():
    import psycopg

    s = lab_settings()
    try:
        psycopg.connect(s.database_url_local, connect_timeout=2).close()
    except psycopg.OperationalError:
        pytest.skip("Postgres is not running")
    first = fill_demo(s, through_week=3, reset=True)
    assert first.saved == 2 * 18 * 6 and first.needs_attention > 0 and first.groups > 0
    again = fill_demo(s, through_week=3)
    assert again.saved == 0 and again.already_there == first.saved  # safe to run twice
    with psycopg.connect(s.database_url_local) as c:
        for view in ("concept_status_latest", "class_concept_week_latest", "reteach_group_latest", "peer_pair_latest"):
            assert c.execute(f"SELECT count(*) FROM {view}").fetchone()[0] > 0, view
        assert c.execute("SELECT count(*) FROM finding_link").fetchone()[0] > 0
    fill_demo(s, through_week=0, reset=True)  # leave the test database without reviews


def test_standards_are_found_through_the_package_not_a_folder_name():
    """The lab sits next to agentic/ here but next to AgenticAI/ in the team repo."""
    from agentic.config import PACKAGE_ROOT
    from agentic_lab.standards_v2 import REVIEWABILITY_CSV

    assert REVIEWABILITY_CSV == PACKAGE_ROOT / "standards" / "reviewability-v2.csv"
    assert REVIEWABILITY_CSV.exists()

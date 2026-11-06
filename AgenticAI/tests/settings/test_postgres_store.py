import pytest

from agentic.config import Settings
from agentic.settings.base import effective, load_defs
from agentic.settings.postgres_store import PostgresSettingsStore

pytestmark = pytest.mark.db


@pytest.fixture
def defs():
    return load_defs(Settings.load().config_dir)


def test_append_and_read_back(db_conn, defs):
    store = PostgresSettingsStore(db_conn, defs)
    before = len(store.overrides())
    row = store.append("mc_finding_min_picks", 3, "t1")
    assert (row.key, row.value, row.changed_by) == ("mc_finding_min_picks", 3, "t1")
    assert row.changed_at.tzinfo is not None
    rows = store.overrides()
    assert len(rows) == before + 1
    assert effective(defs, rows)["mc_finding_min_picks"] == 3


def test_latest_row_wins_and_booleans_round_trip(db_conn, defs):
    store = PostgresSettingsStore(db_conn, defs)
    store.append("root_cause_enabled", False, "t1")
    store.append("mc_finding_min_picks", 3, "t1")
    store.append("mc_finding_min_picks", 4, "t2")
    eff = effective(defs, store.overrides())
    assert eff["mc_finding_min_picks"] == 4 and eff["root_cause_enabled"] is False


def test_append_validates_before_inserting(db_conn, defs):
    store = PostgresSettingsStore(db_conn, defs)
    before = len(store.overrides())
    with pytest.raises(ValueError, match="mc_finding_min_picks"):
        store.append("mc_finding_min_picks", 0, "t1")
    with pytest.raises(ValueError, match="not available"):
        store.append("extension_check_enabled", True, "t1")
    with pytest.raises(ValueError, match="unknown"):
        store.append("nope", 1, "t1")
    assert len(store.overrides()) == before

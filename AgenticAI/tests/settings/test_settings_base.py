from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from agentic.settings.base import (InMemorySettingsStore, OverrideRow, SettingDef, effective, load_defs,
                                   settings_hash, validate, validate_all)

CONFIG = Path(__file__).resolve().parents[2] / "config"
T0 = datetime(2026, 10, 29, tzinfo=timezone.utc)


ANALYTICS = {"attention_hits": 2, "attention_window": 3, "secure_hits": 3, "secure_window": 4,
             "trend_window": 3, "review_flip_rate": 0.10, "group_min_size": 2, "max_tutees": 2}


def _defs():
    return load_defs(CONFIG)


def test_defaults_load():
    defs = _defs()
    assert set(defs) == {"mc_finding_min_picks", "root_cause_enabled",
                         "notable_strengths_enabled", "extension_check_enabled", *ANALYTICS}
    assert effective(defs, []) == {"mc_finding_min_picks": 2, "root_cause_enabled": True,
                                   "notable_strengths_enabled": True, "extension_check_enabled": False,
                                   **ANALYTICS}
    assert defs["extension_check_enabled"].available is False
    assert defs["mc_finding_min_picks"].scope == "system"


@pytest.mark.parametrize("bad", [0, 11, True, "3", 2.5])
def test_picks_out_of_range_or_wrong_type_rejected(bad):
    with pytest.raises(ValueError, match="mc_finding_min_picks"):
        validate(_defs()["mc_finding_min_picks"], bad)


def test_picks_in_range_accepted():
    assert validate(_defs()["mc_finding_min_picks"], 10) == 10


def test_bool_type_rejected():
    with pytest.raises(ValueError, match="root_cause_enabled"):
        validate(_defs()["root_cause_enabled"], "yes")


def test_unavailable_setting_rejects_non_default():
    d = _defs()["extension_check_enabled"]
    with pytest.raises(ValueError, match="not available"):
        validate(d, True)
    assert validate(d, False) is False


def test_choices_enforced():
    d = SettingDef(key="mode", type="str", default="a", choices=["a", "b"], description="x")
    assert validate(d, "b") == "b"
    with pytest.raises(ValueError, match="mode"):
        validate(d, "c")


def test_latest_override_wins_and_unknown_key_ignored():
    defs = _defs()
    clock = iter(T0 + timedelta(minutes=i) for i in range(10))
    store = InMemorySettingsStore(defs, clock=lambda: next(clock))
    store.append("mc_finding_min_picks", 3, "t1")
    row = store.append("mc_finding_min_picks", 4, "t2")
    assert row.changed_by == "t2" and row.changed_at.tzinfo is not None
    rows = store.overrides() + [OverrideRow("no_such_key", 1, "x", T0 + timedelta(days=1))]
    assert effective(defs, rows)["mc_finding_min_picks"] == 4
    assert "no_such_key" not in effective(defs, rows)


def test_ties_resolved_by_insertion_order():
    defs = _defs()
    store = InMemorySettingsStore(defs, clock=lambda: T0)
    store.append("mc_finding_min_picks", 3, "a")
    store.append("mc_finding_min_picks", 5, "b")
    assert effective(defs, store.overrides())["mc_finding_min_picks"] == 5


def test_append_validates_and_stores_nothing_on_failure():
    store = InMemorySettingsStore(_defs())
    with pytest.raises(ValueError):
        store.append("mc_finding_min_picks", 99, "t")
    with pytest.raises(ValueError, match="unknown"):
        store.append("nope", 1, "t")
    assert store.overrides() == []


def test_hash_stable_across_ordering():
    a = {"x": 1, "y": True}
    assert settings_hash(a) == settings_hash({"y": True, "x": 1})
    assert settings_hash(a) != settings_hash({"x": 2, "y": True})
    assert len(settings_hash(a)) == 64


def test_malformed_def_names_file_and_key(tmp_path):
    (tmp_path / "review-settings.toml").write_text('[bad]\ntype = "date"\ndefault = 1\ndescription = "d"\n')
    with pytest.raises(ValueError, match=r"review-settings\.toml.*bad"):
        load_defs(tmp_path)
    (tmp_path / "review-settings.toml").write_text('[bad]\ntype = "int"\ndefault = "x"\ndescription = "d"\n')
    with pytest.raises(ValueError, match=r"review-settings\.toml.*bad"):
        load_defs(tmp_path)


def test_float_setting_accepts_int_or_float_in_range():
    d = _defs()["review_flip_rate"]
    assert d.type == "float" and d.min == 0.01 and d.max == 0.35
    assert validate(d, 0.2) == 0.2
    assert validate(d, 0.35) == 0.35


@pytest.mark.parametrize("bad", [0, 0.36, 0.6, 0.005, True, "0.1", float("nan"), float("inf")])
def test_float_setting_rejects_out_of_range_or_wrong_type(bad):
    with pytest.raises(ValueError, match="review_flip_rate"):
        validate(_defs()["review_flip_rate"], bad)


def test_validate_all_defaults_are_clean():
    defs = _defs()
    assert validate_all(defs, effective(defs, [])) == []


@pytest.mark.parametrize("hits,window", [("attention_hits", "attention_window"), ("secure_hits", "secure_window")])
def test_validate_all_hits_larger_than_window(hits, window):
    defs = _defs()
    eff = effective(defs, [])
    eff[hits] = eff[window] + 1
    errs = validate_all(defs, eff)
    assert len(errs) == 1 and hits in errs[0] and window in errs[0]
    eff[hits] = eff[window]
    assert validate_all(defs, eff) == []

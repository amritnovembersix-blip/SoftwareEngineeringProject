from pathlib import Path

import pytest

from agentic.config import PACKAGE_ROOT, Settings
from agentic.standards.loader import normalize_rows, read_xlsx
from agentic.tuning import (
    load_conditions, load_pricing, load_profile, load_prompt, plan_reviewers, tuning_hash, validate_tuning,
)

CONFIG = PACKAGE_ROOT / "config"
PROMPTS = PACKAGE_ROOT / "prompts"
CATALOG = PACKAGE_ROOT / "standards" / "mls-ela-reviewable-v2.xlsx"
PROFILES = ["full", "lean", "graded"]


@pytest.fixture(scope="module")
def catalog():
    return normalize_rows(read_xlsx(CATALOG))


def _band_top(grade: int) -> int:
    return {9: 10, 10: 10, 11: 12, 12: 12}.get(grade, grade)


def test_settings_default_dirs_point_at_the_package():
    s = Settings.load(env_file=None)
    assert s.config_dir == CONFIG and s.prompts_dir == PROMPTS


@pytest.mark.parametrize("name", PROFILES)
def test_profiles_load_and_prompts_exist(name):
    profile = load_profile(CONFIG, name)
    assert profile.name == name and profile.reviewers
    for spec in profile.reviewers:
        assert load_prompt(PROMPTS, spec.prompt)


def test_full_profile_reviewers():
    ids = {r.id: r for r in load_profile(CONFIG, "full").reviewers}
    assert set(ids) == {"3-5-language", "3-5-writing", "3-5-reading", "6+-writing", "6+-reading"}
    assert ids["6+-reading"].strands == ("RL", "RI") and ids["6+-reading"].grades == (6, "course")
    assert ids["3-5-writing"].mode == "per_standard" and ids["6+-writing"].mode == "progression"


def test_graded_profile_reviewers():
    ids = {r.id: r for r in load_profile(CONFIG, "graded").reviewers}
    per_grade = {f"{g}-{name}" for g in (3, 4, 5) for name in ("language", "writing", "reading")}
    assert set(ids) == per_grade | {"6+-writing", "6+-reading"}
    for g in (3, 4, 5):
        for name, strand in (("language", "L"), ("writing", "W"), ("reading", "R")):
            r = ids[f"{g}-{name}"]
            assert (r.grades, r.strands, r.mode, r.prompt, r.reasoning) == (
                (g, g), (strand,), "per_standard", "per_standard.v2", "low")  # S1-10
    assert ids["6+-reading"].strands == ("RL", "RI") and ids["6+-writing"].strands == ("W",)
    for k in ("6+-writing", "6+-reading"):
        assert ids[k].grades == (6, "course") and ids[k].reasoning == "high"
        assert (ids[k].mode, ids[k].prompt) == ("search", "progression.v3")  # S1-1


def test_graded_plan_grade_4_drops_grade_5_and_6plus():
    plan = plan_reviewers(load_profile(CONFIG, "graded"), 4)
    assert {spec.id for spec, _, _ in plan} == {f"{g}-{n}" for g in (3, 4) for n in ("language", "writing", "reading")}


def test_graded_plan_grade_9_has_nine_per_standard_reviewers_plus_6plus():
    plan = plan_reviewers(load_profile(CONFIG, "graded"), 9)
    assert sum(spec.mode == "per_standard" for spec, _, _ in plan) == 9
    assert {spec.id: hi for spec, _, hi in plan if spec.mode == "search"} == {"6+-writing": 10, "6+-reading": 10}


def test_unknown_profile_fails():
    with pytest.raises(ValueError):
        load_profile(CONFIG, "nope")


@pytest.mark.parametrize("name", PROFILES)
@pytest.mark.parametrize("course_grade", range(3, 13))
def test_every_reviewable_code_lands_in_exactly_one_reviewer(catalog, name, course_grade):
    plan = plan_reviewers(load_profile(CONFIG, name), course_grade)
    for row in catalog:
        if row.grade_min > course_grade:
            continue
        owners = [spec.id for spec, lo, hi in plan
                  if lo <= row.grade_min <= hi and row.strand_code in spec.strands]
        assert len(owners) == 1, (row.code, course_grade, owners)


def test_plan_grade_4_has_no_progression_and_caps_the_low_band():
    plan = plan_reviewers(load_profile(CONFIG, "full"), 4)
    assert all(spec.mode == "per_standard" for spec, _, _ in plan)
    assert plan and all(hi == 4 for _, _, hi in plan)


def test_plan_grade_11_band_top_is_12():
    plan = {spec.id: (lo, hi) for spec, lo, hi in plan_reviewers(load_profile(CONFIG, "full"), 11)}
    assert plan["6+-writing"] == (6, 12) and plan["6+-reading"] == (6, 12)
    assert plan["3-5-writing"] == (3, 5)


def test_plan_grade_9_band_top_is_10():
    plan = {spec.id: hi for spec, _, hi in plan_reviewers(load_profile(CONFIG, "full"), 9)}
    assert plan["6+-writing"] == 10


def test_conditions_keys_are_concepts_of_catalog_codes(catalog):
    concepts = {".".join(r.code.split(".")[1:4]) for r in catalog}
    conditions = load_conditions(CONFIG)
    assert {"L.1.A", "L.1.B", "W.3.A", "W.2.A", "RL.1.A", "RI.1.A", "R.1.A"} <= set(conditions)
    for key, items in conditions.items():
        assert key in concepts, key
        assert 3 <= len(items) <= 6


def test_pricing_loads_as_pairs():
    pricing = load_pricing(CONFIG)
    assert pricing and all(len(v) == 2 and all(isinstance(x, float) for x in v) for v in pricing.values())


def test_missing_prompt_file_names_the_ref():
    with pytest.raises(FileNotFoundError, match="nope.v9"):
        load_prompt(PROMPTS, "nope.v9")


def test_prompt_templates_have_the_placeholders():
    for ref in ("per_standard.v1", "progression.v1", "root_cause.v1"):
        text = load_prompt(PROMPTS, ref)
        for name in ("$standards", "$segments", "$assignment", "$conditions"):
            assert name in text, (ref, name)


def test_tuning_hash_changes_with_prompt_text():
    profile = load_profile(CONFIG, "full")
    conditions = load_conditions(CONFIG)
    a = tuning_hash(profile, conditions, {"per_standard.v1": "one"})
    assert a == tuning_hash(profile, conditions, {"per_standard.v1": "one"})
    assert a != tuning_hash(profile, conditions, {"per_standard.v1": "two"})
    assert a != tuning_hash(load_profile(CONFIG, "lean"), conditions, {"per_standard.v1": "one"})


def test_invalid_review_pipeline_fails_at_load(monkeypatch):
    monkeypatch.setenv("REVIEW_PIPELINE", "v3")
    with pytest.raises(ValueError, match="REVIEW_PIPELINE"):
        Settings.load(env_file=None)


def test_invalid_bands_profile_fails_at_load(monkeypatch):
    monkeypatch.setenv("BANDS_PROFILE", "huge")
    with pytest.raises(ValueError, match="BANDS_PROFILE"):
        Settings.load(env_file=None)


def test_new_settings_defaults_and_limits(monkeypatch):
    for n in ("REVIEW_PIPELINE", "BANDS_PROFILE", "LLM_TPM", "LLM_RPD"):
        monkeypatch.delenv(n, raising=False)
    s = Settings.load(env_file=None)
    assert (s.review_pipeline, s.bands_profile, s.llm_tpm, s.llm_rpd) == ("v2", "graded", None, None)
    monkeypatch.setenv("LLM_TPM", "250000")
    monkeypatch.setenv("LLM_RPD", "")
    s = Settings.load(env_file=None)
    assert s.llm_tpm == 250000 and s.llm_rpd is None


# --- fail-fast validation: errors name the file and key path ---

def _tuning_copy(tmp_path):
    import shutil
    cfg, prompts = tmp_path / "config", tmp_path / "prompts"
    shutil.copytree(CONFIG, cfg)
    shutil.copytree(PROMPTS, prompts)
    return cfg, prompts


def _reviewer(**over):
    r = {"id": "r1", "grades": [3, 5], "strands": ["W"], "mode": "per_standard", "prompt": "per_standard.v1"}
    r.update(over)
    return r


def _write_bands(cfg, reviewer_lines):
    (cfg / "bands.toml").write_text(
        "[profiles.full]\n[[profiles.full.reviewers]]\n" + reviewer_lines, encoding="utf-8")


def _lines(**over):
    r = _reviewer(**over)
    r = {k: v for k, v in r.items() if v is not None}
    import json
    return "\n".join(f"{k} = {json.dumps(v)}" for k, v in r.items()) + "\n"


def test_search_mode_loads_and_an_unknown_mode_names_the_choices(tmp_path):
    cfg, prompts = _tuning_copy(tmp_path)
    _write_bands(cfg, _lines(mode="search", prompt="progression.v3"))
    assert load_profile(cfg, "full").reviewers[0].mode == "search"
    _write_bands(cfg, _lines(mode="walk"))
    with pytest.raises(ValueError, match="per_standard, progression or search"):
        load_profile(cfg, "full")


@pytest.mark.parametrize("mode, prompt, ok", [
    ("search", "progression.v3", True), ("search", "progression.v4", True), ("search", "progression.v2", False),
    ("search", "per_standard.v3", False), ("progression", "progression.v1", True),
    ("progression", "progression.v2", True), ("progression", "progression.v3", False),
    ("per_standard", "per_standard.v1", True)])
def test_mode_and_prompt_pairing(mode, prompt, ok):
    from agentic.tuning import check_mode_prompt
    if ok:
        check_mode_prompt("6+-writing", mode, prompt)
    else:
        with pytest.raises(ValueError, match=rf"6\+-writing.*{prompt}"):
            check_mode_prompt("6+-writing", mode, prompt)


def test_validate_tuning_rejects_a_search_reviewer_on_an_old_prompt(tmp_path):
    from agentic.tuning import validate_tuning
    cfg, prompts = _tuning_copy(tmp_path)
    _write_bands(cfg, _lines(mode="search", prompt="progression.v2"))
    with pytest.raises(ValueError, match=r"r1.*progression\.v2"):
        validate_tuning(cfg, prompts, "full")


@pytest.mark.parametrize("key", ["id", "strands", "mode", "prompt", "grades"])
def test_missing_reviewer_key_names_file_profile_and_key(tmp_path, key):
    cfg, prompts = _tuning_copy(tmp_path)
    _write_bands(cfg, _lines(**{key: None}))
    with pytest.raises(ValueError, match=rf"bands\.toml profiles\.full.*missing key '{key}'"):
        load_profile(cfg, "full")


@pytest.mark.parametrize("grades", [[3], [3, 5, 6], [5, 3], ["a", 5], [3, "later"]])
def test_bad_grades_name_the_reviewer(tmp_path, grades):
    cfg, _ = _tuning_copy(tmp_path)
    _write_bands(cfg, _lines(grades=grades))
    with pytest.raises(ValueError, match=r"bands\.toml profiles\.full\.r1: .*grades"):
        load_profile(cfg, "full")


@pytest.mark.parametrize("strands", [[], ["X"], "W"])
def test_bad_strands_name_the_reviewer(tmp_path, strands):
    cfg, _ = _tuning_copy(tmp_path)
    _write_bands(cfg, _lines(strands=strands))
    with pytest.raises(ValueError, match=r"bands\.toml profiles\.full\.r1: .*strands"):
        load_profile(cfg, "full")


def test_bad_mode_names_the_reviewer(tmp_path):
    cfg, _ = _tuning_copy(tmp_path)
    _write_bands(cfg, _lines(mode="both"))
    with pytest.raises(ValueError, match=r"bands\.toml profiles\.full\.r1: .*mode"):
        load_profile(cfg, "full")


@pytest.mark.parametrize("bad", ['"x" = 1.5', '"x" = [1.0]', '"x" = [1.0, "a"]', '"x" = [1, 2, 3]'])
def test_malformed_pricing_names_file_and_model(tmp_path, bad):
    cfg, _ = _tuning_copy(tmp_path)
    (cfg / "pricing.toml").write_text(bad + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match=r"pricing\.toml.*'x'"):
        load_pricing(cfg)


@pytest.mark.parametrize("bad", ['"L.1.A" = "text"', '"L.1.A" = [1, 2, 3]', '"L.1.A" = []'])
def test_malformed_conditions_name_file_and_key(tmp_path, bad):
    cfg, _ = _tuning_copy(tmp_path)
    (cfg / "conditions.toml").write_text(bad + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match=r"conditions\.toml.*'L\.1\.A'"):
        load_conditions(cfg)


def test_toml_syntax_error_names_the_file(tmp_path):
    cfg, _ = _tuning_copy(tmp_path)
    (cfg / "bands.toml").write_text("[profiles.full\n", encoding="utf-8")
    with pytest.raises(ValueError, match=r"bands\.toml"):
        load_profile(cfg, "full")


def test_validate_tuning_checks_every_referenced_prompt(tmp_path):
    from agentic.tuning import validate_tuning
    cfg, prompts = _tuning_copy(tmp_path)
    validate_tuning(cfg, prompts, "full")
    (prompts / "progression.v2.md").unlink()
    with pytest.raises(FileNotFoundError, match=r"progression\.v2"):
        validate_tuning(cfg, prompts, "full")


def test_validate_tuning_rejects_unknown_profile(tmp_path):
    from agentic.tuning import validate_tuning
    cfg, prompts = _tuning_copy(tmp_path)
    with pytest.raises(ValueError, match="nope"):
        validate_tuning(cfg, prompts, "nope")


def test_strands_are_a_tuple():
    assert all(isinstance(r.strands, tuple) for r in load_profile(CONFIG, "full").reviewers)


def test_settings_load_validates_tuning_for_v2(monkeypatch, tmp_path):
    cfg, prompts = _tuning_copy(tmp_path)
    (prompts / "per_standard.v2.md").unlink()  # the default profile (graded) uses v2
    monkeypatch.setenv("CONFIG_DIR", str(cfg))
    monkeypatch.setenv("PROMPTS_DIR", str(prompts))
    monkeypatch.setenv("REVIEW_PIPELINE", "mvp")
    Settings.load(env_file=None)  # the MVP does not read tuning config
    monkeypatch.setenv("REVIEW_PIPELINE", "v2")
    with pytest.raises(FileNotFoundError, match="per_standard.v2"):
        Settings.load(env_file=None)


def test_progression_prompt_names_each_lines_grade():
    text = load_prompt(PROMPTS, "progression.v1")
    assert "highest grade G" in text and "stated grade" not in text


def test_progression_reviewers_use_prompt_v2_and_v1_stays_selectable():
    from dataclasses import replace
    from agentic.review.v2.graph import effective_profile
    from agentic.review.v2.options import ReviewOptions
    from agentic.tuning import validate_tuning
    for name in ("full", "lean"):
        profile = load_profile(CONFIG, name)
        assert {r.prompt for r in profile.reviewers if r.mode == "progression"} == {"progression.v2"}
        validate_tuning(CONFIG, PROMPTS, name)
    s = Settings(config_dir=CONFIG, bands_profile="full")
    base = ReviewOptions().resolve(s, {})
    v1 = ReviewOptions(prompt_versions={"6+-writing": "v1"}).resolve(s, {})
    assert base.prompts["6+-writing"] == "progression.v2" and v1.prompts["6+-writing"] == "progression.v1"
    full = load_profile(CONFIG, "full")
    texts = {ref: load_prompt(PROMPTS, ref) for ref in ("per_standard.v1", "progression.v1", "progression.v2")}
    conditions = load_conditions(CONFIG)
    assert (tuning_hash(effective_profile(full, base), conditions, texts)
            != tuning_hash(effective_profile(full, v1), conditions, texts))


# ---- reasoning level (Task R) ----

def test_llm_reasoning_setting(monkeypatch):
    monkeypatch.delenv("LLM_REASONING", raising=False)
    assert Settings.load(env_file=None).llm_reasoning == "default"
    monkeypatch.setenv("LLM_REASONING", "")
    assert Settings.load(env_file=None).llm_reasoning == "default"
    monkeypatch.setenv("LLM_REASONING", "Low")
    assert Settings.load(env_file=None).llm_reasoning == "low"
    monkeypatch.setenv("LLM_REASONING", "extreme")
    with pytest.raises(ValueError, match="LLM_REASONING"):
        Settings.load(env_file=None)


def test_reviewer_reasoning_is_read_and_validated(tmp_path):
    cfg, _ = _tuning_copy(tmp_path)
    assert all(r.reasoning is None for r in load_profile(cfg, "full").reviewers)  # shipped: no key
    _write_bands(cfg, _lines(reasoning="medium"))
    assert load_profile(cfg, "full").reviewers[0].reasoning == "medium"
    _write_bands(cfg, _lines(reasoning="extreme"))
    with pytest.raises(ValueError, match=r"bands\.toml profiles\.full\..*reasoning.*extreme"):
        load_profile(cfg, "full")


def test_tuning_hash_differs_between_reasoning_levels():
    from dataclasses import replace

    profile = load_profile(CONFIG, "lean")
    conditions = load_conditions(CONFIG)

    def hashed(level):
        p = replace(profile, reviewers=[replace(profile.reviewers[0], reasoning=level), *profile.reviewers[1:]])
        return tuning_hash(p, conditions, {})
    assert hashed("low") != hashed("high")
    assert hashed("low") == hashed("low")
    assert hashed(None) == hashed("default") == tuning_hash(profile, conditions, {})  # default = today's hash


def test_graded_profile_uses_per_standard_v2_while_full_and_lean_stay_on_v1():
    graded = load_profile(CONFIG, "graded").reviewers
    per_std = [r for r in graded if r.mode == "per_standard"]
    assert len(per_std) == 9 and {r.prompt for r in per_std} == {"per_standard.v2"}
    for name in ("full", "lean"):
        profile = load_profile(CONFIG, name).reviewers
        assert {r.prompt for r in profile if r.mode == "per_standard"} == {"per_standard.v1"}
    for name in PROFILES:
        validate_tuning(CONFIG, PROMPTS, name)

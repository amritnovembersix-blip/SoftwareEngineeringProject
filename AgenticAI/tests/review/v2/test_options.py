import re

import pytest

from agentic.config import Settings
from agentic.review.v2.options import ReviewOptions

DEFAULTS = {"mc_finding_min_picks": 2, "root_cause_enabled": True}


def test_defaults_come_from_settings():
    s = Settings(review_pipeline="v2", bands_profile="lean", verify=False)
    r = ReviewOptions().resolve(s, DEFAULTS)
    assert r.pipeline == "v2" and r.profile == "lean"
    assert r.root_cause is True and r.verify is False
    assert r.settings == DEFAULTS
    assert r.prompts and all(".v" in v for v in r.prompts.values())  # one "<name>.v<N>" per reviewer


def test_option_beats_settings_default():
    s = Settings(review_pipeline="mvp", bands_profile="full", verify=True)
    r = ReviewOptions(pipeline="v2", profile="lean", root_cause=False, verify=False).resolve(s, DEFAULTS)
    assert (r.pipeline, r.profile, r.root_cause, r.verify) == ("v2", "lean", False, False)


def test_settings_override_merges_over_defaults():
    r = ReviewOptions(settings_override={"mc_finding_min_picks": 3}).resolve(Settings(), DEFAULTS)
    assert r.settings == {"mc_finding_min_picks": 3, "root_cause_enabled": True}
    assert DEFAULTS["mc_finding_min_picks"] == 2  # the defaults dict is not mutated


def test_prompt_version_override():
    s = Settings(bands_profile="full")
    base = ReviewOptions().resolve(s, DEFAULTS).prompts["3-5-language"]
    r = ReviewOptions(prompt_versions={"3-5-language": "v2"}).resolve(s, DEFAULTS)
    assert r.prompts["3-5-language"] == base.rsplit(".", 1)[0] + ".v2"


def test_unknown_reviewer_override_rejected():
    with pytest.raises(ValueError, match="nope"):
        ReviewOptions(prompt_versions={"nope": "v2"}).resolve(Settings(), DEFAULTS)


def test_settings_override_is_validated_against_the_defs():
    with pytest.raises(ValueError, match="no_such_setting"):
        ReviewOptions(settings_override={"no_such_setting": 1}).resolve(Settings(), DEFAULTS)
    with pytest.raises(ValueError, match="extension_check_enabled"):  # not available yet
        ReviewOptions(settings_override={"extension_check_enabled": True}).resolve(Settings(), DEFAULTS)
    with pytest.raises(ValueError, match="mc_finding_min_picks"):  # below min 1
        ReviewOptions(settings_override={"mc_finding_min_picks": 0}).resolve(Settings(), DEFAULTS)
    with pytest.raises(ValueError, match="root_cause_enabled"):  # wrong type
        ReviewOptions(settings_override={"root_cause_enabled": "yes"}).resolve(Settings(), DEFAULTS)


# ---- reasoning level (Task R): run option > bands.toml > LLM_REASONING > default ----

def _bands_with(tmp_path, reasoning_lines):
    import shutil

    from agentic.config import PACKAGE_ROOT

    cfg = tmp_path / "config"
    shutil.copytree(PACKAGE_ROOT / "config", cfg)
    (cfg / "bands.toml").write_text(
        "[profiles.lean]\n"
        '[[profiles.lean.reviewers]]\nid = "low-band"\ngrades = [3, 5]\nstrands = ["L", "W", "R"]\n'
        'mode = "per_standard"\nprompt = "per_standard.v1"\n' + reasoning_lines.get("low-band", "") +
        '[[profiles.lean.reviewers]]\nid = "high-band"\ngrades = [6, "course"]\nstrands = ["W", "RL", "RI"]\n'
        'mode = "progression"\nprompt = "progression.v2"\n' + reasoning_lines.get("high-band", ""),
        encoding="utf-8")
    return cfg


def test_reasoning_precedence(tmp_path):
    cfg = _bands_with(tmp_path, {"high-band": 'reasoning = "high"\n'})
    s = Settings(bands_profile="lean", config_dir=cfg, llm_reasoning="medium")
    assert ReviewOptions().resolve(s, DEFAULTS).reasoning == {"low-band": "medium", "high-band": "high"}
    over = ReviewOptions(reasoning={"high-band": "low"}).resolve(s, DEFAULTS)
    assert over.reasoning == {"low-band": "medium", "high-band": "low"}  # run option beats bands.toml
    no_env = Settings(bands_profile="lean", config_dir=cfg)
    assert ReviewOptions().resolve(no_env, DEFAULTS).reasoning == {"low-band": "default", "high-band": "high"}
    off = ReviewOptions(reasoning={"high-band": "default"}).resolve(s, DEFAULTS)
    assert off.reasoning["high-band"] == "default"  # an explicit "default" is an override too


def test_reasoning_override_names_a_bad_reviewer_or_level():
    s = Settings(bands_profile="lean")
    with pytest.raises(ValueError, match="nope"):
        ReviewOptions(reasoning={"nope": "low"}).resolve(s, DEFAULTS)
    with pytest.raises(ValueError, match="3-5-all.*extreme"):
        ReviewOptions(reasoning={"3-5-all": "extreme"}).resolve(s, DEFAULTS)


@pytest.mark.parametrize("profile, version", [("graded", "v1"), ("graded", "v2"), ("full", "v3"), ("lean", "v3")])
def test_prompt_override_must_fit_the_reviewers_mode(profile, version):
    reviewer = "6+-all" if profile == "lean" else "6+-writing"
    with pytest.raises(ValueError, match=rf"{re.escape(reviewer)}.*progression\.{version}"):
        ReviewOptions(profile=profile, prompt_versions={reviewer: version}).resolve(Settings(), DEFAULTS)


def test_prompt_override_to_a_newer_search_prompt_resolves():
    r = ReviewOptions(profile="graded", prompt_versions={"6+-writing": "v4"}).resolve(Settings(), DEFAULTS)
    assert r.prompts["6+-writing"] == "progression.v4"

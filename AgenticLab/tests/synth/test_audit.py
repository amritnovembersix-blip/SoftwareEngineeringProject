"""Cross-model fidelity audit (lab): config, sampler, agreement math, report hygiene. No live model."""
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from agentic.config import Settings
from agentic.llm.fake import FakeChatModel
from agentic_lab.config import SYNTHETIC_DIR
from agentic_lab.synth import audit
from agentic_lab.synth.generate_course import FamilyCheck, FidelityOut

VERSIONS = ("2.00-control", "2.00-intervention")
SENTINEL = "ZQX-SENTINEL-SUBMISSION-TEXT-0451"


# --- config --------------------------------------------------------------------------------------------------

def _env(tmp_path, text: str) -> Path:
    p = tmp_path / ".env"
    p.write_text(text, encoding="utf-8")
    return p


@pytest.fixture
def clean_env(monkeypatch):
    for k in ("AUDIT_LLM_MODEL", "AUDIT_LLM_PROVIDER", "AUDIT_LLM_API_KEY", "AUDIT_LLM_RPM",
              "SYNTH_LLM_MODEL", "SYNTH_LLM_PROVIDER", "SYNTH_LLM_API_KEY", "SYNTH_LLM_RPM"):
        monkeypatch.delenv(k, raising=False)


def test_audit_settings_requires_a_model(tmp_path, clean_env):
    from agentic_lab.config import audit_settings
    s = Settings(llm_provider="p1", llm_model="m1", llm_api_key="k1", llm_rpm=7)
    with pytest.raises(ValueError, match="AUDIT_LLM_MODEL"):
        audit_settings(s, env_file=_env(tmp_path, "SYNTH_LLM_MODEL=writer\n"))
    with pytest.raises(ValueError, match="AUDIT_LLM_MODEL"):
        audit_settings(s, env_file=_env(tmp_path, "AUDIT_LLM_MODEL=   \n"))


def test_audit_settings_rejects_the_writer_model(tmp_path, clean_env, monkeypatch):
    from agentic_lab.config import audit_settings
    s = Settings(llm_provider="p1", llm_model="m1", llm_api_key="k1", llm_rpm=7)
    with pytest.raises(ValueError, match="writer"):
        audit_settings(s, env_file=_env(tmp_path, "SYNTH_LLM_MODEL=writer\nAUDIT_LLM_MODEL=writer\n"))
    # no SYNTH_LLM_MODEL: the writer is LLM_MODEL
    with pytest.raises(ValueError, match="writer"):
        audit_settings(s, env_file=_env(tmp_path, "AUDIT_LLM_MODEL=m1\n"))


def test_audit_settings_falls_back_to_llm_not_synth(tmp_path, clean_env, monkeypatch):
    from agentic_lab.config import audit_settings
    s = Settings(llm_provider="p1", llm_model="m1", llm_api_key="k1", llm_rpm=7)
    env = _env(tmp_path, "SYNTH_LLM_MODEL=writer\nSYNTH_LLM_API_KEY=synth-key\nSYNTH_LLM_RPM=99\n"
                         "AUDIT_LLM_MODEL=big-model\nAUDIT_LLM_RPM=\n")
    monkeypatch.setenv("AUDIT_LLM_API_KEY", "audit-key")
    out = audit_settings(s, env_file=env)
    assert (out.llm_provider, out.llm_model, out.llm_api_key, out.llm_rpm) == ("p1", "big-model", "audit-key", 7)


# --- sampler (real committed 2.00 data, no model) -------------------------------------------------------------

def test_sample_is_deterministic_and_covers_every_required_stratum():
    a = audit.draw_sample(SYNTHETIC_DIR, VERSIONS, n=40, seed=7)
    b = audit.draw_sample(SYNTHETIC_DIR, VERSIONS, n=40, seed=7)
    assert [c.submission_id for c in a.chosen] == [c.submission_id for c in b.chosen]
    assert len(a.chosen) == 40 and len({(c.version, c.submission_id) for c in a.chosen}) == 40
    assert all(c.assignment_type != "multiple_choice" for c in a.chosen)
    required = [r for r in a.coverage if r.required]
    assert required and all(r.sampled >= 1 for r in required), [r for r in required if not r.sampled]
    dims = {(r.dimension, r.value) for r in required}
    for v in ("on-track", "Approaching", "Developing", "Beginning"):
        assert ("band", v) in dims
    for v in ("paragraph", "short_answer", "essay"):
        assert ("type", v) in dims
    assert {("arm", "control"), ("arm", "intervention"), ("quirk", "yes"), ("kind", "intervention"),
            ("module", "rl"), ("module", "ri"), ("original", "fail")} <= dims  # original failures sampled too
    profiles = {json.loads(x)["profile"] for v in VERSIONS
                for x in (SYNTHETIC_DIR / "courses" / v / "answer_key" / "students.jsonl").read_text(
                    encoding="utf-8").splitlines()}
    assert {("profile", p) for p in profiles} <= dims
    other = audit.draw_sample(SYNTHETIC_DIR, VERSIONS, n=40, seed=8)
    assert [c.submission_id for c in other.chosen] != [c.submission_id for c in a.chosen]


def test_small_sample_still_covers_required_strata():
    s = audit.draw_sample(SYNTHETIC_DIR, VERSIONS, n=1, seed=7)
    assert all(r.sampled >= 1 for r in s.coverage if r.required)  # coverage wins over N


# --- agreement math --------------------------------------------------------------------------------------------

def _rec(sub, band, checks, original_pass, audit_pass, extra=(), usable=True, unjudged=False):
    return audit.AuditRecord(submission_id=sub, version="v-control", arm="control", assignment_id="a",
                             profile="P", band=band, checks=[audit.CheckPair(*c) for c in checks],
                             original_pass=original_pass, audit_pass=audit_pass, audit_extra=list(extra),
                             usable=usable, possibly_unjudged=unjudged)


def test_agreement_counts_and_kappa():
    records = [
        _rec("s1", "Developing", [("agreement", "family", True, True), ("3.W.2.A.a", "quirk", True, False)],
             True, False),
        _rec("s2", "Developing", [("agreement", "family", False, False)], False, False),
        _rec("s3", "Beginning", [("agreement", "family", True, False), ("evidence", "family", False, True)],
             False, False, extra=["9-10.W.2.A"]),
        _rec("s4", "on-track", [], True, True),
        _rec("s5", "on-track", [], True, True, usable=False),  # unusable: excluded from agreement
    ]
    out = audit.summarize(records)
    fam = out["per_check"]["agreement"]
    assert (fam["both_present"], fam["both_absent"], fam["original_only"], fam["audit_only"]) == (1, 1, 1, 0)
    assert fam["n"] == 3 and fam["percent_agreement"] == pytest.approx(2 / 3)
    # po = 2/3; pe = (2/3*1/3) + (1/3*2/3) = 4/9; kappa = (2/3-4/9)/(5/9) = 0.4
    assert fam["kappa"] == pytest.approx(0.4)
    assert out["per_check"]["quirk:3.W.2.A.a"]["original_only"] == 1
    assert out["totals"]["family"]["n"] == 4 and out["totals"]["quirk"]["n"] == 1
    sub = out["submissions"]
    assert (sub["both_pass"], sub["both_fail"], sub["original_pass_audit_fail"],
            sub["original_fail_audit_pass"]) == (1, 2, 1, 0)
    assert out["per_band"]["Developing"]["checks"]["n"] == 3
    assert out["per_band"]["on-track"]["submissions"]["both_pass"] == 1
    assert out["extras"] == {"submissions_with_extra": 1, "top_codes": [["9-10.W.2.A", 1]], "non_code_entries": 0}
    assert out["usable"] == 4 and out["unusable"] == 1


def test_possibly_unjudged_rows_are_counted_and_reported_both_ways():
    records = [
        _rec("s1", "Developing", [("agreement", "family", True, True)], True, True),
        # all expected missing, no extra: maybe a malformed final check, not a verdict
        _rec("s2", "Developing", [("agreement", "family", False, True)], False, True, unjudged=True),
    ]
    out = audit.summarize(records)
    assert out["possibly_unjudged"] == 1
    assert out["per_check"]["agreement"]["audit_only"] == 1
    assert out["submissions"]["original_fail_audit_pass"] == 1
    w = out["without_unjudged"]
    assert w["per_check"]["agreement"]["n"] == 1 and w["per_check"]["agreement"]["audit_only"] == 0
    assert w["totals"]["all"]["n"] == 1
    assert w["submissions"]["n"] == 1 and w["submissions"]["original_fail_audit_pass"] == 0


def test_possibly_unjudged_flag_from_a_fidelity_row():
    from agentic_lab.answer_key import FidelityRow
    row = FidelityRow(submission_id="x", missing=["agreement", "3.W.2.A.a"], extra=[])
    assert audit.possibly_unjudged(row, ["agreement", "3.W.2.A.a"])
    assert not audit.possibly_unjudged(row, ["agreement", "3.W.2.A.a", "evidence"])  # a partial miss is a verdict
    assert not audit.possibly_unjudged(FidelityRow(submission_id="x", missing=["agreement"], extra=["9-10.W.2.A"]),
                                       ["agreement"])
    assert not audit.possibly_unjudged(None, ["agreement"])
    assert not audit.possibly_unjudged(FidelityRow(submission_id="x", missing=[], extra=[]), [])


def test_blank_extras_are_not_extras():
    check = FidelityOut(checks=[FamilyCheck(name="agreement", present=True)], extra_severe=["", "   "])
    assert audit.audit_verdict(check, ["agreement"], {}) == ([], [])


def test_kappa_is_none_when_chance_agreement_is_total():
    out = audit.summarize([_rec("s1", "Developing", [("agreement", "family", True, True)], True, True)])
    assert out["per_check"]["agreement"]["kappa"] is None
    assert out["per_check"]["agreement"]["percent_agreement"] == 1.0


def test_extras_normalization_ignores_planted_codes_case_and_space():
    check = FidelityOut(checks=[FamilyCheck(name="CHECK Agreement:", present=True)],
                        extra_severe=["9-10.w.3.a.C", " 9-10.W.2.A ", "9-10.W.2.A"])
    missing, extra = audit.audit_verdict(check, ["agreement"], {"9-10.W.3.A.c": 2})
    assert missing == [] and extra == ["9-10.W.2.A"]


def test_extra_entries_that_are_not_codes_are_counted_but_never_shown():
    out = audit.summarize([_rec("s1", "Developing", [], True, False, extra=[SENTINEL + " the student wrote"])])
    assert out["extras"]["non_code_entries"] == 1 and out["extras"]["top_codes"] == []
    assert SENTINEL not in json.dumps(out)


# --- end to end on a copy of the real data, with a fake audit chat --------------------------------------------

@pytest.fixture
def data(tmp_path) -> Path:
    import shutil
    for v in VERSIONS:
        shutil.copytree(SYNTHETIC_DIR / "courses" / v, tmp_path / "courses" / v)
    shutil.copytree(SYNTHETIC_DIR / "curricula", tmp_path / "curricula")
    shutil.copy(SYNTHETIC_DIR / "skill_families.json", tmp_path / "skill_families.json")
    sample = audit.draw_sample(tmp_path, VERSIONS, n=12, seed=7)
    for c in sample.chosen:  # plant the sentinel in every sampled submission's text
        path = tmp_path / "courses" / c.version / "submissions" / f"{c.assignment_id}.jsonl"
        rows = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()]
        for r in rows:
            if r["submission_id"] == c.submission_id:
                if "paragraphs" in r:
                    r["paragraphs"][0] = SENTINEL + " " + r["paragraphs"][0]
                else:
                    r["answers"][0]["text"] = SENTINEL + " " + r["answers"][0]["text"]
        path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return tmp_path


def _names(user: str) -> list[str]:
    return [line.split()[1].rstrip(":") for line in user.splitlines() if line.startswith("CHECK ")]


def test_run_audit_rebuilds_the_generator_prompt_and_reports_no_text(data, tmp_path):
    users = []

    def check(_s, u):
        users.append(u)
        if len(users) == 2:  # one unusable reply: counted, not fatal
            return FidelityOut.model_validate({"checks": None, "extra_severe": []})
        return FidelityOut(checks=[FamilyCheck(name=n, present=True) for n in _names(u)],
                           extra_severe=[SENTINEL, "11-12.W.1.A"])

    chat = FakeChatModel({FidelityOut: check}, model_name="fake-audit")
    standards = {"9-10.W.3.A.c": "Edit for sentence formation."}
    result = audit.run_audit(chat, data, VERSIONS, n=12, seed=7, standards=standards, model="fake-audit",
                             writer_model="writer-model-w")
    assert result.calls == 12 and len(users) == 12
    assert result.summary["usable"] == 11 and result.summary["unusable"] == 1
    assert all(SENTINEL in u for u in users)  # the checker saw the submission text

    md, js = audit.write_report(result, tmp_path / "out" / "audit.md")
    text = md.read_text(encoding="utf-8") + js.read_text(encoding="utf-8")
    assert SENTINEL not in text
    assert "fake-audit" in text and "writer-model-w" in text and "second opinion" in text and "11-12.W.1.A" in text
    lines = md.read_text(encoding="utf-8").splitlines()
    at = lines.index("## Extras (unplanned severe weaknesses the audit lists)")
    assert lines[at + 2].startswith("- Submissions with any extra code:")  # the block sits under its heading
    loaded = json.loads(js.read_text(encoding="utf-8"))
    assert loaded["n_sampled"] == 12 and loaded["n_usable"] == 11 and loaded["coverage"]


def test_checker_prompt_matches_what_generate_course_sent(tmp_path):
    """Run generate_course with a recording fake, then rebuild each submission's checker prompt with the audit."""
    import shutil
    from agentic_lab.synth.generate_course import ParagraphOut, ShortAnswerOut, generate_course
    for v in VERSIONS:
        shutil.copytree(SYNTHETIC_DIR / "courses" / v, tmp_path / "courses" / v)
    shutil.copytree(SYNTHETIC_DIR / "curricula", tmp_path / "curricula")
    shutil.copy(SYNTHETIC_DIR / "skill_families.json", tmp_path / "skill_families.json")
    counter = iter(range(10_000))
    sent: list[tuple[str, str]] = []

    def items(u):
        return [line.split()[1].rstrip(":") for line in u.splitlines() if line.startswith("ITEM ")]

    def check(s, u):
        sent.append((s, u))
        return FidelityOut(checks=[FamilyCheck(name=n, present=True) for n in _names(u)], extra_severe=[])

    chat = FakeChatModel({
        ParagraphOut: lambda _s, _u: ParagraphOut(paragraphs=[f"Unique paragraph {next(counter)}."]),
        ShortAnswerOut: lambda _s, u: ShortAnswerOut(
            answers=[{"item_id": i, "text": f"Unique answer {next(counter)}."} for i in items(u)]),
        FidelityOut: check})
    standards = {"9-10.W.3.A.c": "Edit for sentence formation.", "3.W.2.A.a": "Plan writing."}
    for v in VERSIONS:
        generate_course(chat, tmp_path, v, 2027, through_week=2, students=4, overwrite=True, standards=standards)
    assert sent
    sample = audit.draw_sample(tmp_path, VERSIONS, n=10_000, seed=1)
    matched = 0
    for c in sample.chosen:
        system, user = audit.checker_prompt(tmp_path, c, standards)
        if "Unique " not in user:
            continue  # a committed row from a student outside this run
        assert (system, user) in sent, c.submission_id
        matched += 1
    assert matched == len(sent) == 2 * 3 * 4  # 2 arms x (a02, a03 short answer, a04) x 4 students


def test_all_unusable_replies_are_counted_not_fatal(data):
    chat = FakeChatModel({FidelityOut: lambda _s, _u: FidelityOut.model_validate({"checks": 1})})
    with pytest.raises(ValidationError):
        chat.structured("s", "u", FidelityOut)
    result = audit.run_audit(chat, data, VERSIONS, n=3, seed=7, standards={}, model="m")
    n = len(result.sample.chosen)  # coverage of the required strata wins over n=3
    assert n >= 3 and result.calls == n
    assert result.summary["unusable"] == n and result.summary["usable"] == 0


# --- CLI ----------------------------------------------------------------------------------------------------

class _ProviderError(Exception):
    def __init__(self, code, status, message):
        super().__init__(message)
        self.code, self.status = code, status


def test_cli_failure_prints_class_and_status_only(monkeypatch, capsys, tmp_path, clean_env):
    import agentic_lab.__main__ as cli

    def boom(*a, **k):
        raise _ProviderError(429, "RESOURCE_EXHAUSTED", SENTINEL)

    monkeypatch.setenv("AUDIT_LLM_MODEL", "audit-model-x")
    monkeypatch.setattr(cli, "build_synth_chat", lambda s: object())
    monkeypatch.setattr(cli.audit, "run_audit", boom)
    assert cli.main(["audit-fidelity", "--sample", "4", "--out", str(tmp_path / "a.md")]) == 1
    err = capsys.readouterr().err
    assert "audit-fidelity failed: _ProviderError 429 RESOURCE_EXHAUSTED" in err and SENTINEL not in err


def test_cli_setup_error_when_model_unset(monkeypatch, capsys, clean_env):
    import agentic_lab.__main__ as cli
    monkeypatch.setattr(cli, "audit_settings", lambda s: (_ for _ in ()).throw(ValueError("AUDIT_LLM_MODEL is not set")))
    assert cli.main(["audit-fidelity"]) != 0
    assert "AUDIT_LLM_MODEL" in capsys.readouterr().err


def test_cli_writes_report_with_fake_chat(monkeypatch, capsys, tmp_path, data, clean_env):
    import agentic_lab.__main__ as cli
    monkeypatch.setenv("AUDIT_LLM_MODEL", "audit-model-x")
    monkeypatch.setenv("DATA_DIR", str(data))
    monkeypatch.setattr(cli, "build_synth_chat", lambda s: FakeChatModel(
        {FidelityOut: lambda _s, u: FidelityOut(checks=[FamilyCheck(name=n, present=True) for n in _names(u)],
                                                extra_severe=[])}))
    monkeypatch.setattr(cli, "read_standard_texts", lambda: {})
    out = tmp_path / "rep" / "audit.md"
    assert cli.main(["audit-fidelity", "--sample", "5", "--seed", "3", "--out", str(out)]) == 0
    assert out.exists() and out.with_suffix(".json").exists()
    printed = capsys.readouterr().out
    assert "audit-model-x" in printed and SENTINEL not in printed

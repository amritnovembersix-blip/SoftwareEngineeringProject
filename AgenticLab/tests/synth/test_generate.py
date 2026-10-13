import json

import pytest
from fakes import label_row, write_essays, write_labels

from agentic import dataset
from agentic_lab.__main__ import main
from agentic_lab.labels import LabelRow
from agentic.llm.fake import FakeChatModel
from agentic_lab.synth.assignment import ASSIGNMENT_PROMPT
from agentic_lab.synth.generate_essays import EssayOut, build_prompt, generate

CODE = "9-10.W.2.A.a"
UNDER = {"standard": CODE, "standard_grade": "9 to 10", "level": 2, "level_label": "Developing",
         "strand": "W", "concept": "Organize ideas logically",
         "description": "Use transitions to link sections of the essay"}


def _chat(calls=None):
    def h(system, user):
        return EssayOut(paragraphs=["Para one é.", "Para two.", "Para three."])
    return FakeChatModel({EssayOut: h})


def test_prompt_includes_concepts_and_severity_not_codes():
    row = LabelRow(**label_row(grade=9, meets_all_standards=False, underdeveloped=[UNDER]))
    system, user = build_prompt(row)
    text = system + user
    assert CODE not in text
    assert "Organize ideas logically" in text
    assert "Use transitions to link sections of the essay" in text
    assert "frequent" in text
    assert "grade 9" in text
    assert ASSIGNMENT_PROMPT in text


def test_prompt_clean_essay_when_meets_all():
    row = LabelRow(**label_row(grade=9, meets_all_standards=True))
    system, user = build_prompt(row)
    assert "clean" in (system + user).lower()
    assert "proficient" in (system + user).lower()


def test_generate_writes_rows_and_skips_existing(tmp_path):
    labels = [LabelRow(**label_row(student_hash=c * 64)) for c in "ab"]
    out = tmp_path / "essays_classroom_1.01.jsonl"
    chat = _chat()
    rep = generate(chat, labels[:1], out, "1.01")
    assert (rep.written, rep.skipped) == (1, 0)
    rep = generate(chat, labels, out, "1.01")
    assert (rep.written, rep.skipped) == (1, 1)
    assert len(chat.calls) == 2
    rows = dataset.load_essays(tmp_path, "1.01")
    assert [r.student_hash for r in rows] == ["a" * 64, "b" * 64]
    r = rows[0]
    assert r.submission_id == "sub-" + "a" * 12 and r.attempt == 1
    assert r.course_id == "classroom_1.01" and r.assignment_id == "asg-classroom_1.01-essay1"
    assert r.submitted_at.isoformat().startswith("2026-10-01T15:00:00")
    raw = out.read_text(encoding="utf-8")
    assert "2026-10-01T15:00:00Z" in raw and "é" in raw


def test_generate_overwrite(tmp_path):
    labels = [LabelRow(**label_row(student_hash="a" * 64))]
    out = tmp_path / "essays_classroom_1.01.jsonl"
    generate(_chat(), labels, out, "1.01")
    rep = generate(_chat(), labels, out, "1.01", overwrite=True)
    assert (rep.written, rep.skipped) == (1, 0)
    assert len(out.read_text(encoding="utf-8").splitlines()) == 1


def test_overwrite_failure_keeps_existing_file(tmp_path):
    labels = [LabelRow(**label_row(student_hash=c * 64)) for c in "ab"]
    out = tmp_path / "essays_classroom_1.01.jsonl"
    generate(_chat(), labels[:1], out, "1.01")
    before = out.read_text(encoding="utf-8")
    n = {"i": 0}

    def boom(system, user):
        n["i"] += 1
        if n["i"] == 2:
            raise RuntimeError("x")
        return EssayOut(paragraphs=["a", "b", "c"])

    with pytest.raises(RuntimeError):
        generate(FakeChatModel({EssayOut: boom}), labels, out, "1.01", overwrite=True)
    assert out.read_text(encoding="utf-8") == before
    assert not list(tmp_path.glob("*.tmp"))


def test_essay_out_needs_three_paragraphs():
    with pytest.raises(ValueError):
        EssayOut(paragraphs=["a", "b"])


def test_cli_generate_essays_fake(tmp_path, monkeypatch, capsys):
    data = tmp_path / "data"
    data.mkdir()
    write_labels(data, "1.01", [label_row(student_hash="a" * 64),
                                label_row(student_hash="b" * 64, meets_all_standards=False,
                                          underdeveloped=[UNDER])])
    for k, v in {"FAKE_LLM": "1", "DATA_DIR": str(data), "CLASSROOM": "1.01"}.items():
        monkeypatch.setenv(k, v)
    assert main(["generate-essays"]) == 0
    out = capsys.readouterr().out
    rows = dataset.load_essays(data, "1.01")
    assert len(rows) == 2 and all(len(r.paragraphs) >= 3 for r in rows)
    assert rows[0].paragraphs[0] not in out
    assert main(["generate-essays"]) == 0
    assert len(dataset.load_essays(data, "1.01")) == 2
    assert main(["generate-essays", "--overwrite"]) == 0
    assert len(dataset.load_essays(data, "1.01")) == 2


def test_cli_generate_essays_missing_labels(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("FAKE_LLM", "1")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    assert main(["generate-essays", "--classroom", "9.99"]) == 2


# --- assignment types: argument (default), rl, ri ---------------------------------------------

from agentic.dataset import AssignmentInfo  # noqa: E402

RL_UNDER = {"standard": "9-10.RL.1.A", "standard_grade": "9-10", "level": 3, "level_label": "Beginning",
            "strand": "Reading Literary Text", "concept": "Evidence/Inference",
            "description": "Draw conclusions, infer, and analyze by citing relevant textual evidence"}


def test_rl_prompt_is_literary_analysis_of_the_named_text():
    row = LabelRow(**label_row(grade=9, meets_all_standards=False, underdeveloped=[RL_UNDER]))
    system, user = build_prompt(row, AssignmentInfo(type="rl", text="Animal Farm (George Orwell)",
                                                    strands=["W", "RL"]))
    assert "Animal Farm (George Orwell)" in user
    assert "literary" in user.lower()
    assert "Evidence/Inference" in user and "pervasive" in user
    assert "9-10.RL.1.A" not in system + user
    assert ASSIGNMENT_PROMPT not in user


def test_ri_prompt_defaults_to_orwell_preface():
    row = LabelRow(**label_row(grade=9, meets_all_standards=True))
    _, user = build_prompt(row, AssignmentInfo(type="ri", strands=["W", "RI"]))
    assert "preface" in user.lower() and "informational" in user.lower()


def test_rl_prompt_default_text_is_animal_farm():
    row = LabelRow(**label_row(grade=9, meets_all_standards=True))
    _, user = build_prompt(row, AssignmentInfo(type="rl", strands=["W", "RL"]))
    assert "Animal Farm" in user


def test_generate_uses_the_assignment(tmp_path):
    chat = _chat()
    rows = [LabelRow(**label_row(grade=9))]
    generate(chat, rows, tmp_path / "e.jsonl", "1.02", assignment=AssignmentInfo(type="rl", strands=["W", "RL"]))
    assert "Animal Farm" in chat.calls[0][2]


def test_cli_generate_reads_classroom_assignment_file(tmp_path, monkeypatch, capsys):
    import agentic_lab.__main__ as lab_main

    write_labels(tmp_path, "1.02", [label_row(grade=9)])
    dataset.assignment_path(tmp_path, "1.02").write_text('{"type": "rl"}', encoding="utf-8")
    chat = _chat()
    monkeypatch.setattr(lab_main, "build_chat", lambda s: chat)
    for k, v in {"FAKE_LLM": "1", "DATA_DIR": str(tmp_path), "CLASSROOM": "1.02"}.items():
        monkeypatch.setenv(k, v)
    assert main(["generate-essays"]) == 0
    assert "Animal Farm" in chat.calls[0][2]


# --- teacher prompt + target check ------------------------------------------------------------

from agentic_lab.synth.generate_essays import check_targets  # noqa: E402


def test_essay_prompt_uses_the_teachers_prompt():
    row = LabelRow(**label_row(grade=9, meets_all_standards=True))
    _, user = build_prompt(row, AssignmentInfo(type="rl", prompt="Choose ONE theme and trace it.",
                                               targets=["9-10.RL.1.D"]))
    assert "Choose ONE theme and trace it." in user


def test_check_targets_finds_codes_outside_targets():
    rows = [LabelRow(**label_row(grade=9, meets_all_standards=False, underdeveloped=[RL_UNDER, UNDER]))]
    assert check_targets(rows, AssignmentInfo(type="rl", targets=["9-10.RL.1.A"])) == [CODE]
    assert check_targets(rows, AssignmentInfo(type="rl")) == []  # no targets: nothing to check


def test_cli_generate_rejects_labels_outside_targets(tmp_path, monkeypatch, capsys):
    write_labels(tmp_path, "1.05", [label_row(grade=9, meets_all_standards=False, underdeveloped=[UNDER])])
    dataset.assignment_path(tmp_path, "1.05").write_text(
        '{"type": "rl", "targets": ["9-10.RL.1.A"]}', encoding="utf-8")
    for k, v in {"FAKE_LLM": "1", "DATA_DIR": str(tmp_path), "CLASSROOM": "1.05"}.items():
        monkeypatch.setenv(k, v)
    assert main(["generate-essays"]) == 2
    err = capsys.readouterr().err
    assert f"labels use codes outside the assignment targets: {CODE}" in err
    assert not dataset.essays_path(tmp_path, "1.05").exists()

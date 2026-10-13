import json
from datetime import datetime

from fakes import essay_row, label_row, write_essays, write_labels

from agentic import reports, wiring
from agentic.config import Settings
from agentic.models import (
    Dropped, Finding, MetStandard, ReviewResult, SubmissionRef, Verification,
)

PARAS = ["Secret essay sentence alpha.", "Secret essay sentence beta.", "Secret essay sentence gamma."]


def _run(tmp_path, strategy):
    write_labels(tmp_path, "1.01", [label_row(grade=9)])
    write_essays(tmp_path, "1.01", [essay_row(paragraphs=PARAS, course_id="classroom_1.01",
                                              assignment_id="asg-classroom_1.01-essay1")])
    ref = SubmissionRef(course_id="classroom_1.01", assignment_id="asg-classroom_1.01-essay1",
                        user_id="a" * 64, submission_id="sub-1", attempt=1)
    s = Settings(fake_llm=True, data_dir=tmp_path, classroom="1.01")
    return wiring.build_reviewer(s, strategy=strategy).run(ref)


def test_report_has_no_essay_or_issue_text(tmp_path):
    for strategy in ("full", "rag"):
        res = _run(tmp_path, strategy)
        out = tmp_path / strategy
        path = reports.write_review_report(res, out, now=datetime(2026, 10, 5, 12, 30, 15))
        assert path.name == f"sub-1-{strategy}-20261005T123015.json"
        text = path.read_text(encoding="utf-8")
        for p in PARAS:
            assert p not in text
        assert "Rambling" not in text  # fake RAG issue text is never persisted
        data = json.loads(text)
        assert data["strategy"] == strategy
        assert set(data["models"]) == {"chat", "embedder", "decision"}


def _result():
    return ReviewResult(
        submission_ref=SubmissionRef(course_id="c", assignment_id="a", user_id="u",
                                     submission_id="s", attempt=1),
        course_grade=9, strategy="full", standards=[], observations=[],
        findings=[
            Finding(standard_code="W.1", concept="Research", description="d", paragraphs=[2],
                    reasons=["thin"], observation_count=1, summary="Needs sources", p_correct=0.8),
            Finding(standard_code="W.2", concept="Development", description="d", paragraphs=[1],
                    reasons=["thin"], observation_count=1, summary="Weak structure"),
        ],
        met_standards=[MetStandard(standard_code="W.3", concept="Conventions", p_met=0.3, possible_miss=True),
                       MetStandard(standard_code="W.4", concept="Voice", p_met=0.9)],
        dropped=[Dropped(stage="assess", reason="unknown_standard")],
        verification=Verification(status="ok", model="jev-latest"),
    )


def test_print_summary_lists_possible_misses():
    text = reports.print_summary(_result())
    assert "W.1" in text and "0.80" in text
    assert "W.2" in text and "n/a" in text
    assert "Possible misses" in text and "W.3" in text and "W.4" not in text
    assert "ok" in text


def test_report_writes_given_models(tmp_path):
    s = Settings(fake_llm=True, data_dir=tmp_path, classroom="1.01")
    res = _run(tmp_path, "rag")
    reviewer = wiring.build_reviewer(s, strategy="rag")
    path = reports.write_review_report(res, tmp_path / "r", models=reviewer.models)
    assert json.loads(path.read_text(encoding="utf-8"))["models"] == {
        "chat": "fake-chat", "embedder": "fake-embed", "decision": "fake-jev"}

from datetime import datetime, timedelta, timezone

import pytest

from agentic.analytics.base import AnalyticsWriter, ResultsReader
from agentic.analytics.memory import InMemoryAnalyticsWriter, InMemoryResultsReader
from agentic.analytics.models import LinkRow, VerdictRow
from agentic.analytics.run import UnknownClassroom, analyze_classroom, analyze_student
from agentic.analytics.status import AnalyticsSettings
from agentic.analytics.summary import PairSuggestion, ReteachGroup

S = AnalyticsSettings(attention_hits=2, attention_window=3, secure_hits=3, secure_window=4,
                      trend_window=3, review_flip_rate=0.10, group_min_size=2, max_tutees=2)
CONCEPT = "9-10.W.3.A"
STD = CONCEPT + ".a"
T0 = datetime(2026, 9, 1, 12, tzinfo=timezone.utc)
AS_OF = T0 + timedelta(days=60)


def vr(student, n, level, strength=False, classroom="c1", std=STD):
    """Assignment n of the student; level 0 meets, else growth_area at that level."""
    due = T0 + timedelta(days=7 * n)
    growth = level > 0
    return VerdictRow(
        student_id=student, classroom_id=classroom, submission_id=f"{student}-{n}", due_at=due,
        submitted_at=due, submission_type="essay", standard_code=std,
        verdict="growth_area" if growth else "meets",
        support_level={1: "approaching", 2: "developing", 3: "beginning"}.get(level), notable_strength=strength,
        confidence=0.8, finding_condition="with a prompt" if growth else None, overruled=False)


def classroom_rows():
    rows = []
    for s in ("a", "b", "c"):  # three growth areas in a row: needs attention
        rows += [vr(s, n, 2) for n in range(3)]
    rows += [vr("d", n, 0, strength=(n == 3)) for n in range(4)]  # four meets with a strength: secure
    return rows


LINKS = [LinkRow("a", "a-2", CONCEPT, "7.W.3.A"), LinkRow("b", "b-1", CONCEPT, "7.W.3.A")]


def run_all(rows=None, links=LINKS, as_of=AS_OF, **kw):
    reader = InMemoryResultsReader(classroom_rows() if rows is None else rows, links, kw.get("classrooms"))
    writer = InMemoryAnalyticsWriter()
    out = analyze_classroom(reader, writer, "c1", as_of, S, "manual")
    return out, writer


def test_in_memory_adapters_fit_the_targets():
    assert isinstance(InMemoryResultsReader([]), ResultsReader)
    assert isinstance(InMemoryAnalyticsWriter(), AnalyticsWriter)


def test_classroom_run_end_to_end():
    out, writer = run_all()
    by = {st.student_id: st for st in out.statuses}
    assert [st.student_id for st in out.statuses] == ["a", "b", "c", "d"]
    assert [by[s].status for s in "abcd"] == ["needs_attention"] * 3 + ["secure"]
    assert by["a"].points == (2, 2, 2) and by["a"].basis == "3/3" and by["a"].current_level == 2
    assert out.groups == [ReteachGroup("7.W.3.A", CONCEPT, ("a", "b"))]
    assert out.pairs == [PairSuggestion(CONCEPT, "d", "a"), PairSuggestion(CONCEPT, "d", "b")]
    assert [(g.concept_code, g.students) for g in out.gaps] == [(CONCEPT, ("a", "b", "c"))]
    run = writer.started[0]
    assert writer.saved[0].run == run and writer.failed == []
    assert run.scope == "classroom" and run.student_id is None and run.trigger == "manual"
    assert str(run.run_id) == out.run_id
    saved = writer.saved[0]
    assert saved.statuses == out.statuses and saved.groups == out.groups and saved.pairs == out.pairs
    assert saved.weeks and {w.concept_code for w in saved.weeks} == {CONCEPT}


def test_student_scope_writes_statuses_only():
    reader = InMemoryResultsReader(classroom_rows(), LINKS)
    writer = InMemoryAnalyticsWriter()
    out = analyze_student(reader, writer, "c1", "a", AS_OF, S, "after_review")
    assert [(st.student_id, st.status) for st in out.statuses] == [("a", "needs_attention")]
    saved = writer.saved[0]
    assert saved.weeks == [] and saved.groups == [] and saved.pairs == []
    assert out.groups == [] and out.pairs == [] and out.gaps == []
    assert saved.run.scope == "student" and saved.run.student_id == "a"


def test_as_of_filters_by_due_date():
    out, writer = run_all(as_of=T0 - timedelta(days=1))
    assert out.statuses == [] and writer.saved[0].weeks == []  # an empty, succeeded run
    out, _ = run_all(as_of=T0 + timedelta(days=7))  # only the first two assignments are due
    assert [(st.student_id, st.status, st.points, st.basis) for st in out.statuses] == [
        ("a", "needs_attention", (2, 2), "2/2"), ("b", "needs_attention", (2, 2), "2/2"),
        ("c", "needs_attention", (2, 2), "2/2"), ("d", "emerging", (0, 0), "0/2")]


def test_unknown_classroom_starts_no_run():
    reader, writer = InMemoryResultsReader(classroom_rows()), InMemoryAnalyticsWriter()
    with pytest.raises(UnknownClassroom):
        analyze_classroom(reader, writer, "nope", AS_OF, S, "manual")
    with pytest.raises(ValueError):
        analyze_student(reader, writer, "nope", "a", AS_OF, S, "manual")
    assert writer.started == []


def test_naive_as_of_is_rejected():
    with pytest.raises(ValueError):
        run_all(as_of=datetime(2026, 11, 1))


def test_failure_marks_the_run_failed_with_the_class_name_only():
    class Boom(InMemoryResultsReader):
        def verdict_rows(self, *a, **k):
            raise KeyError("secret essay sentence")

    writer = InMemoryAnalyticsWriter()
    with pytest.raises(KeyError):
        analyze_classroom(Boom(classroom_rows()), writer, "c1", AS_OF, S, "scheduled")
    assert writer.saved == [] and [(r.trigger, e) for r, e in writer.failed] == [("scheduled", "KeyError")]


def test_other_classrooms_rows_are_ignored():
    rows = classroom_rows() + [vr("z", n, 3, classroom="c2") for n in range(3)]
    out, _ = run_all(rows=rows)
    assert "z" not in {st.student_id for st in out.statuses}


def test_given_run_id_is_the_runs_id():
    from uuid import uuid4
    reader, writer = InMemoryResultsReader(classroom_rows()), InMemoryAnalyticsWriter()
    rid = uuid4()
    out = analyze_classroom(reader, writer, "c1", AS_OF, S, "manual", run_id=rid)
    assert out.run_id == str(rid) and writer.started[0].run_id == rid
    assert analyze_student(reader, writer, "c1", "a", AS_OF, S, "manual", run_id=rid).run_id == str(rid)


def test_in_memory_reader_maps_ids():
    reader = InMemoryResultsReader(classroom_rows(), courses={"course-1": "c1"}, users={"u1": "a"})
    assert reader.classroom_for_course("course-1") == "c1" and reader.classroom_for_course("c1") == "c1"
    assert reader.classroom_for_course("nope") is None
    assert reader.student_for_user("u1") == "a" and reader.student_for_user("b") == "b"


def test_everything_reviewed_counts_work_due_in_the_future():
    """With no as_of, a reviewed submission counts even if its due date hasn't come yet."""
    from datetime import timedelta, timezone

    from agentic.analytics.memory import InMemoryAnalyticsWriter, InMemoryResultsReader
    from agentic.analytics.models import VerdictRow
    from agentic.analytics.run import analyze_classroom, everything_reviewed
    from agentic.analytics.status import AnalyticsSettings

    now = datetime(2026, 11, 5, tzinfo=timezone.utc)
    due = now + timedelta(days=60)
    row = VerdictRow(student_id="u1", classroom_id="c1", submission_id="s1", due_at=due, submitted_at=due,
                     submission_type="essay", standard_code="3.L.1.A.a", verdict="meets", support_level=None,
                     notable_strength=False, confidence=None, finding_condition=None, overruled=False)
    reader = InMemoryResultsReader([row])
    assert everything_reviewed(reader, "c1", now) == due
    assert everything_reviewed(InMemoryResultsReader([], classrooms={"c1"}), "c1", now) == now  # nothing reviewed
    later = due + timedelta(days=1)
    assert everything_reviewed(reader, "c1", later) == later  # a past due date never moves it back
    cfg = AnalyticsSettings(attention_hits=2, attention_window=3, secure_hits=3, secure_window=4, trend_window=3,
                            review_flip_rate=0.1, group_min_size=2, max_tutees=2)
    out = analyze_classroom(reader, InMemoryAnalyticsWriter(), "c1", everything_reviewed(reader, "c1", now), cfg, "manual")
    assert len(out.statuses) == 1

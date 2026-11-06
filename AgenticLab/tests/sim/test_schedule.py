from datetime import datetime, timezone
from pathlib import Path

from agentic_lab.sim.schedule import ScheduledAssignment, due_in_week, event_time_for, schedule

DATA = Path(__file__).resolve().parents[2] / "data" / "synthetic"
CONTROL, INTERVENTION = "2.00-control", "2.00-intervention"


def _utc(*args):
    return datetime(*args, tzinfo=timezone.utc)


def _ids(items):
    return [i.file_id for i in items]


def test_a02_due_and_event_time():
    a02 = next(i for i in schedule(DATA, CONTROL) if i.file_id == "ela9v2-a02")
    assert a02.due_at == _utc(2027, 1, 16, 5, 59)  # Fri 2027-01-15 23:59 CST
    assert a02.event_time == _utc(2027, 1, 18, 14, 0)  # Mon 08:00 CST
    assert a02.course_id == "classroom_2.00-control"
    assert a02.lms_id == "ela9v2-a02@2.00-control"
    assert a02.version == CONTROL and a02.week == 1


def test_weeks_in_due_order():
    for version in (CONTROL, INTERVENTION):
        assert _ids(due_in_week(schedule(DATA, version), 4)) == ["ela9v2-a07", "ela9v2-a08"]
    assert _ids(due_in_week(schedule(DATA, CONTROL), 5)) == ["ela9v2-a09", "ela9v2-a10"]
    assert _ids(due_in_week(schedule(DATA, INTERVENTION), 5)) == [
        "ela9v2-a09", "ela9v2-a10", "ela9v2-i01"]


def test_schedule_sorted_by_due_then_id():
    items = schedule(DATA, INTERVENTION)
    assert items == sorted(items, key=lambda i: (i.due_at, i.file_id))


def test_sunday_maps_to_following_monday():
    sunday_night = _utc(2027, 1, 18, 5, 59)  # Sun 2027-01-17 23:59 CST
    assert event_time_for(sunday_night) == _utc(2027, 1, 18, 14, 0)


def test_monday_maps_to_next_week():
    monday = _utc(2027, 1, 12, 5, 59)  # Mon 2027-01-11 23:59 CST
    assert event_time_for(monday) == _utc(2027, 1, 18, 14, 0)


def test_dst_uses_local_offset():
    friday = _utc(2027, 3, 20, 4, 59)  # Fri 2027-03-19 23:59 CDT
    assert event_time_for(friday) == _utc(2027, 3, 22, 13, 0)  # Mon 08:00 CDT


def test_scheduled_assignment_is_frozen():
    item = schedule(DATA, CONTROL)[0]
    assert isinstance(item, ScheduledAssignment)
    try:
        item.week = 9
    except AttributeError:
        return
    raise AssertionError("not frozen")

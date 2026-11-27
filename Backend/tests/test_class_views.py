"""Class views: GET /classrooms/{id}/concept-weeks (heatmap), /reteach-groups and GET /comparison.

Expected values are read straight from Justin's views (class_concept_week_latest,
reteach_group_latest, concept_status_latest)."""
import uuid
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app import auth, db
from app.config import settings
from app.main import app

client = TestClient(app)
P1, P2 = "classroom_2.00-control", "classroom_2.00-intervention"


def _rows(sql, params=()):
    try:
        return db.query(sql, params)
    except db.DatabaseUnavailable:
        return []


needs_data = pytest.mark.skipif(
    not _rows("SELECT 1 FROM class_concept_week_latest WHERE classroom_id = %s LIMIT 1", (P1,)),
    reason="run `python -m agentic_lab fill-demo` first")


# ---- heatmap ----------------------------------------------------------------------------

@needs_data
def test_heatmap_cells_match_the_analysis():
    body = client.get(f"/classrooms/{P1}/concept-weeks").json()
    expected = {(r["concept_code"], r["week_start"].isoformat()): r for r in _rows(
        "SELECT * FROM class_concept_week_latest WHERE classroom_id = %s", (P1,))}
    seen = 0
    for skill in body["skills"]:
        assert len(skill["cells"]) == len(body["weeks"])
        for week, cell in zip(body["weeks"], skill["cells"]):
            e = expected.get((skill["concept_code"], week))
            if e is None:
                assert cell is None
                continue
            seen += 1
            assert (cell["meets"], cell["approaching"], cell["developing"], cell["beginning"],
                    cell["too_little_evidence"]) == (e["n_level0"], e["n_level1"], e["n_level2"],
                                                     e["n_level3"], e["n_too_little"])
            assert cell["needing_support"] == e["n_level2"] + e["n_level3"]
    assert seen == len(expected)


@needs_data
def test_heatmap_weeks_are_mondays_in_order_and_neediest_skills_first():
    body = client.get(f"/classrooms/{P1}/concept-weeks").json()
    assert body["weeks"] == sorted(body["weeks"])
    assert all(date.fromisoformat(w).isoweekday() == 1 for w in body["weeks"])
    need = [s["needing_support_latest"] for s in body["skills"]]
    assert need == sorted(need, reverse=True)
    assert body["legend"]["meets"] == "Meets"
    text = str(body).lower()
    assert "percent" not in text and "%" not in text


@needs_data
def test_heatmap_strand_filter():
    body = client.get(f"/classrooms/{P1}/concept-weeks", params={"strand": "Writing"}).json()
    assert body["skills"] and {s["strand"] for s in body["skills"]} == {"Writing"}


# ---- reteach groups ---------------------------------------------------------------------

@needs_data
def test_reteach_groups_match_the_analysis_largest_first():
    body = client.get(f"/classrooms/{P1}/reteach-groups").json()
    expected: dict[str, set] = {}
    for r in _rows("SELECT group_id, student_id FROM reteach_group_latest WHERE classroom_id = %s", (P1,)):
        expected.setdefault(str(r["group_id"]), set()).add(r["student_id"])
    assert {g["group_id"]: set(g["student_ids"]) for g in body} == expected
    assert all(g["size"] == len(g["student_ids"]) for g in body)
    sizes = [g["size"] for g in body]
    assert sizes == sorted(sizes, reverse=True)
    assert all(g["root_skill_code"] and g["grade_skill_code"] for g in body)


# ---- comparison -------------------------------------------------------------------------

@needs_data
def test_comparison_numbers_match_the_analysis():
    body = client.get("/comparison", params={"classrooms": f"{P1},{P2}"}).json()
    assert [c["classroom_id"] for c in body["classrooms"]] == [P1, P2]
    for c in body["classrooms"]:
        cid = c["classroom_id"]
        need = _rows("SELECT COUNT(DISTINCT student_id) AS n FROM concept_status_latest "
                     "WHERE classroom_id = %s AND status = 'needs_attention'", (cid,))[0]["n"]
        total = _rows("SELECT COUNT(*) AS n FROM concept_status_latest WHERE classroom_id = %s", (cid,))[0]["n"]
        assert c["students_needing_attention"] == need
        assert sum(c["skill_statuses"].values()) == total == sum(c["skill_trends"].values())
        weekly = _rows("""SELECT week_start, SUM(n_level2 + n_level3) AS n FROM class_concept_week_latest
                          WHERE classroom_id = %s GROUP BY week_start ORDER BY week_start""", (cid,))
        assert [w["needing_support"] for w in c["weekly"]] == [r["n"] for r in weekly]


@needs_data
@pytest.mark.parametrize("value", [P1, f"{P1},{P1}", f"{P1},{P2},classroom_1.01", ""])
def test_comparison_needs_two_different_classes(value):
    assert client.get("/comparison", params={"classrooms": value}).status_code == 422


@needs_data
def test_unknown_class_is_404():
    assert client.get("/classrooms/nope/concept-weeks").status_code == 404
    assert client.get("/classrooms/nope/reteach-groups").status_code == 404
    assert client.get("/comparison", params={"classrooms": f"{P1},nope"}).status_code == 404


# ---- with real logins -------------------------------------------------------------------

@pytest.fixture
def other_teacher():
    settings.jwt_secret = settings.jwt_secret or "test-secret-" + "x" * 40
    tag = uuid.uuid4().hex[:8]
    tid = f"test-teacher-{tag}"
    db.execute("INSERT INTO teacher (id, name) VALUES (%s, 'Other Teacher')", (tid,))
    uid = db.execute("""INSERT INTO app_user (email, password_hash, role, teacher_id, display_name)
                        VALUES (%s, 'unused', 'teacher', %s, 'x') RETURNING id""", (f"o-{tag}@sass.test", tid))[0]["id"]
    yield {"Authorization": f"Bearer {auth.create_token(uid, 'teacher')}"}
    db.execute("DELETE FROM app_user WHERE id = %s", (uid,))
    db.execute("DELETE FROM teacher WHERE id = %s", (tid,))


@needs_data
@pytest.mark.real_auth
def test_another_teacher_gets_403(other_teacher):
    h = other_teacher
    assert client.get(f"/classrooms/{P1}/concept-weeks", headers=h).status_code == 403
    assert client.get(f"/classrooms/{P1}/reteach-groups", headers=h).status_code == 403
    assert client.get("/comparison", params={"classrooms": f"{P1},{P2}"}, headers=h).status_code == 403


@pytest.mark.real_auth
def test_no_login_is_401():
    assert client.get(f"/classrooms/{P1}/concept-weeks").status_code == 401
    assert client.get(f"/classrooms/{P1}/reteach-groups").status_code == 401
    assert client.get("/comparison", params={"classrooms": f"{P1},{P2}"}).status_code == 401

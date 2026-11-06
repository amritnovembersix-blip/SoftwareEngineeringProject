from pathlib import Path

from agentic.coursedata import lms_assignment_id
from agentic.lms.coursedir_client import CourseDirLMSClient

from agentic_lab.sim.release import FolderReleaser

DATA = Path(__file__).resolve().parents[2] / "data" / "synthetic"
CONTROL, INTERVENTION = "2.00-control", "2.00-intervention"


def _sub_files(sim_dir, version):
    return sorted(p.name for p in (sim_dir / "courses" / version / "submissions").glob("*.jsonl"))


def test_release_copies_only_that_assignments_submissions(tmp_path):
    r = FolderReleaser(DATA, tmp_path)
    rows = r.release(CONTROL, "ela9v2-a01")
    assert rows > 0
    assert _sub_files(tmp_path, CONTROL) == ["ela9v2-a01.jsonl"]
    assert r.is_released(CONTROL, "ela9v2-a01")
    assert not r.is_released(CONTROL, "ela9v2-a02")


def test_base_files_copied_and_answer_key_never(tmp_path):
    r = FolderReleaser(DATA, tmp_path)
    r.release(CONTROL, "ela9v2-a01")
    r.release(INTERVENTION, "ela9v2-a01")
    for v in (CONTROL, INTERVENTION):
        assert not (tmp_path / "courses" / v / "answer_key").exists()
        assert (tmp_path / "courses" / v / "course.json").is_file()
        assert (tmp_path / "courses" / v / "roster.jsonl").is_file()
        assert (tmp_path / "courses" / v / "interventions.json").is_file()
    assert (tmp_path / "courses" / INTERVENTION / "assignments" / "ela9v2-i01.json").is_file()
    assert (tmp_path / "curricula" / "ela9-v2" / "curriculum.json").is_file()
    assert not list(tmp_path.rglob("answer_key"))


def test_lms_client_sees_only_released_rows(tmp_path):
    r = FolderReleaser(DATA, tmp_path)
    n = r.release(CONTROL, "ela9v2-a01")
    client = CourseDirLMSClient(tmp_path, CONTROL)
    assert len(client.list_submissions(f"classroom_{CONTROL}", lms_assignment_id("ela9v2-a01", CONTROL))) == n
    assert len(client.list_submissions(f"classroom_{CONTROL}")) == n  # nothing else released


def test_release_twice_is_a_no_op(tmp_path):
    r = FolderReleaser(DATA, tmp_path)
    n = r.release(CONTROL, "ela9v2-a01")
    f = tmp_path / "courses" / CONTROL / "submissions" / "ela9v2-a01.jsonl"
    before = f.read_bytes()
    assert r.release(CONTROL, "ela9v2-a01") == n
    assert f.read_bytes() == before


def test_deleting_the_copy_unreleases_and_rerelease_restores_base(tmp_path):
    import shutil
    r = FolderReleaser(DATA, tmp_path)
    n = r.release(CONTROL, "ela9v2-a01")
    (tmp_path / "courses" / CONTROL / "submissions" / "ela9v2-a01.jsonl").unlink()
    assert not r.is_released(CONTROL, "ela9v2-a01")
    shutil.rmtree(tmp_path)
    assert r.release(CONTROL, "ela9v2-a01") == n
    assert (tmp_path / "courses" / CONTROL / "course.json").is_file()


def _spy_writes(monkeypatch):
    from agentic_lab.sim import release
    written, real = [], release._write_copy
    monkeypatch.setattr(release, "_write_copy", lambda src, dst: written.append(Path(dst)) or real(src, dst))
    return written


def test_second_release_rewrites_no_base_file(tmp_path, monkeypatch):
    """Ruling 14: files the service may be reading for earlier reviews are never rewritten when unchanged."""
    r = FolderReleaser(DATA, tmp_path)
    r.release(CONTROL, "ela9v2-a01")
    written = _spy_writes(monkeypatch)
    r.release(CONTROL, "ela9v2-a02")
    assert written == [tmp_path / "courses" / CONTROL / "submissions" / "ela9v2-a02.jsonl"]
    assert not list(tmp_path.rglob("*.tmp"))


def test_changed_or_missing_base_file_is_rewritten(tmp_path, monkeypatch):
    r = FolderReleaser(DATA, tmp_path)
    r.release(CONTROL, "ela9v2-a01")
    roster = tmp_path / "courses" / CONTROL / "roster.jsonl"
    roster.write_text("stale\n", encoding="utf-8")
    curriculum = tmp_path / "curricula" / "ela9-v2" / "curriculum.json"
    curriculum.unlink()
    written = _spy_writes(monkeypatch)
    r.release(CONTROL, "ela9v2-a02")
    assert roster in written and curriculum in written
    assert roster.read_bytes() == (DATA / "courses" / CONTROL / "roster.jsonl").read_bytes()
    assert curriculum.is_file()


def test_missing_source_submissions_names_only_ids(tmp_path):
    import pytest
    r = FolderReleaser(DATA, tmp_path)
    with pytest.raises(LookupError) as e:
        r.release(CONTROL, "ela9v2-zz99")
    assert "ela9v2-zz99" in str(e.value) and CONTROL in str(e.value)
    assert "/" not in str(e.value) and "\\" not in str(e.value)

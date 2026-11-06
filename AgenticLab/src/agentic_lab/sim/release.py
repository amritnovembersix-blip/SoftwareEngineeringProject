"""Hands out one assignment's submissions at a time, so the LMS view grows week by week."""
import filecmp
import os
import shutil
from pathlib import Path
from typing import Protocol

from agentic.coursedata import CourseDir, course_path, curriculum_path


def _write_copy(src: Path, dst: Path) -> None:
    """Copy src over dst through a temporary file in dst's folder, so a reader never sees a half-written file."""
    tmp = dst.with_name(dst.name + ".tmp")
    try:
        shutil.copy2(src, tmp)
        os.replace(tmp, dst)
    finally:
        tmp.unlink(missing_ok=True)


def _sync_file(src: Path, dst: Path) -> None:
    """Copy src to dst only when dst is missing or its bytes differ. The agentic service may be reading dst for
    an earlier assignment's reviews; on Windows replacing an open file fails, so unchanged files are left alone."""
    if dst.is_file() and filecmp.cmp(src, dst, shallow=False):
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    _write_copy(src, dst)


def _sync_tree(src: Path, dst: Path) -> None:
    for path in sorted(src.rglob("*")):
        if path.is_file():
            _sync_file(path, dst / path.relative_to(src))


class Releaser(Protocol):
    """Target: what the simulator needs to make a submissions file visible to the LMS."""

    def release(self, version: str, file_id: str) -> int:
        """Make the assignment's submissions visible; returns the number of rows released."""
        ...

    def is_released(self, version: str, file_id: str) -> bool: ...


class FolderReleaser:
    """Adapter: wraps the synthetic course folder (the Adaptee), copying released pieces into sim_dir.

    sim_dir is a course-folder tree that CourseDirLMSClient can serve. answer_key/ is never copied.
    """

    def __init__(self, data_dir: Path, sim_dir: Path):
        self._data = Path(data_dir)
        self._sim = Path(sim_dir)

    def _copy_base(self, version: str) -> None:
        """Course files, the course's own assignments and the whole curriculum (idempotent: writes only
        missing or changed files)."""
        src, dst = course_path(self._data, version), course_path(self._sim, version)
        dst.mkdir(parents=True, exist_ok=True)
        for name in ("course.json", "roster.jsonl", "interventions.json"):
            if (src / name).is_file():
                _sync_file(src / name, dst / name)
        if (src / "assignments").is_dir():
            _sync_tree(src / "assignments", dst / "assignments")
        curriculum = CourseDir(self._data, version).course().curriculum
        _sync_tree(curriculum_path(self._data, curriculum), curriculum_path(self._sim, curriculum))

    def _released_file(self, version: str, file_id: str) -> Path:
        return course_path(self._sim, version) / "submissions" / f"{file_id}.jsonl"

    def release(self, version: str, file_id: str) -> int:
        source = course_path(self._data, version) / "submissions" / f"{file_id}.jsonl"
        if not source.is_file():
            raise LookupError(f"no submissions for {file_id}@{version}")
        self._copy_base(version)
        _sync_file(source, self._released_file(version, file_id))
        return len(CourseDir(self._sim, version).submissions(file_id))

    def is_released(self, version: str, file_id: str) -> bool:
        return self._released_file(version, file_id).is_file()

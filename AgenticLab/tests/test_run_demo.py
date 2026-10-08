import importlib.util
from pathlib import Path

from fakes import label_row, write_labels

from agentic import dataset
from agentic_lab.labels import load_labels

SPEC = importlib.util.spec_from_file_location(
    "run_demo", Path(__file__).resolve().parents[1] / "demo" / "run_demo.py")
run_demo = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(run_demo)


def test_prepare_fake_data_copies_labels_and_generates_essays(tmp_path):
    src = tmp_path / "synthetic"
    src.mkdir()
    write_labels(src, "1.00", [label_row(student_hash="a" * 64, grade=9)])
    dest = tmp_path / "demo"
    run_demo.prepare_fake_data(src, dest, "1.00")
    assert load_labels(dest, "1.00")[0].student_hash == "a" * 64
    essays = dataset.load_essays(dest, "1.00")
    assert len(essays) == 1 and len(essays[0].paragraphs) >= 3
    run_demo.prepare_fake_data(src, dest, "1.00")  # rerun is safe: no duplicates
    assert len(dataset.load_essays(dest, "1.00")) == 1

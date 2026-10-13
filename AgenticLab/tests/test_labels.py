import pytest
from fakes import label_row, write_labels

from agentic import dataset
from agentic_lab.labels import load_labels


def test_round_trip_labels(tmp_path):
    ud = {"standard": "9-10.W.1", "standard_grade": "9-10", "level": 1, "level_label": "Approaching",
          "strand": "Writing", "concept": "C", "description": "D"}
    write_labels(tmp_path, "1.00", [label_row(meets_all_standards=False, underdeveloped=[ud])])
    labels = load_labels(tmp_path, "1.00")
    assert labels[0].grade == 9 and labels[0].underdeveloped[0].level == 1
    assert labels[0].underdeveloped[0].standard == "9-10.W.1"


def test_malformed_labels_error_has_no_essay_text(tmp_path):
    dataset.labels_path(tmp_path, "1.00").write_text('{"x": "SECRET ESSAY" oops\n', encoding="utf-8")
    with pytest.raises(ValueError, match="malformed") as ei:
        load_labels(tmp_path, "1.00")
    assert "SECRET" not in str(ei.value) and ei.value.__context__ is None

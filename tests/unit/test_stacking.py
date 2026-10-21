"""Waves and countries of a tracker: what drifted, and one stacked file."""

import polars as pl

from src.core.models import DataMap, Variable, VarType
from src.engines.tabs.stacking import drift, stack
from src.engines.tabs.tracking import _shared_trackable_variables


def _dm(**variables):
    return DataMap([Variable(name=n, label=n, type=VarType.SINGLE, values=v, question_ref=n)
                    for n, v in variables.items()])


W1 = _dm(BRAND={1: "Alpha", 2: "Beta", 3: "Gamma"}, AGE={1: "18-34", 2: "35+"},
         SRC={1: "Sago", 2: "Lucid", 3: "Cint"})
W2 = _dm(BRAND={1: "Alpha", 2: "Beta", 3: "Delta", 4: "Gamma"}, AGE={1: "18-34", 2: "35 +"},
         SRC={1: "Client", 2: "Sago", 3: "Lucid", 4: "Cint"}, NEW={1: "Yes"})


def test_drift_names_what_breaks_and_what_only_grew():
    d = drift([("Wave 1", W1), ("Wave 2", W2)])
    by = {c["variable"]: c for c in d["changed"]}
    assert d["not_in_every_source"] == [{"variable": "NEW", "in": ["Wave 2"]}]
    assert "AGE" not in by  # "35+" vs "35 +" is not a change
    assert by["BRAND"]["breaking"] and by["BRAND"]["problems"][0] == (
        "code 3 is 'Delta' here, 'Gamma' in Wave 1")
    assert by["SRC"]["problems"][0].startswith(
        "codes renumbered (2 answers moved): 'Sago' is 2 here")
    assert d["n_breaking"] == 2


def test_a_code_that_only_grew_stacks_and_missing_columns_stay_blank():
    w3 = _dm(BRAND={1: "Alpha", 2: "Beta", 3: "Gamma", 4: "Epsilon"})
    d = drift([("UK", W1), ("FR", w3)])
    assert d["n_breaking"] == 0 and d["changed"][0]["problems"] == ["codes added: [4]"]
    data, dm = stack([("UK", pl.DataFrame({"BRAND": [1, 3], "AGE": [2, 1]}), W1),
                      ("FR", pl.DataFrame({"BRAND": [4]}), w3)])
    assert data.columns[0] == "SOURCE" and data["SOURCE"].to_list() == [1, 1, 2]
    assert data["AGE"].to_list() == [2, 1, None]
    assert dm["SOURCE"].values == {1: "UK", 2: "FR"}
    assert dm["BRAND"].values[4] == "Epsilon"


def test_wave_tracking_refuses_a_code_whose_label_changed():
    shared, skipped = _shared_trackable_variables(
        _dm(BRAND={1: "Alpha", 2: "Gamma"}), _dm(BRAND={1: "Alpha", 2: "Delta"}))
    assert shared == [] and skipped["BRAND"].startswith("answer labels changed (code 2")

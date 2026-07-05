"""Tab plan loading, validation, and auto-nets."""

import pytest

from src.core.models import DataMap, Variable, VarType
from src.engines.tabs.spec import auto_nets, load_tab_plan

SCALE = {1: "Strongly agree", 2: "Agree", 3: "Neither", 4: "Disagree", 5: "Strongly disagree"}


@pytest.fixture
def datamap():
    return DataMap(
        [
            Variable("gender", "Gender", VarType.SINGLE, {1: "Male", 2: "Female"}),
            Variable("q1", "Agreement", VarType.SINGLE, SCALE),
            Variable("weight", "Weight", VarType.NUMERIC),
        ]
    )


def _write(tmp_path, text):
    path = tmp_path / "plan.yaml"
    path.write_text(text)
    return path


def test_auto_nets_five_point(datamap):
    nets = auto_nets(datamap["q1"])
    assert [n.codes for n in nets] == [[1, 2], [4, 5]]
    assert nets[0].label == "Top 2 Box (Strongly agree + Agree)"


def test_auto_nets_skips_short_scales(datamap):
    assert auto_nets(datamap["gender"]) == []


def test_load_valid_plan(tmp_path, datamap):
    plan = load_tab_plan(
        _write(
            tmp_path,
            """
title: Test plan
weight: weight
banner: [gender]
tables:
  - stub: q1
    nets: auto
    mean: true
  - stub: q1
    filter: {gender: [1]}
""",
        ),
        datamap,
    )
    assert plan.weight == "weight"
    assert len(plan.tables) == 2
    assert len(plan.tables[0].nets) == 2
    assert plan.tables[0].mean is True
    assert plan.tables[1].filter == {"gender": [1]}


def test_unknown_stub_rejected(tmp_path, datamap):
    with pytest.raises(ValueError, match="stub variable not in datamap"):
        load_tab_plan(
            _write(tmp_path, "banner: [gender]\ntables:\n  - stub: q99\n"), datamap
        )


def test_unknown_banner_rejected(tmp_path, datamap):
    with pytest.raises(ValueError, match="banner variable"):
        load_tab_plan(
            _write(tmp_path, "banner: [nope]\ntables:\n  - stub: q1\n"), datamap
        )


def test_empty_tables_rejected(tmp_path, datamap):
    with pytest.raises(ValueError, match="no tables"):
        load_tab_plan(_write(tmp_path, "banner: [gender]\ntables: []\n"), datamap)

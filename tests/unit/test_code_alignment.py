"""Renumbering an inferred data file's codes to the questionnaire's, by label.

A CSV/Excel export that ships answer TEXT has no codes of its own — the
importer invents them, alphabetically. The questionnaire's codes are the real
ones, and every code-level check silently compares apples to oranges until the
two agree. Measured on the Federal Reserve SHED 2014 file: 18 of 19 routing
checks read 0% agreement for this reason alone.

The rules pinned here are the conservative ones. Renumbering the wrong column
would corrupt every table cut from it, so refusing is always the safe answer.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl

from src.core.models import DataMap, Variable, VarType
from src.core.survey_definition import (
    Block,
    Option,
    Question,
    QuestionType,
    SurveyDefinition,
)
from src.engines.questionnaire.label_merge import (
    align_codes_to_questionnaire,
    remap_data_codes,
)


def _survey(options: list[Option], qid: str = "B6") -> SurveyDefinition:
    question = Question(
        qid=qid,
        text="Are you better or worse off than your parents were?",
        type=QuestionType.SINGLE,
        options=options,
    )
    return SurveyDefinition(
        study_id="s1",
        title="t",
        source="extracted",
        blocks=[Block(block_id="B1", question_ids=[qid])],
        questions=[question],
    )


# The real shape: a descending scale, and a category the questionnaire never
# prints. The importer sorted the answer text alphabetically.
_SCALE = [
    Option(code=5, label="Much better off"),
    Option(code=4, label="Somewhat better off"),
    Option(code=3, label="About the same"),
    Option(code=2, label="Somewhat worse off"),
    Option(code=1, label="Much worse off"),
]
_ALPHABETICAL = {
    1: "About the same",
    2: "Much better off",
    3: "Much worse off",
    4: "Refused",
    5: "Somewhat better off",
    6: "Somewhat worse off",
}


def _datamap(values: dict[int, str], provenance: str = "csv_import") -> DataMap:
    dm = DataMap()
    dm.add(
        Variable(
            name="B6",
            label="B6",
            type=VarType.SINGLE,
            values=dict(values),
            provenance=provenance,
        )
    )
    return dm


def test_alphabetical_codes_become_the_questionnaires_codes() -> None:
    dm = _datamap(_ALPHABETICAL)
    result = align_codes_to_questionnaire(_survey(_SCALE), dm)

    assert result.n_codes_aligned == 5
    remap = result.code_remaps["B6"]
    assert remap[2] == 5  # "Much better off"
    assert remap[5] == 4  # "Somewhat better off"
    assert remap[1] == 3  # "About the same"
    assert remap[6] == 2  # "Somewhat worse off"
    assert remap[3] == 1  # "Much worse off"
    # The labels are untouched — only the number moved.
    assert dm["B6"].values[5] == "Much better off"
    assert dm["B6"].values[1] == "Much worse off"


def test_a_category_the_questionnaire_never_prints_keeps_a_distinct_code() -> None:
    """"Refused" is a real answer; it must not be dropped or collide."""
    dm = _datamap(_ALPHABETICAL)
    result = align_codes_to_questionnaire(_survey(_SCALE), dm)

    remap = result.code_remaps["B6"]
    refused = remap[4]
    assert refused not in {remap[c] for c in (1, 2, 3, 5, 6)}
    assert dm["B6"].values[refused] == "Refused"
    assert len(dm["B6"].values) == len(_ALPHABETICAL)  # nothing lost


def test_spss_codes_are_never_renumbered() -> None:
    """Codes that came from the script are the script's. We do not touch them."""
    dm = _datamap(_ALPHABETICAL, provenance="spss")
    result = align_codes_to_questionnaire(_survey(_SCALE), dm)

    assert result.code_remaps == {}
    assert dm["B6"].values == _ALPHABETICAL


def test_running_it_twice_changes_nothing() -> None:
    dm = _datamap(_ALPHABETICAL)
    survey = _survey(_SCALE)
    align_codes_to_questionnaire(survey, dm)
    first = dict(dm["B6"].values)

    second = align_codes_to_questionnaire(survey, dm)
    assert second.code_remaps == {}
    assert dm["B6"].values == first


def test_two_data_values_pointing_at_one_option_refuses() -> None:
    """An ambiguous join is not a join. Leave it for a human."""
    dm = _datamap({1: "Much better off", 2: "much  better  off", 3: "About the same"})
    result = align_codes_to_questionnaire(_survey(_SCALE), dm)

    assert result.code_remaps == {}


def test_a_single_matching_label_is_not_enough_evidence() -> None:
    """One shared "Yes" is a coincidence, not proof of the same answer set."""
    dm = _datamap({1: "About the same", 2: "Something else entirely"})
    result = align_codes_to_questionnaire(_survey(_SCALE), dm)

    assert result.code_remaps == {}


def test_bracketed_piping_does_not_stop_the_join() -> None:
    """The questionnaire keeps the scripter's inserts; the data map resolves them."""
    options = [
        Option(code=1, label="I [IF PPMARIT=1, INSERT: (and/or my spouse)] own [our/my] home"),
        Option(code=2, label="I [IF PPMARIT=1, INSERT: (and/or my spouse)] pay rent"),
        Option(code=3, label="I [IF PPMARIT=1, INSERT: (and/or my spouse)] do neither"),
    ]
    dm = _datamap({1: "I [and/or my spouse/partner] pay rent",
                   2: "I [and/or my spouse/partner] own [my/our] home",
                   3: "I [and/or my spouse/partner] do neither"})
    result = align_codes_to_questionnaire(_survey(options), dm)

    remap = result.code_remaps["B6"]
    assert remap[2] == 1  # "own ... home"
    assert remap[1] == 2  # "pay rent"
    assert remap[3] == 3


def test_remap_rewrites_the_data_file(tmp_path: Path) -> None:
    """The data map and the data move together, or they disagree."""
    parquet = tmp_path / "data.parquet"
    pl.DataFrame({"B6": [1, 2, 3, None, 6], "other": [1, 1, 1, 1, 1]}).write_parquet(parquet)

    n = remap_data_codes(parquet, {"B6": {1: 3, 2: 5, 3: 1, 6: 2}})

    assert n == 1
    out = pl.read_parquet(parquet)
    assert out["B6"].to_list() == [3, 5, 1, None, 2]
    assert out["other"].to_list() == [1, 1, 1, 1, 1]  # untouched


def test_remap_leaves_a_value_the_map_never_declared_alone(tmp_path: Path) -> None:
    """A stray code is a finding for the cross-check, not data for us to delete."""
    parquet = tmp_path / "data.parquet"
    pl.DataFrame({"B6": [1, 99]}).write_parquet(parquet)

    remap_data_codes(parquet, {"B6": {1: 3}})

    assert pl.read_parquet(parquet)["B6"].to_list() == [3, 99]


def test_remap_of_nothing_does_not_touch_the_file(tmp_path: Path) -> None:
    parquet = tmp_path / "data.parquet"
    pl.DataFrame({"B6": [1, 2]}).write_parquet(parquet)
    before = parquet.read_bytes()

    assert remap_data_codes(parquet, {}) == 0
    assert remap_data_codes(parquet, {"NOT_A_COLUMN": {1: 2}}) == 0
    assert parquet.read_bytes() == before

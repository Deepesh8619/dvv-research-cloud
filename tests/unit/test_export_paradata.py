"""Typing/timing evidence from Decipher trace columns (synthetic data only)."""

import polars as pl

from src.core.models import DataMap, Variable, VarType
from src.engines.qc.evidence import evidence
from src.engines.qc.paradata import paradata_columns, paradata_features

DF = pl.DataFrame({
    "record": [1, 2, 3, 4],
    "Q15": ["a" * 1168, "typed by hand " * 3, "spoken words here", "copied"],
    "hQ15OE_KeyStrokeCount": ["1", "40", "0", "5"],
    "hQ15OE_CharacterCount": ["1168", "42", "17", "6"],
    "hQ15CopyResponse": ["0", "0", "0", "TRUE"],
    "hQ15OE_SpeechToTextText": [None, None, "spoken words here", None],
})


def test_columns_map_and_oe_suffix_collapses():
    m = paradata_columns(DF.columns + ["Q15OE_ManualTyping", "Q9", "hQ7OE_KeyStrokeCount"])
    assert m["Q15"] == {"keystrokes": "hQ15OE_KeyStrokeCount", "chars": "hQ15OE_CharacterCount",
                        "paste": "hQ15CopyResponse", "stt": "hQ15OE_SpeechToTextText",
                        "manual": "Q15OE_ManualTyping"}
    assert set(m) == {"Q15", "Q7"}
    assert paradata_columns(["record", "Q1"]) == {}


def test_paste_speech_and_flag_variants():
    f = paradata_features(DF, paradata_columns(DF.columns))
    assert f["respondent_id"].to_list() == ["1", "2", "3", "4"]
    assert f["n_pasted"].to_list() == [1, 0, 0, 1]  # 1,168 chars/1 key; copy flag as "TRUE"
    assert f["n_speech"].to_list() == [0, 0, 1, 0]
    assert f["pasted_qids"].to_list() == ["Q15", "", "", "Q15"]
    assert f["max_chars_per_key"][0] == 1168
    assert f["n_open_ends_traced"].to_list() == [1, 1, 1, 1]


def test_evidence_block_only_with_traces():
    dm = DataMap([Variable(name="Q15", label="Why", type=VarType.TEXT, question_ref="Q15")])
    assert evidence(DF, dm, 0, [])["paradata"] == {
        "pasted": ["Q15"], "speech": [], "max_chars_per_key": 1168.0}
    assert "paradata" not in evidence(DF.select("record", "Q15"), dm, 0, [])

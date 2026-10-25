"""One authenticity score per respondent, every reason shown.

Built against real Decipher exports: a physician answered an open end with
1,168 characters and ONE keystroke, the copied text recorded beside it; another
typed 16 keystrokes for 92 characters on a phone keyboard, which autocomplete
explains and is not a fault.
"""

import polars as pl

from src.core.models import DataMap, Variable, VarType
from src.engines.qc.authenticity import (
    ai_text_flags,
    reading_speed_flags,
    score,
    typing_flags,
)
from src.engines.qc.respondents import detect_gibberish_open_ends


def _typing_frame() -> pl.DataFrame:
    return pl.DataFrame({
        "hQ26OE_KeyStrokeCount": [1, 16, 111, 80],
        "hQ26OE_CharacterCount": [1168, 92, 65, 90],
        "hQ26CopyResponse": ["I would recommend ensuring that any", None, "word", None],
        "hQ26Manualtyping": [None, "In clu de data comparing", None, None],
        "hQ26_Copy_Paste_Flagged": [1, 0, 1, 0],
    })


def test_a_paste_counts_only_when_the_keystrokes_fall_well_short():
    found = typing_flags(_typing_frame())
    assert found[0] == [("pasted_answer", "Q26: pasted 1168 characters with 1 keystroke")]
    assert 1 not in found  # phone autocomplete explains few keystrokes
    assert 2 not in found  # 111 keys for 65 characters is typing, whatever was pasted
    assert 3 not in found


def test_dictation_is_never_a_fault():
    df = pl.DataFrame({"hQ5OE_KeyStrokeCount": [0], "hQ5OE_CharacterCount": [200],
                       "hQ5speechToTextText": ["spoken answer"]})
    assert typing_flags(df) == {}


def test_phrases_a_language_model_leaves_behind():
    dm = DataMap([Variable("Q9", label="Why?", type=VarType.TEXT)])
    df = pl.DataFrame({"Q9": ["As an AI language model, I prefer brand A", "It is cheaper"]})
    assert list(ai_text_flags(df, dm)) == [0]


def test_finishing_faster_than_the_words_shown_could_be_skimmed():
    long_q = " ".join(["word"] * 200)
    dm = DataMap([Variable("qtime", label="time", type=VarType.NUMERIC),
                  Variable("Q1", label=long_q, question_text=long_q, type=VarType.SINGLE,
                           values={1: "Yes", 2: "No"})])
    df = pl.DataFrame({"qtime": [5.0, 120.0], "Q1": [1, 2]})
    found = reading_speed_flags(df, dm)
    assert list(found) == [0] and found[0][0][0] == "too_fast_to_read"


def test_script_columns_are_never_gibberish_answers():
    dm = DataMap([Variable("uuid", type=VarType.TEXT), Variable("session", type=VarType.TEXT),
                  Variable("Q26", label="Comments", type=VarType.TEXT)])
    df = pl.DataFrame({"uuid": ["xq9zk2vw8r"], "session": ["kq2jxv9wpl"], "Q26": ["sdfkjhsdkfjh"]})
    assert list(detect_gibberish_open_ends(df, dm)) == ["Q26"]


def test_the_score_adds_reasons_and_bands_them():
    pasted = {"check": "pasted_answer", "detail": "Q26: pasted"}
    fast = {"check": "too_fast_to_read", "detail": "finished in 5s"}
    assert score([pasted])["band"] == "worth a look"
    both = score([pasted, fast, {"check": "pasted_answer", "detail": "Q27: pasted"}])
    assert both["score"] == 35 + 30 + 17 and both["band"] == "review first"
    assert score([{"check": "straightliner", "detail": "grid"}])["band"] == "fine"


def test_the_panel_shows_the_same_score_the_list_ranks_by():
    from src.engines.qc.authenticity import score, signals

    flags = [{"check": "fraud_signal", "detail": "shared_connection"},
             {"check": "fraud_signal", "detail": "copied open end"},
             {"check": "straightliner", "detail": "SAT_ATTR"},
             {"check": "too_fast_to_read", "detail": "finished in 0s"}]
    panel = signals(flags)
    assert (panel["score"], panel["band"]) == (score(flags)["score"], score(flags)["band"]) == (
        70, "review first")
    assert [x["weight"] for x in panel["signals"]] == [20, 10, 10, 30]
    assert signals([]) == {"score": 0, "band": "fine", "signals": []}


def test_a_near_duplicate_open_end_adds_one_duplicate_reason():
    from src.engines.qc.authenticity import signals

    assert signals([], near_duplicate_of="s9")["score"] == 30
    already = [{"check": "duplicate", "detail": "same answers as s3"}]
    assert len(signals(already, near_duplicate_of="s9")["signals"]) == 1


def test_near_duplicate_partners_pairs_sessions_and_is_empty_without_an_index(monkeypatch):
    import src.platform.vectors as vectors
    from src.engines.qc.authenticity import near_duplicate_partners

    monkeypatch.setattr(vectors, "near_duplicates", lambda db, sid: [("Q5:s1", "Q5:s2", 0.99)])
    assert near_duplicate_partners(None, "x") == {"s1": "s2", "s2": "s1"}
    monkeypatch.setattr(vectors, "near_duplicates", lambda db, sid: [])
    assert near_duplicate_partners(None, "x") == {}

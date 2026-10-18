import polars as pl

from src.core.models import DataMap, Variable, VarType
from src.engines.qc.fraud import fraud_signals

PASTE = "great product, would buy again"  # 30 chars


def test_flags_and_scores():
    dm = DataMap([Variable("oe", type=VarType.TEXT)])
    df = pl.DataFrame({
        "oe": [
            PASTE, PASTE.upper() + "!", "short same", "short same", "unique long answer here ok",
        ],
        "vConnection": ["h1", "h1", "h1", None, "h2"],
        "vDevice": [1, 1, 99, 1, 1],
        "vPlaceCheck": [1, 2, 1, 1, 1],
    })
    out = fraud_signals(df, dm)
    assert out["checks_skipped"] == []
    f = out["flags"]
    assert f["row:0"] == {"signals": ["shared_connection", "copied_open_end"], "score": 2}
    assert f["row:1"]["signals"] == ["shared_connection", "copied_open_end", "place_mismatch"]
    assert f["row:2"] == {"signals": ["shared_connection", "no_user_agent"], "score": 2}
    assert "row:3" not in f and "row:4" not in f  # short repeat (<20) ignored


def test_no_paradata_skips():
    dm = DataMap([Variable("q1", type=VarType.SINGLE)])
    out = fraud_signals(pl.DataFrame({"q1": [1, 1]}), dm, key_of=lambda i, r: f"k{i}")
    assert out["flags"] == {}
    assert out["checks_skipped"] == [
        "shared_connection", "copied_open_end", "no_user_agent", "place_mismatch"]


def test_system_text_and_constants_are_not_copied_answers():
    """A real Decipher export flagged every complete: the user agent and a
    script-written variable list are identical text, not pasted answers."""
    dm = DataMap([
        Variable("vUserAgent", type=VarType.TEXT),
        Variable("Suspicious_OtherVariables", type=VarType.TEXT),
        Variable("OE1", type=VarType.TEXT),
    ])
    n = 12
    df = pl.DataFrame({
        "vUserAgent": ["Mozilla/5.0 (Windows NT 10.0; Win64; x64)"] * n,
        "Suspicious_OtherVariables": ["uuid,w,hSample,VendorID,vGetIP"] * n,
        "OE1": [PASTE, PASTE] + [f"my own distinct answer number {i}" for i in range(n - 2)],
    })
    flags = fraud_signals(df, dm)["flags"]
    assert sorted(flags) == ["row:0", "row:1"]

"""Codeframe generation and verbatim coding."""

from unittest.mock import patch

from src.engines.coding.codeframe import Code, CodeFrame, CodeType


def test_codeframe_to_prompt_text():
    codes = [
        Code("QUALITY", "Quality issues", "Product defects or durability concerns"),
        Code("PRICE", "Pricing", "Too expensive or value concerns", parent_id=None),
    ]
    cf = CodeFrame(question_var="q10_open", codes=codes)
    prompt = cf.to_prompt_text()
    assert "Q1_QUALITY" in prompt or "QUALITY" in prompt
    assert "Quality issues" in prompt


def test_batch_code_verbatims_with_mock():
    import polars as pl

    codes = [
        Code("GOOD", "Positive", "Loves it, great product"),
        Code("BAD", "Negative", "Hate it, too expensive"),
        Code("NEUTRAL", "Neutral", "It's okay"),
    ]
    cf = CodeFrame(question_var="q10", codes=codes)

    df = pl.DataFrame({
        "resp_id": [1, 2, 3],
        "text": ["great product", "too expensive", "it works"],
    })

    class FakeAI:
        content = {"codes": ["GOOD"], "confidence": 0.95, "reasoning": "loves it"}
        backend = "test"
        model = "test"

    with patch("src.engines.coding.coder.run_task", return_value=FakeAI()):
        from src.engines.coding.coder import batch_code_verbatims

        result = batch_code_verbatims(df, "text", "resp_id", cf)
        assert "coding_confidence" in result.columns
        assert result.height == 3


def test_codeframe_getitem():
    codes = [Code("Q1", "Label", "Description")]
    cf = CodeFrame("var", codes)
    assert cf["Q1"].label == "Label"


def test_code_defaults():
    c = Code("ID", "Label", "Desc")
    assert c.type == CodeType.THEME
    assert c.parent_id is None
    assert c.examples == []

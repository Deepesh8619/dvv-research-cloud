"""One small questionnaire carrying every shape a real one has.

Built as DocumentBlocks rather than a .docx so it can live in the repo: the
instruments that exposed these shapes are client documents and are not ours to
commit. Each block here is the structure of something real, with the wording
changed.

What it covers, and what each one cost when it was wrong:

* S0/S1  a screener with a terminate, and a numeric age
* S6     a multi-punch with RANDOMIZE, ANCHOR and EXCLUSIVE on "None of these"
* the recruitment-path header that routes P1-P3 and becomes a hidden variable
* P1     a base written inline, and an intro screen under "[PN: SHOW IF P1=1]"
* P2     an option carrying a display condition (it used to be thrown away)
* Q1     a grid written as a table: statements down, scale across, codes under
* Q2     a scale strip whose statements are a separate table
* Q3     a "Note:" line that is not an answer, and a long option with "e.g."
* C9     a quota table, which must NOT become a grid
"""

from src.intake.questionnaire_loader import DocumentBlock


def _p(text: str, index: int) -> DocumentBlock:
    return DocumentBlock(block_type="paragraph", text=text, paragraph_index=index)


def _t(rows: list[list[str]], index: int) -> DocumentBlock:
    serialized = "\n".join(" | ".join(cell for cell in row) for row in rows)
    return DocumentBlock(
        block_type="table", text=serialized, paragraph_index=index, rows=rows
    )


def golden_blocks() -> list[DocumentBlock]:
    blocks: list[DocumentBlock] = []
    n = 0

    def para(text: str) -> None:
        nonlocal n
        n += 1
        blocks.append(_p(text, n))

    def table(rows: list[list[str]]) -> None:
        nonlocal n
        n += 1
        blocks.append(_t(rows, n))

    para("S0. Are you the head of the household?")
    para("1. Yes")
    para("2. No [TERMINATE]")

    para("S1. What is your age?")
    para("[Num box 18-99]")

    para("S6. Which of these shops have you bought skincare from in the past 6 months?")
    para("[PN: RANDOMIZE]")
    para("1. Retailer A")
    para("2. Retailer B")
    para("3. Retailer C")
    para("4. None of these [ANCHOR] [EXCLUSIVE]")

    para("Parent Recruitment Path Ages 26-60 (IF S0=1 AND S1=26-60) ASK P1-P3")

    para("P1. Are you the parent or guardian of a teenager aged 13 to 17?")
    para("1. Yes")
    para("2. No [TERMINATE]")
    para("[PN: SHOW IF P1=1]")
    para(
        "In this survey we would like to speak with your teenager about the "
        "products they use, with your permission."
    )

    para("P2. Who do you buy those products for? Select all that apply.")
    para("Yes - for a child under 18 [ONLY SHOW IF P1=1]")
    para("Yes - for another adult")
    para("No, only for myself [ANCHOR, EXCLUSIVE]")

    para("P3. [ASK IF C1=18+] Is your teenager available to take part?")
    para("1. Yes")
    para("2. No [TERMINATE]")

    para("Q1. How often do you use each of these products?")
    table(
        [
            ["", "[PN: RANDOMIZE ROWS]", "Every day", "Weekly", "Rarely"],
            ["", "", "1", "2", "3"],
            ["1", "Cleanser", "", "", ""],
            ["2", "Moisturiser", "", "", ""],
            ["3", "Sunscreen", "", "", ""],
        ]
    )

    para("Q2. How important is each of these when you choose a brand?")
    table([["0=Not at all important", "1", "2", "3", "4", "5=Very important"]])
    table(
        [
            ["", "[PN: RANDOMIZE]", "Theme - INTERNAL, NOT SHOWN"],
            ["1", "The price is fair", "Value"],
            ["2", "The ingredients are listed clearly", "Trust"],
        ]
    )

    para("Q3. Where do you usually buy skincare? Select one.")
    para("Note: This includes anything bought for you by someone else.")
    para("1. A national chain such as Retailer A or Retailer B")
    para(
        "2. Directly from a brand's own website or application (e.g. a brand "
        "shop, a brand app, or a subscription service they run)"
    )

    para("C9. Sample definition for the field team")
    table(
        [
            ["", "", "If Consumer Panel (S0=1)", "If CRM list (S0=2)"],
            ["1", "13-17", "N=400 QUOTA", "No Quota"],
            ["2", "18-25", "N=400 QUOTA", "No Quota"],
        ]
    )
    return blocks

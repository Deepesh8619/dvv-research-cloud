"""Grids written as tables — read from the cells, and refused when unsure.

Every table here is the real shape from one client questionnaire: a frequency
grid, a brand funnel, an 11-point scale strip with its statements beside it, and
the two tables that must NOT become questions.
"""

from src.core.survey_definition import Option
from src.engines.questionnaire.table_questions import (
    grid,
    question_from_tables,
    scale_strip,
    statements_table,
)

FREQUENCY_GRID = [
    ["", "[PN: RANDOMIZE ROWS]", "Every day", "A few times a week", "Rarely or never"],
    ["", "", "1", "2", "3"],
    ["1", "Face wash / cleanser", "", "", ""],
    ["2", "Moisturizer", "", "", ""],
    ["3", "Sunscreen (for the face)", "", "", ""],
]

BRAND_FUNNEL = [
    ["", "", "Q2a. ever heard of?", "Q2b. Ever purchased from?"],
    ["1", "Bubble", "AUTOCODE 'YES' IF BUBBLE CRM (S0=2)", ""],
    ["2", "Anua", "", ""],
    ["3", "Byoma", "", ""],
]

SCALE_STRIP = [["0=Not Important At All", "1", "2", "3", "5=Neutral", "10=Very Important"]]

STATEMENTS = [
    ["", "[PN: RANDOMIZE]", "Theme – INTERNAL, NOT SHOWN"],
    ["1", "Feels designed for someone my age", "Fit & Longevity"],
    ["2", "Is a brand I can see myself using", "Fit & Longevity"],
]

QUOTA_TABLE = [
    ["", "", "If Consumer Panel (S0=1)", "If Bubble CRM (S0=2)"],
    ["1", "13-17", "N=400 QUOTA", "No Quota"],
    ["2", "18-22", "N=400 QUOTA", "No Quota"],
]

REGION_TABLE = [
    ["Division", "Region", "Quota if Consumer Panel"],
    ["1. Pacific (WA, OR, CA)", "West", "23%"],
    ["3. West North Central", "Midwest", "22%"],
]


def test_a_frequency_grid_is_read_from_its_cells():
    result = grid(FREQUENCY_GRID)
    assert result is not None
    assert [o.label for o in result.cols] == [
        "Every day",
        "A few times a week",
        "Rarely or never",
    ]
    assert [o.code for o in result.cols] == [1, 2, 3]  # the punch row, not positions
    assert [o.label for o in result.rows] == [
        "Face wash / cleanser",
        "Moisturizer",
        "Sunscreen (for the face)",
    ]
    assert result.randomize_rows is True  # "[PN: RANDOMIZE ROWS]" in the header


def test_a_brand_funnel_is_a_grid_not_a_multi_punch():
    """22 brands x 2 questions. Read as one option list it was 31 junk punches."""
    result = grid(BRAND_FUNNEL)
    assert result is not None
    assert [o.label for o in result.cols] == [
        "Q2a. ever heard of?",
        "Q2b. Ever purchased from?",
    ]
    assert [o.label for o in result.rows] == ["Bubble", "Anua", "Byoma"]


def test_a_scale_strip_becomes_columns_with_its_own_codes():
    points = scale_strip(SCALE_STRIP)
    assert points is not None
    assert [o.code for o in points] == [0, 1, 2, 3, 5, 10]
    assert points[0].label == "Not Important At All"
    assert points[1].label == "1"  # a bare point keeps its number as its label


def test_statements_drop_the_scripter_only_column():
    statements = statements_table(STATEMENTS)
    assert statements is not None
    assert [o.code for o in statements] == [1, 2]
    assert [o.label for o in statements] == [
        "Feels designed for someone my age",
        "Is a brand I can see myself using",
    ]
    assert all("INTERNAL" not in o.label for o in statements)


def test_a_quota_table_is_not_a_question():
    """Same shape as a grid, but the interior is already filled in — nobody is
    asked it. Reading it invents a question and fabricates data columns."""
    assert grid(QUOTA_TABLE) is None
    assert grid(REGION_TABLE) is None


def test_a_scale_and_a_statements_table_compose_into_one_grid():
    result = question_from_tables([SCALE_STRIP, STATEMENTS], prose_options=[])
    assert result is not None
    assert len(result.cols) == 6
    assert [o.label for o in result.rows] == [
        "Feels designed for someone my age",
        "Is a brand I can see myself using",
    ]


def test_a_scale_alone_takes_its_statements_from_the_prose():
    prose = [Option(code=1, label="Bubble looks like a brand for me"),
             Option(code=2, label="Bubble has strong clinical credibility")]
    result = question_from_tables([SCALE_STRIP], prose_options=prose)
    assert result is not None
    assert [o.label for o in result.rows] == [o.label for o in prose]


def test_half_a_grid_invents_no_statements():
    """A scale with no statements anywhere keeps its SCALE and no rows: inventing
    rows would put statements nobody wrote into a delivery file. Discarding the
    scale too left "How do you feel about Bubble?" with no answers at all — it is
    either one thing rated on that scale, or a grid whose rows the document
    states elsewhere, and the extractor decides which."""
    shape = question_from_tables([SCALE_STRIP], prose_options=[])
    assert shape is not None and shape.rows == [] and shape.cols
    assert question_from_tables([QUOTA_TABLE], prose_options=[]) is None
    assert question_from_tables([], prose_options=[]) is None


def test_a_grid_row_keeps_the_condition_written_in_it():
    """A grid whose rows each depend on an earlier answer is ordinary — ask
    about the medication types this person actually fills. Every row was shown
    to everybody."""
    rows = grid(
        [
            ["", "", "Filled 1-2 times", "Filled 3-5 times"],
            ["", "", "1", "2"],
            ["1", "Short-term medications [SHOW IF Q14=1]", "", ""],
            ["2", "Ongoing medications", "", ""],
        ]
    )
    assert rows is not None
    assert rows.rows[0].show_if is not None
    assert rows.rows[1].show_if is None


def test_a_table_of_rows_to_fill_in_under_a_measure_header():
    from src.engines.questionnaire.table_questions import entry_table

    rows = [["Treatment", "% of TRD Patients"],
            ["Treated with TMS Only", "[DROP DOWN % - TO BE CONFIRMED]"],
            ["Treated with Spravato Only", ""],
            ["Had a consult only", ""]]
    labels, percent = entry_table(rows)
    assert [o.label for o in labels] == ["Treated with TMS Only", "Treated with Spravato Only",
                                         "Had a consult only"]
    assert percent
    # An answer list with a header is not this shape.
    assert entry_table([["Brand", "Aware"], ["A", "1"], ["B", "2"]]) is None


def test_grid_rows_keep_their_anchor_and_exclusive_tags():
    from src.engines.questionnaire.table_questions import _statements

    rows = [["1", "Bubble", "", ""], ["98", "Other (Specify) [ANCHOR]", "", ""],
            ["99", "None of the above / [ANCHOR, EXCLUSIVE, TERMINATE IF S0=1]", "", ""]]
    got = {o.code: (o.anchored, o.exclusive) for o in _statements(rows, [2, 3])}
    assert got == {1: (False, False), 98: (True, False), 99: (True, True)}

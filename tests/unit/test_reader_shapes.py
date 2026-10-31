"""How real documents write questions, each pinned by a few made-up lines.

Every case here was a layout the reader got wrong on a questionnaire it had not
seen (scripts/reader_whatif.py and the synthetic set found them). A failing test
names the layout; docs/READER_FIX_GUIDE.md says where it is read.
"""

from src.core.survey_definition import QuestionType
from src.engines.questionnaire.prose_directives import parse_prose_question
from src.engines.questionnaire.spec_table import parse_spec_table
from src.engines.questionnaire.table_questions import question_from_tables
from src.intake.questionnaire_loader import DocumentBlock, segment_into_questions


def _p(text, i=0, **kw):
    return DocumentBlock(block_type="paragraph", text=text, paragraph_index=i, **kw)


def _doc(*lines):
    return [_p(t, i) for i, t in enumerate(lines)]


def _labels(blocks):
    return [c.candidate_qid for c in segment_into_questions(blocks) if c.candidate_qid]


# --- where questions start ---------------------------------------------------

def test_a_dash_after_the_label_separates_it():
    assert _labels(_doc("Q1 – Are you?", "Yes", "Q2 - Why?", "Q3 — How?")) == ["Q1", "Q2", "Q3"]


def test_a_label_alone_names_the_next_line():
    chunks = segment_into_questions(_doc("Q1.", "Are you?", "Yes", "No", "Q2.", "Why?"))
    assert [c.candidate_qid for c in chunks] == ["Q1", "Q2"]
    assert chunks[0].chunk_text.splitlines()[0] == "Q1. Are you?"


def test_number_labels_run_in_sequence_and_answers_restart():
    blocks = _doc("1.\tHow old are you? [NUMERIC]", "2.\tWhich brands do you know? MULTI",
                  "1.\tAlpha", "2.\tBeta", "3.\tWhat do you buy most? Single response",
                  "1.\tAlpha", "2.\tBeta")
    assert _labels(blocks) == ["Q1", "Q2", "Q3"]


def test_answer_tags_are_not_question_signals():
    """"1. Market research [TERMINATE]" is an answer, not question 1."""
    blocks = _doc("S1.\tDo you work in any of these?", "1.\tMarket research [TERMINATE]",
                  "2.\tRetail [TERMINATE]", "3.\tNone of these")
    assert _labels(blocks) == ["S1"]


def test_word_numbering_supplies_the_label():
    blocks = [_p("Are you responsible for the shopping? [S]", 0, list_label="Q1."),
              _p("Yes", 1, list_label="1."), _p("No", 2, list_label="2."),
              _p("What is your age? [N]", 3, list_label="Q2.")]
    assert _labels(blocks) == ["Q1", "Q2"]


def test_a_dotted_label_is_a_label():
    assert _labels(_doc("Q.1 Your age? [number]", "Q.2 Are you… [1 answer]",
                        "Q.3 Which pets? [any answers]")) == ["Q1", "Q2", "Q3"]


def test_a_terminate_line_stays_with_its_question():
    chunks = segment_into_questions(_doc("S11. Which would you never visit?", "Alpha",
                                         "TERMINATE IF ALPHA IS SELECTED.", "S12. Next?"))
    assert [c.candidate_qid for c in chunks] == ["S11", "S12"]
    assert "TERMINATE IF ALPHA" in chunks[0].chunk_text


def test_a_quota_heading_is_not_part_of_the_question_above():
    chunks = segment_into_questions(_doc("S6. Decision maker? (SINGLE)", "Yes", "No",
                                         "QUOTA S5 – timing (soft)", "Cell | Target"))
    assert "QUOTA" not in chunks[0].chunk_text


def test_a_translated_line_is_a_note_but_an_english_one_is_the_question():
    translated = segment_into_questions(_doc("Q1. Which language?", "¿Qué idioma prefiere?",
                                             "English", "Spanish"))
    assert "[PN: also worded as:" in translated[0].chunk_text
    continued = segment_into_questions(_doc("Q22. Based on what you have seen…",
                                            "How much do you agree with each?", "Agree"))
    assert "also worded" not in continued[0].chunk_text


# --- what a question is ------------------------------------------------------

def test_matrix_one_per_row_is_a_grid_not_single():
    fields = parse_prose_question("S9. When did you last order from each?\n"
                                  "[PN: MATRIX. SELECT ONE PER ROW]", "S9")
    assert fields.qtype is QuestionType.GRID_SINGLE


def test_a_punch_code_grid_keeps_every_row_and_its_codes():
    rows = [["", "Past 30 days", "1-3 months", "Never"],
            ["Pizza", "1", "2", "6"], ["Bar & Grill", "1", "2", "6"], ["Mexican", "1", "2", "6"]]
    shape = question_from_tables([rows], [])
    assert [r.label for r in shape.rows] == ["Pizza", "Bar & Grill", "Mexican"]
    assert [c.code for c in shape.cols] == [1, 2, 6]


def test_a_quota_table_is_still_not_a_grid():
    rows = [["", "Panel", "CRM"], ["13-17", "N=400", "N=50"], ["18-25", "N=600", "No quota"],
            ["26-40", "N=300", "N=20"]]
    assert question_from_tables([rows], []) is None


# --- one table per variable --------------------------------------------------

def _table(rows):
    return DocumentBlock(block_type="table", text="", paragraph_index=0, rows=rows)


def test_a_variable_table_with_its_answers_in_one_cell():
    fields = parse_spec_table(_table([
        ["Variable", "AGE_BAND"], ["Question type", "Single choice"],
        ["Universe", "IF SVOD_AWARE in (1-6)"], ["Wording", "Which age group are you in?"],
        ["Answers", "1 = Under 16\n2 = 16-24\n3 = None of these (exclusive) (anchor)"],
        ["Scripter instructions", "TERMINATE if AGE_BAND = 1"]]))
    assert [(o.code, o.label) for o in fields.options] == [
        (1, "Under 16"), (2, "16-24"), (3, "None of these")]
    assert fields.options[2].exclusive and fields.options[2].anchored
    assert fields.show_if.to_dict()["values"] == [1, 2, 3, 4, 5, 6]
    assert fields.terminate_if.to_dict()["values"] == [1]


def test_shorthand_types_and_bracketed_codes():
    fields = parse_spec_table(_table([
        ["Question ID", "B2"], ["Type", "SC"], ["Asked of", "ALL"],
        ["Text", "How will your budget change?"],
        ["Codes", "[1] Decrease\n[2] Stay the same\n[3] Increase"]]))
    assert fields.qtype is QuestionType.SINGLE
    assert [o.code for o in fields.options] == [1, 2, 3]
    screen = parse_spec_table(_table([["Question ID", "S0"], ["Type", "TEXT SCREEN"],
                                      ["Text", "Thank you for taking part."]]))
    assert screen.qtype is QuestionType.INFO


# --- what the document profile learns ---------------------------------------

def test_the_profile_learns_where_routing_sits_and_how_notes_are_marked():
    from src.intake.document_profile import build_profile

    above = _doc("[BASE: ALL]", "Q1. Are you?", "Yes", "[BASE: Q1=1]", "Q2. Why?",
                 "[BASE: Q2=1]", "Q3. How?", "[BASE: Q3=1]", "Q4. When?")
    profile = build_profile(above)
    assert profile["routing_position"] == "above"
    assert profile["note_markers"]["[…]"] == 4
    below = _doc("Q1. Are you?", "Yes", "Ask if S1=1", "No", "Q2. Why?", "Ask if Q1=1", "Yes",
                 "Q3. How?", "Yes")
    assert build_profile(below)["routing_position"] == "below"


def test_a_rule_above_a_question_governs_the_next_and_never_itself():
    from src.core.survey_definition import Block, Option, Question, QuestionType, SurveyDefinition
    from src.engines.questionnaire.note_rules import apply

    def q(qid, tail=""):
        return Question(qid=qid, text=qid, type=QuestionType.SINGLE,
                        options=[Option(code=1, label="A"), Option(code=2, label="B")],
                        source_text=f"{qid}?\n1. A\n2. B" + (f"\nFilter: {tail}" if tail else ""))

    qs = [q("Q1", "Q1 = 1"), q("Q2")]
    survey = SurveyDefinition(study_id="s", source="x", status="draft",
                              blocks=[Block(block_id="B", question_ids=["Q1", "Q2"])], questions=qs)
    survey.metadata["document_profile"] = {"routing_position": "above"}
    apply(survey)
    assert qs[0].show_if is None                      # never conditional on itself
    assert qs[1].show_if.to_dict()["qid"] == "Q1"


def test_notes_and_answers_in_the_documents_own_forms():
    chunks = segment_into_questions(_doc(
        "Q.1 Are you? [1 answer]", "Male (1), Female (2)", "Q.2 Pets? [1 answer]",
        "Dog (1), Cat (2)", "(Only show the answers chosen at Q.2)", "Q.3 Which? [1 answer]",
        "Yes (1), No (2)", "→ Asked if Q.2 includes 1"))
    assert "1. Male" in chunks[0].chunk_text and "2. Female" in chunks[0].chunk_text
    assert "[Only show the answers chosen at Q.2]" in chunks[1].chunk_text
    assert "[Asked if Q.2 includes 1]" in chunks[2].chunk_text
    blocks = _doc("Q1 <SC> Why?", "Q2. Rate <BRAND> now <MC>", "A <EXCL>", "B <FIX>")
    text = "\n".join(c.chunk_text for c in segment_into_questions(blocks))
    assert "<BRAND>" in text and "[MC]" in text and "[EXCL]" in text

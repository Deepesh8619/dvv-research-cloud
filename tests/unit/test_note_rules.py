"""Scripter notes applied the way an analyst reads them."""

from src.core.survey_definition import Block, Option, Question, QuestionType, SurveyDefinition
from src.engines.questionnaire.note_rules import apply, rule_words


def _q(qid, notes, qtype=QuestionType.SINGLE, codes=(1, 2, 98, 99)):
    return Question(qid=qid, text=f"{qid}?", type=qtype,
                    options=[Option(code=c, label=f"A{c}") for c in codes],
                    source_text="\n".join(f"[PN: {n}]" for n in notes))


def test_note_conditions_become_rules():
    assert rule_words("selected punch 1-2 at S5d (past 6 months)", "X") == "S5d = 1, 2"
    assert rule_words("S5a=9 AND S5d≠1 or 2", "X") == "S5a = 9 and S5d != 1, 2"
    assert rule_words("punch 1 is not selected at C7", "X") == "C7 != 1"


def test_bases_answers_ranges_and_screen_outs_from_notes():
    qs = [
        _q("S2", ["Create dummy variable called SERVICETYPE:", "INTERNET IF S2=1",
                  "LIVETV IF S2 = 2",
                  "Allow multiple response except punch 99.",
                  "Must select at least punch 1 or 2 to continue, otherwise TERMINATE."],
           QuestionType.MULTI),
        _q("S3", ["ASK S4, S5 and S6 in sequence if SERVICETYPE = 2. If not, skip."]),
        _q("S4", ["Ask if selected punch 1-2 at S3",
                  "Anchor punch 98 ‘Other’ and punch 99 at the bottom."]),
        _q("S5", []),
        _q("AGE", ["Acceptable range: 1-120. If less than 18, terminate."], QuestionType.NUMERIC,
           codes=(99,)),
    ]
    survey = SurveyDefinition(study_id="s", source="x", status="draft",
                              blocks=[Block(block_id="B", question_ids=[q.qid for q in qs])],
                              questions=qs)
    apply(survey)
    by = {q.qid: q for q in qs}
    assert by["S4"].show_if.to_dict()["values"] == [1, 2]          # its own note wins
    assert by["S5"].show_if.to_dict() == {"op": "in", "qid": "S2", "values": [2],
                                           "verbatim_text": "S2 = 2"}   # SERVICETYPE = 2
    assert [o.code for o in by["S4"].options if o.anchored] == [98, 99]
    assert [o.code for o in by["S2"].options if o.exclusive] == [99]
    assert by["S2"].terminate_if.to_dict()["op"] == "not"
    assert (by["AGE"].validation["min"], by["AGE"].validation["max"]) == (1, 120)
    assert by["AGE"].terminate_if.to_dict()["value"] == 18

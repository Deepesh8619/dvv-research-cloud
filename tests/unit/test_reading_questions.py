"""The scripter's notes, read like an analyst: what to build, what to ask."""

from src.core.survey_definition import Block, Option, Question, QuestionType, SurveyDefinition
from src.engines.questionnaire.reading_questions import _rule_words, analyst_reading


def _q(qid, notes, qtype=QuestionType.SINGLE, options=None):
    return Question(qid=qid, text=f"{qid}?", type=qtype, options=options or [],
                    source_text="\n".join(f"[PN: {n}]" for n in notes))


def _survey():
    yes_no = [Option(code=1, label="Yes"), Option(code=2, label="No")]
    services = [Option(code=i, label=x) for i, x in enumerate(
        ["Internet", "TV", "Streaming", "Mobile", "Phone"], 1)]
    qs = [
        _q("QLANG", ["If punch 1 selected show the remainder of the survey in English, if punch 2 "
                     "selected show the remainder of the survey in Spanish."]),
        _q("QAGE", ["Create a dummy hold variable for age ranges, 'AGEBANDS':",
                    "18-34", "35-49", "65+"], QuestionType.NUMERIC),
        _q("S2", ["Create dummy variable called SERVICETYPE:", "INTERNET IF S2=1",
                  "LIVETV IF S2 = 2", "MOBILE IF S2 = 4"], options=services),
        _q("S4", ["ASK S5 in sequence if SERVICETYPE = 2. If not, skip."], options=yes_no),
        _q("S5", ["Create dummy variable “TENURE”:",
                  "<6M: Selected punch 1 at S2 OR S4",
                  ">6M:  Did not select punch 1 at S2 OR S4",
                  "Create dummy variable “PARENTBRAND” using excel file",
                  "Allow multiple responses, flag if more than 3 responses.",
                  "codes as written (1 2 9899) do not match the 4 answers — check the codes"],
           options=yes_no),
    ]
    return SurveyDefinition(study_id="s", source="x", status="draft",
                            blocks=[Block(block_id="B", question_ids=[q.qid for q in qs])],
                            questions=qs)


def test_note_wording_becomes_a_rule():
    assert _rule_words("Selected punch 1 at S5a AND punch 1 at S7") == "S5a = 1 and S7 = 1"
    assert _rule_words("Did not select punch 1 or 2 at S5d OR S6b") == "S5d != 1, 2 and S6b != 1, 2"
    assert _rule_words("punch 1 or 2", "QHHLANG") == "QHHLANG = 1, 2"


def test_variables_are_built_where_the_words_are_exact():
    read = analyst_reading(_survey())
    by = {v["name"]: v for v in read["variables"]}
    assert by["AGEBANDS"]["status"] == "ready"
    assert by["AGEBANDS"]["bands"][2]["condition"] == {
        "op": "gt", "qid": "QAGE", "value": 64, "verbatim_text": "QAGE >= 65"}
    assert by["SERVICETYPE"]["status"] == "ready"
    assert by["TENURE"]["status"] == "ready"
    assert by["TENURE"]["bands"][1]["condition"]["op"] == "and"
    assert by["PARENTBRAND"]["status"] == "needs_you"
    assert "file" in by["PARENTBRAND"]["why"]


def test_what_only_a_person_can_settle_is_asked_with_options():
    read = analyst_reading(_survey())
    kinds = {q["kind"]: q for q in read["questions"]}
    assert "Spanish" in kinds["language"]["ask"]
    assert kinds["group_code"]["options"][0] == "Yes — 2 is LIVETV"
    assert "1 2 9899" in kinds["codes"]["ask"]
    assert kinds["variable"]["variables"] == ["PARENTBRAND"]
    assert [c["kind"] for c in read["checks"]] == ["quality"]


_AGES = "18-34, 35-49, 50-64, 65+, 18-24, 25-34, 35-44, 45-54, 55-64, 65+"
_NEST = "M18-34, F18-34, M35-49, F35-49, M50-64, F50-64, M65+, F65+"


def _nested_survey(form, gender=True):
    if form == "stuck":
        notes = [f"Create a variable called AGEBANDS for age ranges: {_AGES.replace(', ', '')}"
                 f"Create a variable called GENAGE for nested gender and age: "
                 f"{_NEST.replace(', ', '')}"]
    else:
        notes = ["Create a variable called AGEBANDS for age ranges:",
                 *_AGES.split(", "),
                 "Create a variable called GENAGE for nested gender and age:",
                 *_NEST.split(", ")]
    qs = [_q("S1", notes, QuestionType.NUMERIC)]
    if gender:
        qs.append(_q("S2", [], options=[Option(code=1, label="Male"),
                                        Option(code=2, label="Female")]))
    return SurveyDefinition(study_id="s", source="x", status="draft",
                            blocks=[Block(block_id="B", question_ids=[q.qid for q in qs])],
                            questions=qs)


def test_two_lists_and_nested_bands_in_either_form():
    for form in ("stuck", "lines"):
        read = analyst_reading(_nested_survey(form))
        by = {v["name"]: v for v in read["variables"]}
        assert set(by) == {"AGEBANDS", "AGEBANDS_2", "GENAGE"}, form
        assert [len(by[n]["bands"]) for n in ("AGEBANDS", "AGEBANDS_2", "GENAGE")] == [4, 6, 8]
        assert all(v["status"] == "ready" for v in by.values()), form
        asks = [a for a in read["questions"] if a["kind"] == "two_lists"]
        assert len(asks) == 1 and "Keep both" in asks[0]["options"]
        assert by["GENAGE"]["bands"][0]["condition"]["verbatim_text"] == (
            "S2 = 1 AND S1 between 18 and 34")


def test_nested_bands_without_a_gender_question_need_you():
    read = analyst_reading(_nested_survey("lines", gender=False))
    nested = next(v for v in read["variables"] if v["name"] == "GENAGE")
    assert nested["status"] == "needs_you"
    assert "gender question is missing" in nested["why"]

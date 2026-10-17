"""The second look: every flagged question re-checked, solved, or explained once."""

from src.ai.router import AIResponse, BackendUnavailable
from src.core.survey_definition import Expression, Option, Question, QuestionType, SurveyDefinition
from src.engines.questionnaire import schema
from src.engines.questionnaire import second_look as sl
from src.engines.questionnaire.qa_loop import FLAG_QA_ISSUE
from src.engines.questionnaire.routing_prose import parse_routing


def _survey(*questions):
    return SurveyDefinition(study_id="s", questions=list(questions))


def _measure():
    return Question(qid="DA_VID_MEASURE", type=QuestionType.MULTI, text="What do you use?",
                    options=[Option(1, "Google Analytics"), Option(2, "YouTube Analytics"),
                             Option(98, "Other tool"), Option(99, "None of these")],
                    source_text="DA_VID_MEASURE")


_EFFIC_SOURCE = "\n".join([
    "VIDEO MEASUREMENT EFFICACY",
    "Q Name: DA_VID_EFFIC",
    "How effective is this source for measuring your video advertisements?",
    "Variable list:",
    "[INSERT SELECTIONS FROM {name}]",
    "Response list:",
    "10: Extremely effective", "9: (blank)", "8: (blank)", "7: (blank)", "6: (blank)",
    "5: (blank)", "4: (blank)", "3: (blank)", "2: (blank)", "1: Extremely ineffective",
    "Among who: Other >>> | If not All, specify: DA_PLACEMENT = 3 and/or 4 AND 1-98 "
    "selected at {name}",
    "Q type: Custom >>> | If Custom, specify: Sliding scale carousel",
])


def _placement():
    return Question(qid="DA_PLACEMENT", type=QuestionType.MULTI, text="Which ads?",
                    options=[Option(c, f"Ad type {c}") for c in range(1, 6)], source_text="x")


def test_and_or_means_any_of():
    e = parse_routing("IF DA_PLACEMENT = 3 and/or 4")
    assert e is not None and e.qid == "DA_PLACEMENT" and e.values == [3, 4]


def test_a_stale_no_rows_flag_is_cleared_once_the_grid_has_rows():
    q = Question(qid="Q8", type=QuestionType.GRID_SINGLE, text="How do you feel about each?",
                 rows=[Option(1, "Brand A"), Option(2, "Brand B")],
                 cols=[Option(c, str(c)) for c in range(1, 6)],
                 flags=[schema.FLAG_VERIFICATION_DISCREPANCY],
                 validation={"verification_checks": [{"check": "grid_has_rows_and_columns"}]},
                 source_text="Q8. How do you feel about each?\n1 | 2 | 3 | 4 | 5")
    record = sl.second_look(_survey(q), use_model=False)
    assert schema.FLAG_VERIFICATION_DISCREPANCY not in q.flags
    assert record["needs_you"] == 0


def test_an_unread_spec_table_grid_is_read_from_the_question_it_names():
    """DA_VID_EFFIC: rows inserted from DA_VID_MEASURE (name broken over a space),
    a 10-point scale written as a response list, and its base."""
    effic = Question(qid="DA_VID_EFFIC", type=QuestionType.SINGLE, text="VIDEO MEASUREMENT",
                     options=[], flags=[schema.FLAG_EXTRACTION_UNAVAILABLE],
                     source_text=_EFFIC_SOURCE.format(name="DA_VID_  MEASURE"))
    survey = _survey(_placement(), _measure(), effic)
    sl.second_look(survey, use_model=False)
    r = effic.validation["resolution"]
    assert r["status"] == "fixed", r
    assert effic.type is QuestionType.GRID_SINGLE
    assert [o.code for o in effic.rows] == [1, 2, 98]  # "None of these" is not a row
    assert [o.code for o in effic.cols][:2] == [10, 9] and effic.cols[1].label == "9"
    assert effic.text.startswith("How effective")
    assert effic.show_if.qid == "DA_PLACEMENT" and effic.show_if.values == [3, 4]
    assert schema.FLAG_EXTRACTION_UNAVAILABLE not in effic.flags
    assert r["before"]["type"] == "single"  # one click puts it back


def test_a_question_naming_itself_as_its_source_gets_a_fix_to_apply_not_a_silent_change():
    effic = Question(qid="DA_VID_EFFIC", type=QuestionType.GRID_SINGLE, text="How effective?",
                     flags=[schema.FLAG_EMPTY_OPTIONS],
                     source_text=_EFFIC_SOURCE.format(name="DA_VID_EFFIC"))
    sl.second_look(_survey(_measure(), effic), use_model=False)
    r = effic.validation["resolution"]
    assert r["status"] == "ready"
    assert "copy slip" in r["detail"] and "DA_VID_MEASURE" in r["detail"]
    assert not effic.rows  # unchanged until someone applies it
    assert [o["code"] for o in r["proposed"]["rows"]] == [1, 2, 98]


def _hidden():
    return [
        Question(qid="HID_AWARE", type=QuestionType.INFO, text="Hidden",
                 options=[Option(1, "Google Ads Aware"), Option(2, "Meta Ads Aware")],
                 flags=[schema.FLAG_DERIVED_VARIABLE]),
        Question(qid="HID_GOOGLE_USER", type=QuestionType.INFO, text="Hidden",
                 options=[Option(1, "Google Ads Current User"), Option(2, "Google Ads Lapsed User"),
                          Option(3, "Google Ads Aware Non-User")],
                 flags=[schema.FLAG_DERIVED_VARIABLE]),
    ]


def test_a_base_written_as_a_group_name_is_resolved_from_the_hidden_variable():
    q = Question(qid="GA_AI_GOOGLE", type=QuestionType.SINGLE, text="How do you feel?",
                 options=[Option(1, "Good"), Option(2, "Bad")],
                 flags=[schema.FLAG_AMBIGUOUS_ROUTING],
                 validation={"routing_verbatim": "Google Ads aware based on HID_AWARE"},
                 source_text="How do you feel?")
    sl.second_look(_survey(*_hidden(), q), use_model=False)
    assert q.show_if.qid == "HID_AWARE" and q.show_if.values == [1]
    assert schema.FLAG_AMBIGUOUS_ROUTING not in q.flags
    assert q.validation["resolution"]["status"] == "fixed"


def test_a_loop_pass_base_covers_current_and_lapsed_users():
    q = Question(qid="DA_ATT_1", type=QuestionType.SINGLE, text="Agree?",
                 options=[Option(1, "Yes"), Option(2, "No")],
                 flags=[schema.FLAG_AMBIGUOUS_ROUTING],
                 validation={"routing_verbatim": "Provider Current or Lapsed User based on X_USAGE",
                             "loop": {"of": "DA_ATT", "index": 1, "label": "Google Ads"}},
                 source_text="Agree?")
    sl.second_look(_survey(*_hidden(), q), use_model=False)
    assert q.show_if.qid == "HID_GOOGLE_USER" and q.show_if.values == [1, 2]


def test_a_name_used_twice_is_explained_and_no_longer_an_alarm():
    q = Question(qid="S6_2", type=QuestionType.SINGLE, text="Again?",
                 options=[Option(1, "Yes"), Option(2, "No")], flags=[FLAG_QA_ISSUE],
                 source_text="Again?")
    issue = sl.Issue("QID_RENAMED", "the document names two questions S6; this later one "
                                    "was renamed S6_2")
    assert sl.solve_renamed_duplicate(q, [issue]).status == "explained"


def test_a_screen_out_answer_in_a_select_all_list_is_offered_as_exclusive():
    q = Question(qid="Q3", type=QuestionType.MULTI, text="Which? Select all that apply.",
                 options=[Option(1, "A"), Option(2, "B"), Option(10, "None of the above")],
                 terminate_if=Expression(op="in", qid="Q3", values=[10]), source_text="Q3")
    issue = sl.Issue("x", "Q3 screens out on 10 in a select-all list, but those answers are "
                          "not exclusive")
    r = sl.solve_exclusive_screen_out(q, [issue])
    assert r.status == "ready"
    assert [o.exclusive for o in r.after.options] == [False, False, True]
    assert not q.options[2].exclusive


def test_two_answers_with_one_code_are_offered_a_code_nothing_else_uses():
    q = Question(qid="T", type=QuestionType.MULTI, text="Tools?",
                 options=[Option(4, "Gemini"), Option(4, "Microsoft 365"), Option(7, "Azure"),
                          Option(99, "None")], source_text="T")
    r = sl.solve_duplicate_code(q)
    assert [o.code for o in r.after.options] == [4, 8, 7, 99]


def test_a_programmer_note_read_as_a_row_is_removed_and_obeyed():
    q = Question(qid="Q21", type=QuestionType.GRID_SINGLE, text="Concerns?",
                 rows=[Option(1, "[PN: RANDOMIZE]"), Option(2, "Mail order")],
                 cols=[Option(1, "Yes"), Option(2, "No")], source_text="Q21")
    r = sl.solve_note_as_answer(q)
    assert [o.label for o in r.after.rows] == ["Mail order"]
    assert r.after.validation["randomize"] is True


def test_a_missing_name_comes_with_the_likely_real_one():
    qids = ["DA_PLATFORM_USAGE", "C_GEO_ZIP", "C_CUST_TYPE", "HID_AWARE"]
    assert sl._likely_name("DA_USAGE", qids) == "DA_PLATFORM_USAGE"
    assert sl._likely_name("C_ZIP", qids) == "C_GEO_ZIP"
    assert sl._likely_name("CP_CUST_TYPE", qids) == "C_CUST_TYPE"
    assert sl._likely_name("ZZ_NOTHING", qids) is None


def test_a_person_approved_question_is_offered_a_fix_never_changed():
    effic = Question(qid="DA_VID_EFFIC", type=QuestionType.SINGLE, text="x", options=[],
                     flags=[schema.FLAG_EXTRACTION_UNAVAILABLE],
                     source_text=_EFFIC_SOURCE.format(name="DA_VID_MEASURE"))
    survey = _survey(_placement(), _measure(), effic)
    sl.second_look(survey, use_model=False, locked={"DA_VID_EFFIC"})
    assert effic.type is QuestionType.SINGLE
    assert effic.validation["resolution"]["status"] == "ready"


# --- the model, with the questionnaire around it ----------------------------------


def _unread_brand_question():
    prior = Question(qid="Q1", type=QuestionType.MULTI, text="Which brands do you know?",
                     options=[Option(1, "Acme Cola"), Option(2, "Fizz Pop")], source_text="Q1")
    q = Question(qid="Q2", type=QuestionType.SINGLE, text="Which do you prefer?", options=[],
                 flags=[schema.FLAG_EMPTY_OPTIONS],
                 source_text="Q2. Which do you prefer?\n[SHOW BRANDS FROM Q1]")
    return prior, q


def test_the_model_sees_the_questions_named_and_its_fix_is_kept_when_qa_passes(monkeypatch):
    prior, q = _unread_brand_question()
    seen = {}

    def fake(task, prompt, schema_, **_):
        seen["task"], seen["prompt"] = task, prompt
        return AIResponse({"type": "single", "options": [{"code": 1, "label": "Acme Cola"},
                                                         {"code": 2, "label": "Fizz Pop"}],
                           "explanation": "Took the brands from Q1."}, "bedrock", "qwen3")

    monkeypatch.setattr("src.engines.questionnaire.question_agent.run_task", fake)
    sl.second_look(_survey(prior, q), use_model=True)
    assert seen["task"] == "questionnaire.resolve"
    assert "Acme Cola" in seen["prompt"]  # the named question was in view
    r = q.validation["resolution"]
    assert r["status"] == "fixed" and r["source"].startswith("ai:")
    assert [o.label for o in q.options] == ["Acme Cola", "Fizz Pop"]


def test_a_model_answer_with_invented_wording_is_not_used(monkeypatch):
    prior, q = _unread_brand_question()

    def fake(task, prompt, schema_, **_):
        return AIResponse({"type": "single", "options": [{"code": 1, "label": "Acme Cola"},
                                                         {"code": 2, "label": "Mega Soda"}],
                           "explanation": "Added brands."}, "bedrock", "qwen3")

    monkeypatch.setattr("src.engines.questionnaire.question_agent.run_task", fake)
    sl.second_look(_survey(prior, q), use_model=True)
    assert q.validation["resolution"]["status"] == "needs_you"
    assert q.options == []


def test_no_model_reachable_still_explains_in_one_sentence(monkeypatch):
    prior, q = _unread_brand_question()

    def down(*_a, **_k):
        raise BackendUnavailable("down")

    monkeypatch.setattr("src.engines.questionnaire.question_agent.run_task", down)
    sl.second_look(_survey(prior, q), use_model=True)
    r = q.validation["resolution"]
    assert r["status"] == "needs_you" and r["summary"] and r["detail"]


def test_looking_again_at_one_question_keeps_the_rest_of_the_record():
    prior, q = _unread_brand_question()
    other = Question(qid="Q9", type=QuestionType.SINGLE, text="x", options=[], source_text="Q9",
                     flags=[schema.FLAG_EMPTY_OPTIONS])
    survey = _survey(prior, q, other)
    sl.second_look(survey, use_model=False)
    q.options = [Option(1, "Acme Cola"), Option(2, "Fizz Pop")]  # a person fixed Q2
    q.flags = [f for f in q.flags if f != schema.FLAG_EMPTY_OPTIONS]
    record = sl.second_look(survey, use_model=False, only={"Q2"})
    assert sl.RESOLUTION_KEY not in (q.validation or {})
    assert [e["qid"] for e in record["entries"]] == ["Q9"]


def test_an_information_screen_renamed_for_a_duplicate_name_is_not_reported():
    from src.engines.questionnaire.consistency import renamed_duplicate

    q = Question(qid="info_1_2", type=QuestionType.INFO, text="Thank you.",
                 validation={"original_qid": "info_1"})
    assert renamed_duplicate(q, {"info_1_2": q}) == []
    asked = Question(qid="S6_2", type=QuestionType.SINGLE, text="Again?",
                     options=[Option(1, "Yes")], validation={"original_qid": "S6"})
    assert renamed_duplicate(asked, {"S6_2": asked})

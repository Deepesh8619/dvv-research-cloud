"""A person's "Yes" to a model suggestion, through the route; made-up questions only."""

from api.db import AuditLogDB
from src.core.survey_definition import Option, Question, QuestionType, SurveyDefinition
from src.engines.questionnaire.note_ledger import ledger
from src.renderers.surveyjs_compiler import compile_to_surveyjs
from tests.integration.test_fielding_routes import (  # noqa: F401 - `client` is a fixture
    _make_questionnaire,
    _make_study,
    client,
)
from tests.integration.test_users_and_project_delete import _answer, _stored

YES, NO = "Yes", "No, leave it for the scripter"


def _survey(**suggest):
    """Q1 a screener, Q2 a multi with an 'none' answer, each with one unread note."""
    opts = lambda: [Option(1, "A"), Option(2, "B"), Option(99, "None")]  # noqa: E731
    qs = [Question(qid="Q1", text="Pick one?", type=QuestionType.SINGLE, options=opts(),
                   source_text="[PN: Zork the flibbet.]"),
          Question(qid="Q2", text="Pick any?", type=QuestionType.MULTI, options=opts(),
                   source_text="[PN: Blorp the wumble.]")]
    s = SurveyDefinition(study_id="study_f1", questions=qs)
    s.metadata["note_ledger"] = ledger(s)
    suggestions = []
    for i, (qid, said, kind, codes, says) in enumerate(suggest["items"]):
        key = f"key{i:09d}"
        suggestions.append({"key": key, "qid": qid, "said": said, "kind": kind,
                            "qids": [qid], "codes": codes, "says": says})
    s.metadata["note_model"] = suggestions
    s.metadata["decoding"] = {"analyst": {"questions": [
        {"qid": g["qid"], "kind": "note_model", "key": g["key"], "how": "model",
         "ask": "?", "options": [YES, NO]} for g in suggestions]}}
    for r in s.metadata["note_ledger"]["records"]:
        r["outcome"], r["detail"] = "asked", "model suggestion"
    return s


def _setup(sf, *items):
    survey = _survey(items=items)
    _make_study(sf)
    _make_questionnaire(sf, survey=survey)
    return [s["key"] for s in survey.metadata["note_model"]]


def test_yes_sets_exclusive_idempotently_and_is_audited(client):  # noqa: F811
    c, sf, _ = client
    (key,) = _setup(sf, ("Q2", "Blorp the wumble.", "exclusive", [99], "None stands alone."))
    out = _answer(c, key, YES).json()["effect"]
    assert out["status"] == "applied" and out["made"] == ["Q2 exclusive 99"]
    q2 = _stored(sf)["Q2"]
    assert [o.exclusive for o in q2.options] == [False, False, True]
    assert _answer(c, key, YES).json()["effect"]["status"] == "already"
    db = sf()
    rows = db.query(AuditLogDB).filter_by(entity="reading_decision", action="applied").all()
    db.close()
    assert len(rows) == 1 and rows[0].actor_id == "u1"
    records = _stored(sf).metadata["note_ledger"]["records"]
    assert {r["outcome"] for r in records if r["qid"] == "Q2"} == {"applied"}
    compile_to_surveyjs(_stored(sf))


def test_yes_sets_anchor_and_randomize_and_refuses_a_negated_one(client):  # noqa: F811
    c, sf, _ = client
    k1, k2, k3 = _setup(
        sf,
        ("Q2", "Blorp the wumble.", "anchor", [99], "None stays last."),
        ("Q1", "Zork the flibbet.", "randomize", [], "Shuffle the answers."),
        ("Q2", "Do not shuffle.", "randomize", [], "Do not shuffle."))
    assert _answer(c, k1, YES).json()["effect"]["status"] == "applied"
    assert _answer(c, k2, YES).json()["effect"]["status"] == "applied"
    out = _answer(c, k3, YES).json()["effect"]
    assert out["status"] == "not_applied" and "by hand" in out["why"]
    after = _stored(sf)
    assert after["Q2"].options[2].anchored and after["Q1"].randomize_options
    assert not after["Q2"].randomize_options


def test_yes_to_codes_that_are_not_answers_applies_nothing(client):  # noqa: F811
    c, sf, _ = client
    (key,) = _setup(sf, ("Q2", "Blorp the wumble.", "exclusive", [7], "Seven alone."))
    out = _answer(c, key, YES).json()["effect"]
    assert out["status"] == "not_applied" and "by hand" in out["why"]
    assert not any(o.exclusive for o in _stored(sf)["Q2"].options)


def test_yes_terminate_and_base_need_an_exact_rule_on_earlier_questions(client):  # noqa: F811
    c, sf, _ = client
    k1, k2, k3, k4 = _setup(
        sf,
        ("Q2", "End the survey if Q1 = 2", "terminate", [], "Q1 B screens out."),
        ("Q2", "Show only when Q1 = 1", "base", [], "Only Q1 A."),
        ("Q1", "Only for the flibbet-lovers", "base", [], "Some people only."),
        ("Q1", "Ask if Q2 = 1", "base", [], "Those who picked A at Q2."))
    assert _answer(c, k1, YES).json()["effect"]["status"] == "applied"
    assert _answer(c, k1, YES).json()["effect"]["status"] == "already"
    assert _answer(c, k2, YES).json()["effect"]["status"] == "not_applied"  # Q2 has terminate only
    after = _stored(sf)
    assert after["Q2"].terminate_if is not None and after["Q2"].show_if is None
    assert _answer(c, k3, YES).json()["effect"]["status"] == "not_applied"  # no rule in the words
    out = _answer(c, k4, YES).json()["effect"]  # names a LATER question
    assert out["status"] == "not_applied" and "earlier" in out["why"]
    assert _stored(sf)["Q1"].show_if is None
    compile_to_surveyjs(_stored(sf))


def test_no_leaves_it_for_the_scripter(client):  # noqa: F811
    c, sf, _ = client
    (key,) = _setup(sf, ("Q2", "Blorp the wumble.", "exclusive", [99], "None stands alone."))
    assert _answer(c, key, NO).json()["effect"]["status"] == "applied"
    after = _stored(sf)
    assert not any(o.exclusive for o in after["Q2"].options)
    assert any(r["outcome"] == "scripter" and r["qid"] == "Q2"
               for r in after.metadata["note_ledger"]["records"])
    assert any(ch["qid"] == "Q2" for ch in after.metadata["note_ledger"]["checks"])
    assert _answer(c, key, NO).json()["effect"]["status"] == "already"


def test_a_suggestion_that_is_gone_is_stale_and_a_live_survey_is_untouched(client):  # noqa: F811
    c, sf, _ = client
    (key,) = _setup(sf, ("Q2", "Blorp the wumble.", "exclusive", [99], "None stands alone."))
    assert _answer(c, "nokey1234567", YES).json()["effect"]["status"] == "stale"
    assert c.post("/api/studies/study_f1/fielding/links").status_code == 200
    out = _answer(c, key, YES).json()
    assert "live" in out["message"] and out["effect"] is None
    assert not any(o.exclusive for o in _stored(sf)["Q2"].options)


def test_the_yes_is_applied_again_after_a_re_read(client):  # noqa: F811
    from api.db import QuestionnaireDB, get_study
    from api.routes.questionnaire import _persist_reread

    c, sf, _ = client
    (key,) = _setup(sf, ("Q2", "Blorp the wumble.", "exclusive", [99], "None stands alone."))
    _answer(c, key, YES)
    fresh = _survey(items=[("Q2", "Blorp the wumble.", "exclusive", [99], "None stands alone.")])
    assert not any(o.exclusive for o in fresh["Q2"].options)
    db = sf()
    _persist_reread(db, db.get(QuestionnaireDB, "quest_f1"), fresh, [])
    db.commit()
    assert get_study(db, "study_f1").study_metadata["reading_decisions"][key]
    db.close()
    assert _stored(sf)["Q2"].options[2].exclusive

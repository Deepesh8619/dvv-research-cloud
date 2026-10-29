"""Correction loop: founders teach the parser, and the teaching compounds.

The load-bearing test here is `test_no_rules_is_byte_identical_prompt`. The whole
loop is only safe to ship because a profile nobody has corrected produces exactly
the prompt it produced before this feature existed — if that ever breaks, the
correction loop has silently changed extraction for every existing client.
"""

import json
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.auth import UserRole
from api.db import (
    Base,
    CorrectionDB,
    ProfileRuleDB,
    UserDB,
    active_rules_for_profile,
    get_db,
)
from api.main import app
from api.routes.corrections import require_correction_access
from src.ai.router import AIResponse
from src.engines.questionnaire import schema

# --------------------------------------------------------------------------
# The seam: learned rules reach the prompt, and no rules changes nothing
# --------------------------------------------------------------------------


def test_no_rules_is_byte_identical_prompt():
    """A profile with no learned rules must extract exactly as it did pre-loop.

    This is the T32 guarantee inherited by the correction loop. It is what lets
    the feature ship without re-validating extraction for every existing client.
    """
    baseline = schema.build_extraction_prompt("Q1. How old are you?", "Q1", "prose")
    for empty in (None, [], ["", "   "]):
        assert (
            schema.build_extraction_prompt("Q1. How old are you?", "Q1", "prose", empty)
            == baseline
        ), f"learned_rules={empty!r} must be a no-op"


def test_learned_rules_reach_the_prompt():
    prompt = schema.build_extraction_prompt(
        "Q1. How old are you?",
        "Q1",
        "field_spec",
        ["Terminates are written in the TERM row below the options."],
    )
    assert "TERM row below the options" in prompt
    assert "CLIENT-SPECIFIC RULES" in prompt


def test_rules_are_numbered_in_order_and_later_wins_is_stated():
    """Order carries meaning, so it must survive into the prompt."""
    prompt = schema.build_extraction_prompt(
        "Q1.", None, "prose", ["First rule.", "Second rule."]
    )
    assert "1. First rule." in prompt
    assert "2. Second rule." in prompt
    assert "later rule wins" in prompt.lower()


def test_extractor_passes_learned_rules_through():
    """extract_question must hand the rules to the prompt builder, not drop them."""
    from src.engines.questionnaire.extractor import extract_question
    from src.intake.questionnaire_loader import QuestionChunk

    chunk = QuestionChunk(
        chunk_text="Q1. Age?", candidate_qid="Q1", source_blocks=[], provenance="para 1"
    )
    with patch.object(
        schema, "build_extraction_prompt", wraps=schema.build_extraction_prompt
    ) as spy:
        with patch("src.engines.questionnaire.extractor.run_task") as run:
            run.side_effect = KeyError("no backend")  # fail fast; we only want the call
            extract_question(chunk, "field_spec", ["Rule A."])
    assert spy.call_args[0][3] == ["Rule A."]


# --------------------------------------------------------------------------
# active_rules_for_profile — what the extractor actually receives
# --------------------------------------------------------------------------


@pytest.fixture
def db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/corr.db")
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def _rule(slug, text, ordinal, active=True):
    return ProfileRuleDB(
        id=f"rule_{ordinal}_{slug}",
        profile_slug=slug,
        rule_text=text,
        scope="profile",
        ordinal=ordinal,
        active=active,
        created_at="2026-01-01",
    )


def test_active_rules_are_ordered_and_exclude_retired(db):
    db.add_all(
        [
            _rule("gbk_spec_table", "Second.", 1),
            _rule("gbk_spec_table", "First.", 0),
            _rule("gbk_spec_table", "Switched off.", 2, active=False),
            _rule("prose_labelled", "Other profile.", 0),
        ]
    )
    db.commit()
    assert active_rules_for_profile(db, "gbk_spec_table") == ["First.", "Second."]


def test_unknown_profile_has_no_rules(db):
    """No rules for an untaught profile keeps the no-op guarantee reachable."""
    assert active_rules_for_profile(db, "never_seen") == []


# --------------------------------------------------------------------------
# The gate
# --------------------------------------------------------------------------


@pytest.fixture
def client(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/api.db")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)

    def override_db():
        s = Session()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = override_db
    yield TestClient(app), Session
    app.dependency_overrides.clear()


def _as(role):
    return UserDB(id=f"u_{role}", email=f"{role}@dvv.test", name=role, role=role)


@pytest.mark.parametrize("role", [UserRole.ANALYST, UserRole.REVIEWER])
def test_non_founders_are_refused(client, role):
    """The gate is server-side — hiding the button is not access control."""
    c, _ = client
    app.dependency_overrides[require_correction_access] = lambda: (_ for _ in ()).throw(
        __import__("fastapi").HTTPException(status_code=403, detail="nope")
    )
    r = c.post("/api/studies/s1/corrections", json={"instruction": "x"})
    assert r.status_code == 403


def test_founder_may_write_a_correction(client):
    c, Session = client
    from api.db import StudyDB

    s = Session()
    s.add(
        StudyDB(id="s1", client_name="Test", status="draft", created_at="2026-01-01")
    )
    s.commit()
    s.close()

    app.dependency_overrides[require_correction_access] = lambda: _as("founder")
    with patch("api.routes.corrections._classify") as cls:
        cls.return_value = {
            "lane": "live",
            "scope": "profile",
            "rule_text": "Terminates live in the TERM row.",
            "rationale": "Describes a client convention.",
            "confidence": 0.9,
            "degraded": False,
        }
        r = c.post(
            "/api/studies/s1/corrections",
            json={
                "instruction": "terminate not identified, it's in the TERM row",
                "question_id": "Q12",
                "scope": "profile",
            },
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["lane"] == "live"
    assert body["scope"] == "profile"
    assert body["rule_text"] == "Terminates live in the TERM row."


def test_empty_instruction_is_rejected(client):
    c, _ = client
    app.dependency_overrides[require_correction_access] = lambda: _as("founder")
    r = c.post("/api/studies/s1/corrections", json={"instruction": "   "})
    assert r.status_code == 400


# --------------------------------------------------------------------------
# Fail-safe: a degraded model must never auto-apply
# --------------------------------------------------------------------------


def test_classification_failure_routes_to_proposal():
    """Ambiguity and outages both route away from the live lane."""
    from api.routes.corrections import _classify
    from src.ai.router import BackendUnavailable

    with patch("api.routes.corrections.run_task", side_effect=BackendUnavailable("x")):
        verdict = _classify("the terminate is wrong", None, "gbk_spec_table")
    assert verdict["lane"] == "proposal"
    assert verdict["degraded"] is True
    # The author's words survive so the observation is never lost.
    assert verdict["rule_text"] == "the terminate is wrong"


def test_unrecognised_lane_falls_back_to_proposal():
    from api.routes.corrections import _classify

    # The REAL AIResponse, not a hand-rolled stub. An earlier version of this
    # test used a fake class exposing `.data`; the production code read `.data`
    # too, so the test passed green while the live endpoint 500'd on
    # AIResponse's actual `.content`. A double that does not match the real
    # object is worse than no test at all.
    response = AIResponse(
        content={"lane": "yolo", "scope": "nonsense", "rule_text": "r", "rationale": ""},
        backend="ollama",
        model="test",
    )
    with patch("api.routes.corrections.run_task", return_value=response):
        verdict = _classify("x", None, None)
    assert verdict["lane"] == "proposal"
    assert verdict["scope"] == "document"


def test_classifier_reads_the_real_aiproxy_content_field():
    """A well-formed classification must actually be honoured end to end.

    Pairs with the test above: that one proves bad output degrades safely, this
    one proves good output is not ALSO silently degraded — which is what the
    `.data`/`.content` bug did in production.
    """
    from api.routes.corrections import _classify

    response = AIResponse(
        content={
            "lane": "live",
            "scope": "profile",
            "rule_text": "Terminates sit in the TERM row.",
            "rationale": "A client convention.",
        },
        backend="ollama",
        model="test",
    )
    with patch("api.routes.corrections.run_task", return_value=response):
        verdict = _classify("terminates are in the TERM row", None, "gbk_spec_table")
    assert verdict["lane"] == "live"
    assert verdict["scope"] == "profile"
    assert verdict["rule_text"] == "Terminates sit in the TERM row."
    assert verdict["degraded"] is False


def test_any_unexpected_classifier_failure_still_degrades_safely():
    """Never 500: a correction a founder wrote must not be lost to a crash."""
    from api.routes.corrections import _classify

    with patch("api.routes.corrections.run_task", side_effect=AttributeError("boom")):
        verdict = _classify("something", None, None)
    assert verdict["lane"] == "proposal"
    assert verdict["degraded"] is True
    assert verdict["rule_text"] == "something"


# --------------------------------------------------------------------------
# Rules registry
# --------------------------------------------------------------------------


def test_global_scope_requires_owner(client):
    c, _ = client
    app.dependency_overrides[require_correction_access] = lambda: _as("founder")
    r = c.post(
        "/api/profiles/gbk_spec_table/rules",
        json={"rule_text": "Always do X.", "scope": "global"},
    )
    assert r.status_code == 403
    assert "owner" in r.json()["detail"].lower()


def test_owner_may_apply_global_and_rules_append_last(client):
    c, _ = client
    app.dependency_overrides[require_correction_access] = lambda: _as("owner")
    first = c.post(
        "/api/profiles/gbk_spec_table/rules", json={"rule_text": "Rule one."}
    ).json()
    second = c.post(
        "/api/profiles/gbk_spec_table/rules",
        json={"rule_text": "Rule two.", "scope": "global"},
    ).json()
    assert first["ordinal"] == 0
    # Appended last so it wins on conflict, matching what the prompt claims.
    assert second["ordinal"] == 1
    assert second["active"] is True


def test_toggle_switches_a_rule_off_and_back(client):
    c, _ = client
    app.dependency_overrides[require_correction_access] = lambda: _as("owner")
    rule = c.post(
        "/api/profiles/gbk_spec_table/rules", json={"rule_text": "Rule."}
    ).json()
    off = c.post(
        f"/api/profiles/gbk_spec_table/rules/{rule['id']}/toggle"
    ).json()
    assert off["active"] is False
    on = c.post(f"/api/profiles/gbk_spec_table/rules/{rule['id']}/toggle").json()
    assert on["active"] is True


def test_suggestions_lead_with_the_extractors_own_flags():
    """Flagged suggestions rank first — the system already knows where it was unsure."""
    from api.routes.corrections import _flagged_suggestions

    out = _flagged_suggestions(
        {"flags": ["missing_terminate", "low_confidence_options"], "confidence": 0.62}
    )
    texts = [s["text"] for s in out]
    assert any("terminate" in t.lower() for t in texts)
    assert any("option" in t.lower() for t in texts)
    assert all(s["source"] == "flagged" for s in out)


# --------------------------------------------------------------------------
# Preview (P4) — re-read the document under a candidate rule, then diff
# --------------------------------------------------------------------------


def _seed_study_with_questionnaire(Session, definition: dict):
    from api.db import QuestionnaireDB, StudyDB

    s = Session()
    s.add(StudyDB(id="s1", client_name="T", status="draft", created_at="2026-01-01"))
    s.add(
        QuestionnaireDB(
            id="qn1",
            study_id="s1",
            status="draft",
            source_filename="gbk.docx",
            definition_json=json.dumps(definition),
            created_at="2026-01-01",
        )
    )
    s.commit()
    s.close()


def test_preview_refuses_when_source_document_is_gone(client):
    """Render's disk is ephemeral: the definition survives a restart, the .docx does not.

    That must produce an actionable 409, not a 500 — the reviewer needs to be
    told to re-upload, not shown a stack trace.
    """
    c, Session = client
    _seed_study_with_questionnaire(Session, {"questions": [{"qid": "Q1"}]})
    s = Session()
    s.add(
        CorrectionDB(
            id="corr1", study_id="s1", author_id="u", instruction="fix terminates",
            scope="profile", profile_slug="gbk_spec_table", status="resolved",
            created_at="2026-01-01",
        )
    )
    s.commit()
    s.close()

    app.dependency_overrides[require_correction_access] = lambda: _as("founder")
    r = c.post("/api/studies/s1/corrections/corr1/preview")
    assert r.status_code == 409
    assert "re-upload" in r.json()["detail"].lower()


def test_preview_requires_an_existing_correction(client):
    c, Session = client
    _seed_study_with_questionnaire(Session, {"questions": []})
    app.dependency_overrides[require_correction_access] = lambda: _as("founder")
    assert c.post("/api/studies/s1/corrections/nope/preview").status_code == 404


def test_approve_refuses_without_a_completed_preview(client):
    """Nothing may become current until a human has actually seen a diff."""
    c, Session = client
    _seed_study_with_questionnaire(Session, {"questions": []})
    s = Session()
    s.add(
        CorrectionDB(
            id="corr1", study_id="s1", author_id="u", instruction="x",
            scope="profile", profile_slug="gbk_spec_table", status="resolved",
            created_at="2026-01-01",
        )
    )
    s.commit()
    s.close()

    app.dependency_overrides[require_correction_access] = lambda: _as("founder")
    r = c.post("/api/studies/s1/corrections/corr1/preview/approve")
    assert r.status_code == 409


def test_approving_a_ready_preview_applies_rule_and_new_definition(client):
    """The one path where a correction changes what the study holds."""
    from api.db import CorrectionPreviewDB, ProfileRuleDB, QuestionnaireDB

    c, Session = client
    _seed_study_with_questionnaire(Session, {"questions": [{"qid": "Q1"}]})
    candidate = json.dumps({"questions": [{"qid": "Q1", "terminate_if": {"op": "eq"}}]})
    s = Session()
    s.add(
        CorrectionDB(
            id="corr1", study_id="s1", author_id="u",
            instruction="terminates live in the TERM row",
            scope="profile", profile_slug="gbk_spec_table", status="resolved",
            created_at="2026-01-01",
        )
    )
    s.add(
        CorrectionPreviewDB(
            id="prev1", correction_id="corr1", study_id="s1",
            rule_text="terminates live in the TERM row", status="ready",
            candidate_json=candidate, diff_json=json.dumps({"counts": {}}),
            created_at="2026-01-01",
        )
    )
    s.commit()
    s.close()

    app.dependency_overrides[require_correction_access] = lambda: _as("founder")
    r = c.post("/api/studies/s1/corrections/corr1/preview/approve")
    assert r.status_code == 200, r.text
    assert r.json()["applied"] is True

    s = Session()
    # The rule persisted so it applies to FUTURE documents of this format...
    rule = s.query(ProfileRuleDB).filter_by(profile_slug="gbk_spec_table").first()
    assert rule is not None and rule.active is True
    # ...and the previewed extraction became the study's current definition.
    qn = s.query(QuestionnaireDB).filter_by(id="qn1").first()
    assert json.loads(qn.definition_json)["questions"][0]["terminate_if"] == {"op": "eq"}
    s.close()


def test_founder_cannot_approve_a_global_scoped_preview(client):
    """The owner-only gate must hold on the approve path too, not just apply-rule."""
    from api.db import CorrectionPreviewDB

    c, Session = client
    _seed_study_with_questionnaire(Session, {"questions": []})
    s = Session()
    s.add(
        CorrectionDB(
            id="corr1", study_id="s1", author_id="u", instruction="x",
            scope="global", profile_slug="gbk_spec_table", status="resolved",
            created_at="2026-01-01",
        )
    )
    s.add(
        CorrectionPreviewDB(
            id="prev1", correction_id="corr1", study_id="s1", rule_text="x",
            status="ready", candidate_json='{"questions": []}',
            diff_json="{}", created_at="2026-01-01",
        )
    )
    s.commit()
    s.close()

    app.dependency_overrides[require_correction_access] = lambda: _as("founder")
    r = c.post("/api/studies/s1/corrections/corr1/preview/approve")
    assert r.status_code == 403
    assert "owner" in r.json()["detail"].lower()


# --------------------------------------------------------------------------
# Audit trail — the spec calls this a safety property, so it is asserted
# --------------------------------------------------------------------------


def _seed_study(Session, sid="s1"):
    from api.db import StudyDB

    s = Session()
    s.add(StudyDB(id=sid, client_name="T", status="draft", created_at="2026-01-01"))
    s.commit()
    s.close()


def _live_verdict(rule="Terminates live in the TERM row."):
    return {
        "lane": "live", "scope": "profile", "rule_text": rule,
        "rationale": "A client convention.", "confidence": 0.9, "degraded": False,
    }


def test_creating_a_correction_is_audited(client):
    """'Who wrote this, and when' must be answerable without reading logs."""
    from api.db import AuditLogDB

    c, Session = client
    _seed_study(Session)
    app.dependency_overrides[require_correction_access] = lambda: _as("founder")
    with patch("api.routes.corrections._classify", return_value=_live_verdict()):
        c.post("/api/studies/s1/corrections", json={"instruction": "terminates are wrong"})

    s = Session()
    entry = s.query(AuditLogDB).filter_by(entity="correction", action="created").first()
    assert entry is not None
    assert entry.actor_id == "u_founder"
    assert entry.study_id == "s1"
    s.close()


def test_toggling_a_rule_records_before_and_after(client):
    """Switching a rule off is the undo for a bad rule — it must leave a trace."""
    from api.db import AuditLogDB

    c, Session = client
    app.dependency_overrides[require_correction_access] = lambda: _as("owner")
    rule = c.post("/api/profiles/gbk/rules", json={"rule_text": "R."}).json()
    c.post(f"/api/profiles/gbk/rules/{rule['id']}/toggle")

    s = Session()
    entry = s.query(AuditLogDB).filter_by(entity_id=rule["id"], action="disabled").first()
    assert entry is not None
    assert json.loads(entry.before_json) == {"active": True}
    assert json.loads(entry.after_json) == {"active": False}
    s.close()


def test_audit_endpoint_returns_newest_first(client):
    c, Session = client
    _seed_study(Session)
    app.dependency_overrides[require_correction_access] = lambda: _as("founder")
    with patch("api.routes.corrections._classify", return_value=_live_verdict()):
        c.post("/api/studies/s1/corrections", json={"instruction": "first"})
        c.post("/api/studies/s1/corrections", json={"instruction": "second"})

    entries = c.get("/api/corrections/audit?study_id=s1").json()["entries"]
    assert len(entries) == 2
    assert entries[0]["at"] >= entries[1]["at"]


def test_audit_failure_never_breaks_the_action(client):
    """Losing an audit line is bad; failing a founder's approval is worse."""
    from api.db import record_audit

    c, Session = client
    s = Session()
    # A deliberately broken payload must not raise out of record_audit.
    record_audit(
        s, entity="x", entity_id="y", action="z", actor_id="u",
        after={"unserializable": object()},
    )
    s.close()


# --------------------------------------------------------------------------
# Revise — the loop back to the model, carrying the reviewer's note
# --------------------------------------------------------------------------


def test_revise_carries_the_note_into_the_next_classification(client):
    """Revising must start from the last attempt, not beside it."""
    c, Session = client
    _seed_study(Session)
    app.dependency_overrides[require_correction_access] = lambda: _as("founder")
    with patch("api.routes.corrections._classify", return_value=_live_verdict()):
        made = c.post("/api/studies/s1/corrections", json={"instruction": "fix it"}).json()

    with patch("api.routes.corrections._classify", return_value=_live_verdict("v2")) as cls:
        r = c.post(
            f"/api/studies/s1/corrections/{made['id']}/revise",
            json={"note": "this broke Q31, that one uses a different row label"},
        )
    assert r.status_code == 200, r.text
    # The reviewer's note is passed to the classifier, not silently dropped.
    assert cls.call_args.kwargs["revise_note"].startswith("this broke Q31")
    assert r.json()["rule_text"] == "v2"


def test_revise_discards_the_stale_preview(client):
    """A diff built from the OLD rule beside a NEW rule would actively mislead."""
    from api.db import CorrectionPreviewDB

    c, Session = client
    _seed_study(Session)
    app.dependency_overrides[require_correction_access] = lambda: _as("founder")
    with patch("api.routes.corrections._classify", return_value=_live_verdict()):
        made = c.post("/api/studies/s1/corrections", json={"instruction": "fix"}).json()

    s = Session()
    s.add(CorrectionPreviewDB(id="p1", correction_id=made["id"], study_id="s1",
                              rule_text="old", status="ready", diff_json="{}",
                              created_at="2026-01-01"))
    s.commit()
    s.close()

    with patch("api.routes.corrections._classify", return_value=_live_verdict("new")):
        c.post(f"/api/studies/s1/corrections/{made['id']}/revise", json={"note": "no"})

    s = Session()
    assert s.query(CorrectionPreviewDB).filter_by(correction_id=made["id"]).count() == 0
    s.close()


def test_revise_requires_a_note(client):
    c, Session = client
    _seed_study(Session)
    app.dependency_overrides[require_correction_access] = lambda: _as("founder")
    with patch("api.routes.corrections._classify", return_value=_live_verdict()):
        made = c.post("/api/studies/s1/corrections", json={"instruction": "x"}).json()
    r = c.post(f"/api/studies/s1/corrections/{made['id']}/revise", json={"note": "  "})
    assert r.status_code == 400


# --------------------------------------------------------------------------
# Reorder + proposal queue
# --------------------------------------------------------------------------


def test_reorder_sets_ordinals_so_later_rules_win(client):
    """Order is not cosmetic — the prompt tells the model later rules override."""
    c, _ = client
    app.dependency_overrides[require_correction_access] = lambda: _as("owner")
    a = c.post("/api/profiles/gbk/rules", json={"rule_text": "A."}).json()
    b = c.post("/api/profiles/gbk/rules", json={"rule_text": "B."}).json()
    assert (a["ordinal"], b["ordinal"]) == (0, 1)

    out = c.post("/api/profiles/gbk/rules/reorder", json={"rule_ids": [b["id"], a["id"]]})
    assert out.status_code == 200
    assert [r["rule_text"] for r in out.json()["rules"]] == ["B.", "A."]

    listed = c.get("/api/profiles/gbk/rules").json()["rules"]
    assert [r["rule_text"] for r in listed] == ["B.", "A."]


def test_reorder_rejects_rules_from_another_profile(client):
    c, _ = client
    app.dependency_overrides[require_correction_access] = lambda: _as("owner")
    other = c.post("/api/profiles/prose/rules", json={"rule_text": "X."}).json()
    r = c.post("/api/profiles/gbk/rules/reorder", json={"rule_ids": [other["id"]]})
    assert r.status_code == 400


def test_proposal_queue_lists_only_unapplied_proposals(client):
    """The 'it queues for the owner' half of the two-lane design, made real."""
    c, Session = client
    _seed_study(Session)
    app.dependency_overrides[require_correction_access] = lambda: _as("founder")

    proposal = {**_live_verdict("needs code"), "lane": "proposal"}
    with patch("api.routes.corrections._classify", return_value=proposal):
        c.post("/api/studies/s1/corrections", json={"instruction": "add a new question type"})
    with patch("api.routes.corrections._classify", return_value=_live_verdict()):
        c.post("/api/studies/s1/corrections", json={"instruction": "a live one"})

    body = c.get("/api/corrections/proposals").json()
    assert body["count"] == 1
    assert body["proposals"][0]["instruction"] == "add a new question type"
    # The model's structured resolution survives, not just the raw instruction.
    assert body["proposals"][0]["rule_text"] == "needs code"


def test_a_convention_is_understood_from_the_question_as_written():
    """The classifier sees the document's own wording and what can be taught,
    and a proposed convention is kept only when the document contains it."""
    from api.routes.corrections import _classify

    question = {"qid": "QLANG", "type": "single",
                "source_text": "Which language?\n[ALSO] ¿En qué idioma?\n1 English\n2 Spanish",
                "options": [{"code": 1, "label": "[ALSO] ¿En qué idioma?"}]}
    seen = {}

    def model(task, prompt, schema):
        seen["prompt"] = prompt
        return AIResponse(content={
            "lane": "live", "scope": "profile", "rationale": "A client convention.",
            "rule_text": "A line marked [ALSO] is the question in another language.",
            "pattern_kind": "type_signal", "pattern": "[ALSO]", "pattern_value": "info",
        }, backend="t", model="m")

    texts = [("QLANG", question["source_text"]), ("QHH", "[ALSO] ¿Qué idioma?"), ("Q9", "Age?")]
    with patch("api.routes.corrections.run_task", side_effect=model):
        verdict = _classify("the [ALSO] line is a translation, not an answer", question, "gbk",
                            similar=[{"rule_text": "Spanish lines are translations."}],
                            document_texts=texts)
    assert "THE QUESTION AS THE DOCUMENT WRITES IT" in seen["prompt"]
    assert "[ALSO] ¿En qué idioma?" in seen["prompt"]
    assert "question_label" in seen["prompt"]  # what can be taught is offered
    assert "Spanish lines are translations." in seen["prompt"]  # and what already was
    assert verdict["pattern"]["affects"] == ["QLANG", "QHH"]

    # A pattern the document does not contain is never offered.
    def invented(task, prompt, schema):
        return AIResponse(content={"lane": "live", "scope": "profile", "rationale": "x",
                                   "rule_text": "x", "pattern_kind": "type_signal",
                                   "pattern": "[TRANSLATION]", "pattern_value": "info"},
                          backend="t", model="m")

    with patch("api.routes.corrections.run_task", side_effect=invented):
        verdict = _classify("x", question, "gbk", document_texts=texts)
    assert verdict["pattern"] is None


def test_lessons_already_taught_are_found_by_meaning():
    from src.engines.questionnaire.lesson_context import similar_lessons

    rules = [{"id": "a", "rule_text": "Terminates sit in the TERM row under the answers."},
             {"id": "b", "rule_text": "Grids show brands as rows."}]
    found = similar_lessons("the terminate is written in the TERM row", rules)
    assert [r["id"] for r in found] == ["a"]

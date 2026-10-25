"""qa_certificate: expert checks, a 1,000-respondent dry run, then a one-page PDF."""

import hashlib
import html
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.orm import Session

from api.config import STUDIES_DIR
from api.db import JobDB, get_study, list_questionnaires_for_study
from src.engines.questionnaire.logic_proof import summary
from src.platform import ledger
from src.platform.jobs import JobError, handler
from src.platform.workflows import Step, Workflow, register_workflow

N_RESPONDENTS = 1000
SEED = 20260903


def certificate_path(study_id: str) -> Path:
    return Path(STUDIES_DIR) / study_id / "logic_certificate.pdf"


def _approved(db: Session, study_id: str) -> str:
    from api.routes.questionnaire import _read_definition_json

    approved = next(
        (q for q in list_questionnaires_for_study(db, study_id) if q.status == "approved"), None
    )
    payload = _read_definition_json(db, approved) if approved else None
    if not payload:
        raise JobError("This study has no approved questionnaire to check.")
    return payload


@handler("qa.expert_checks")
def _expert_checks(payload: dict, db: Session) -> dict:
    from src.core.survey_definition import SurveyDefinition
    from src.engines.questionnaire.expert_checks import check

    survey = SurveyDefinition.from_json(_approved(db, payload["inputs"]["study_id"]))
    return check(survey)


@handler("qa.dry_run")
def _dry_run(payload: dict, db: Session) -> dict:
    from api.heavy import run_heavy
    from src.core.survey_definition import SurveyDefinition
    from src.engines.fielding.dryrun import simulate
    from src.engines.questionnaire.logic_proof import prove

    survey = SurveyDefinition.from_json(_approved(db, payload["inputs"]["study_id"]))
    r = run_heavy(simulate, survey, [], n=N_RESPONDENTS, seed=SEED)
    try:
        proof = {**run_heavy(prove, survey), "examples": {}}  # examples are for tests
    except Exception:  # the proof is an addition; without it the dry run decides
        proof = None
    return {
        "proof": proof,
        "n_respondents": r.n_respondents,
        "seed": r.seed,
        "dispositions": r.dispositions,
        "never_fired": r.never_fired,
        "impossible": r.impossible,
        "questions_never_shown": r.questions_never_shown,
    }


_CSS = """
* { font-family: sans-serif; color: #14211F; }
.band { background: #0F6E62; color: #FFFFFF; padding: 10px 14px; font-size: 10px;
        letter-spacing: 1px; }
.band b { color: #FFFFFF; }
h1 { font-size: 24px; margin: 18px 0 2px 0; }
h2 { font-size: 13px; margin: 16px 0 6px 0; color: #56635F; }
.sub { font-size: 12px; color: #56635F; }
table { border-collapse: collapse; font-size: 10.5px; margin-top: 10px; }
td { padding: 5px 6px; border-bottom: 1px solid #D5DCD9; vertical-align: top; }
td.k { color: #56635F; }
.verdict { margin-top: 16px; padding: 12px 14px; font-size: 16px; font-weight: bold; }
.ok { background: #DCEFEB; color: #0F6E62; }
.no { background: #F5E1DB; color: #A8442F; }
li { font-size: 10px; margin-bottom: 3px; }
.foot { font-size: 8px; color: #56635F; margin-top: 18px; }
"""


def _html(title: str, facts: list[tuple[str, str]], issued: bool, blockers: list[str],
          notes: list[str], sha: str, version: int) -> str:
    """The certificate page: who, what was tested, the counts, the verdict."""
    e = html.escape
    # Fixed pixel widths: PyMuPDF's HTML layout ignores percentage table widths.
    rows = "".join(f"<tr><td class='k' width='170'>{e(k)}</td><td width='345'>{e(v)}</td></tr>"
                   for k, v in facts)
    verdict = ("<div class='verdict ok'>Verdict: Issued</div>" if issued else
               "<div class='verdict no'>Verdict: Not issued — fix these first</div>"
               + "<ul>" + "".join(f"<li>{e(b)}</li>" for b in blockers) + "</ul>")
    note_html = ("<ul>" + "".join(f"<li>{e(n)}</li>" for n in notes) + "</ul>") if notes \
        else "<p class='sub'>No notes.</p>"
    return (
        "<div class='band'><b>DVV RESEARCH CLOUD</b> &nbsp;·&nbsp; LOGIC CERTIFICATE</div>"
        f"<h1>Logic certificate</h1><div class='sub'>{e(title)}</div>"
        f"<table>{rows}</table>{verdict}<h2>Expert-check notes</h2>{note_html}"
        f"<div class='foot'>This certificate covers questionnaire version {version}, "
        f"sha256 {sha}. Any change to the questionnaire changes that fingerprint, and the "
        "certificate no longer applies. The logic was tested by simulating respondents "
        "through every routing rule, screen-out, mask and quota.</div>"
    )


@handler("qa.certificate")
def _certificate(payload: dict, db: Session) -> dict:
    import fitz

    from src.core.survey_definition import SurveyDefinition

    study_id = payload["inputs"]["study_id"]
    definition = _approved(db, study_id)
    survey = SurveyDefinition.from_json(definition)
    sha = hashlib.sha256(definition.encode()).hexdigest()
    dry, expert = payload["results"]["dry_run"], payload["results"]["expert_checks"]
    impossible = dry["impossible"]
    proof = dry.get("proof")
    if proof:
        # The proof decides. A question the solver could not model still blocks if the
        # dry run never showed it: that is evidence without a proof either way.
        unprovable = {n["qid"] for n in proof["not_provable"]}
        unreachable = [u["qid"] for u in proof["unreachable"]] + [
            q for q in dry["questions_never_shown"] if q in unprovable]
        why = {u["qid"]: u["why"] for u in proof["unreachable"]}
    else:
        unreachable, why = dry["questions_never_shown"], {}
    issued = not unreachable and not impossible
    study = get_study(db, study_id)
    now = datetime.now(UTC)

    notes = expert.get("notes") or []
    impossible_qids = sorted({i["qid"] for i in impossible})

    def listed(items: list[str]) -> str:
        return str(len(items)) + (f" ({', '.join(items)})" if items else "")

    facts = [
        ("Questionnaire", f"version {survey.version}"),
        ("Tested on", f"{now:%d %B %Y}"),
        ("Respondents simulated", f"{dry['n_respondents']:,} (seed {dry['seed']})"),
        ("How they ended", ", ".join(f"{k} {v:,}" for k, v in dry["dispositions"].items())
         or "none"),
        ("Rules that never fired", listed(dry["never_fired"])),
        ("Impossible rules", listed(impossible_qids)),
        ("Unreachable questions", listed(unreachable)),
        ("Solver proof", summary(proof) if proof and not proof["unreachable"]
         else "Not available" if not proof else "Not proved — see below"),
    ]
    blockers = [f"Unreachable: {q} — {why.get(q, 'nobody can be shown it')}"
                for q in unreachable] + [
        f"Impossible: {i['detail']}" for i in impossible]
    shown_notes = [f"{n['qid'] or 'Overall'}: {n['note']}" for n in notes[:12]] + (
        [f"…and {len(notes) - 12} more."] if len(notes) > 12 else [])
    doc = fitz.open()
    page = doc.new_page()  # A4 portrait
    page.insert_htmlbox(fitz.Rect(40, 40, page.rect.width - 40, page.rect.height - 40),
                        _html(survey.title or study.client_name or study_id, facts, issued,
                              blockers, shown_notes, sha, survey.version), css=_CSS)
    path = certificate_path(study_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(path))
    doc.close()

    run_id = db.get(JobDB, payload["job_id"]).workflow_run_id
    cert = {"issued": issued, "at": now.isoformat(), "sha256": sha, "run_id": run_id}
    study.study_metadata = {**(study.study_metadata or {}), "certificate": cert}
    ledger.issue(db, study_id, path)
    db.commit()
    return cert


register_workflow(Workflow("qa_certificate", (
    Step("expert_checks", "qa.expert_checks"),
    Step("dry_run", "qa.dry_run", ("expert_checks",)),
    Step("certificate", "qa.certificate", ("expert_checks", "dry_run")),
)))

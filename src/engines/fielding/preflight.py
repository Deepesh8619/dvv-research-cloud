"""Everything checked once more, the moment before a survey goes to a panel.

Review happens question by question, over days, by more than one person. A
link pins what was approved at the second it is created, and from then on
every problem is paid for in respondents. So this reads the WHOLE instrument
again, as the respondent and the data file will meet it, and says what is
wrong before anyone is sent the link.

Two severities, deliberately few:

* FAIL — the survey does not do what the document says: a question with no
  answers, a condition nobody can meet, a blocking flag nobody resolved. The
  link is refused unless someone overrides it on purpose.
* WARN — probably fine and worth a look: a list the system copied without the
  document naming it, a pipe with nothing to fill it, a screen-out the dry run
  never saw fire.

Every check reports the questions it is about, so the page can take the
reviewer straight to them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.core.survey_definition import QuestionType, SurveyDefinition
from src.engines.fielding.dryrun import simulate
from src.engines.questionnaire import schema
from src.engines.questionnaire.definition_audit import audit_definition
from src.renderers.surveyjs_compiler import display_warnings

PASS, WARN, FAIL = "pass", "warn", "fail"
_CHOICE = {QuestionType.SINGLE, QuestionType.MULTI, QuestionType.DROPDOWN, QuestionType.RANK}
#: Guesses the system made and a person should confirm.
_UNCONFIRMED = {
    schema.FLAG_LIST_INHERITED: "a list copied from another question the document did not name",
    schema.FLAG_DERIVED_VARIABLE_UNCONFIRMED: "read as a hidden variable from its wording alone",
}
_DRY_RUN_N = 300


#: A finding's check id, said as the thing a reviewer is looking for.
_AUDIT_LABELS = {
    "per_item_question_flattened": "Questions asked about EACH thing still have their items",
    "mask_cannot_narrow": "Every mask points at the list it narrows",
    "prose_as_option": "No paragraph is stored as an answer",
    "scripter_tag_in_label": "No scripter tag is left in an answer label",
    "base_stated_but_not_applied": "Every base the document states is applied",
    "base_stated_in_words_only": "No base is left as prose",
    "section_path_unapplied": "Every section path is applied",
    "self_referential_base": "No question is its own base",
    "exclusive_on_single": "No exclusive option on a single-choice question",
    "option_base_unsupported": "Every option-level base is supported",
    "screening_requirement_not_enforced": "Every screening requirement is enforced",
}


@dataclass
class Check:
    id: str
    label: str
    status: str = PASS
    detail: str = ""
    qids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "label": self.label, "status": self.status,
                "detail": self.detail, "qids": self.qids}


def _result(check: Check, qids: list[str], status: str, detail: str) -> Check:
    if qids:
        check.status, check.qids, check.detail = status, sorted(set(qids)), detail
    return check


def preflight(
    survey: SurveyDefinition,
    approved: bool,
    blocking_flags: frozenset[str] = frozenset(),
    approved_qids: set[str] | None = None,
) -> dict[str, Any]:
    """Every check, in the order a reviewer would want to read them."""
    checks: list[Check] = []
    answerable = [q for q in survey.questions if q.type is not QuestionType.INFO]

    approval = Check("approved", "The questionnaire is approved")
    if not approved:
        approval.status, approval.detail = FAIL, "Approve the questionnaire before fielding it."
    checks.append(approval)

    problems = survey.validate()
    structure = Check("structure", "The definition is internally consistent")
    if problems:
        structure.status = FAIL
        structure.detail = "; ".join(problems[:4]) + (" …" if len(problems) > 4 else "")
        structure.qids = sorted(
            {q.qid for q in survey.questions if any(f"question {q.qid}:" in p for p in problems)}
        )
    checks.append(structure)

    empty = [
        q.qid for q in answerable
        if (q.type is QuestionType.GRID_SINGLE and not (q.rows and q.cols))
        or (q.type in _CHOICE and not q.options)
    ]
    checks.append(_result(
        Check("answers", "Every question has answers to choose from"), empty, FAIL,
        "These would reach a respondent with nothing to pick.",
    ))

    blocked = [q.qid for q in survey.questions if blocking_flags & set(q.flags or ())]
    signed_off = approved_qids or set()
    open_flags = [qid for qid in blocked if qid not in signed_off]
    if open_flags:
        checks.append(_result(
            Check("flags", "No unresolved blocking flags"), open_flags, FAIL,
            "Resolve or confirm the flagged routing on these questions.",
        ))
    else:
        # Approved past its flag: a person decided it was right. Worth seeing
        # once more, not worth refusing the link over.
        checks.append(_result(
            Check("flags", "No unresolved blocking flags"), blocked, WARN,
            "Approved with a routing flag still open — confirm the routing is what you meant.",
        ))

    unconfirmed = [q.qid for q in survey.questions if _UNCONFIRMED.keys() & set(q.flags or ())]
    checks.append(_result(
        Check("guesses", "Nothing the system inferred is left unconfirmed"), unconfirmed, WARN,
        "Each is " + " or ".join(sorted(set(_UNCONFIRMED.values()))) + ".",
    ))

    # The defect fingerprints an extraction leaves behind. These were computed
    # by a script nobody ran; a pattern that says "the per-pharmacy grid was
    # flattened into one answer" belongs in front of the person about to open
    # a link, not in a CLI. Warnings, not failures: each is a strong signal
    # rather than a certainty, and a reviewer who has looked can proceed.
    for finding in audit_definition(survey):
        checks.append(_result(
            Check(f"audit_{finding.check}", _AUDIT_LABELS.get(finding.check, finding.check)),
            finding.qids, WARN, finding.detail,
        ))

    struck = (survey.metadata or {}).get("struck_out") or []
    if struck:
        sample = "; ".join(t[:40] for t in struck[:3])
        checks.append(_result(
            Check("struck_out", "Nothing struck out of the document is still in the survey"),
            ["document"], WARN,
            f"{len(struck)} fragment{'s' if len(struck) != 1 else ''} were struck through in "
            f"the document and left out — e.g. {sample}. Confirm that is what the client meant.",
        ))

    warnings = display_warnings(survey)
    checks.append(_result(
        Check("display", "Every pipe and mask has something to show"),
        [w["qid"] for w in warnings], WARN,
        warnings[0]["detail"] if warnings else "",
    ))

    run = simulate(survey, n=_DRY_RUN_N, seed=20260918)
    impossible = [i.get("qid") for i in run.impossible if i.get("qid")]
    checks.append(_result(
        Check("possible", "Every condition can be met by someone"), impossible, FAIL,
        run.impossible[0]["detail"] if run.impossible else "",
    ))
    checks.append(_result(
        Check("reached", "Every question is reached in a dry run"),
        run.questions_never_shown, WARN,
        f"None of {_DRY_RUN_N} synthetic respondents was shown these.",
    ))
    checks.append(_result(
        Check("screen_outs", "Every screen-out fires in a dry run"),
        [c.qid for c in run.conditions if c.kind == "screen_out" and c.fired == 0], WARN,
        f"Not triggered once in {_DRY_RUN_N} synthetic respondents — check the rule.",
    ))

    fails = [c for c in checks if c.status == FAIL]
    return {
        "ok": not fails,
        "n_fail": len(fails),
        "n_warn": sum(c.status == WARN for c in checks),
        "checks": [c.to_dict() for c in checks],
    }

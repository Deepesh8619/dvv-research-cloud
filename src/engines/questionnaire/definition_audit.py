"""Find questionnaires that were extracted before a parser fix landed.

A parser fix only changes what the NEXT extraction produces. Every definition
already sitting in a database keeps whatever the old parser wrote — an intro
paragraph stored as an answer option stays an answer option, a recruitment path
that was never applied stays unapplied, and nothing announces it.

So after an upgrade the honest question is not "is the parser fixed?" but "which
of MY studies are still wrong?". These checks answer that, by looking for the
fingerprints each defect leaves behind. They are deliberately conservative: a
finding means "re-extract and compare", never "this is automatically rewritten".
Nothing here mutates a definition — a draft a reviewer has already corrected by
hand must not be silently overwritten by a re-run.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from src.core.survey_definition import QuestionType, SurveyDefinition
from src.engines.questionnaire.prose_directives import has_sentence_break, is_note_line

# A recruitment path header: "... (IF S0=1 AND S1=26-60) ASK P1-P3".
_SECTION_PATH = re.compile(r"\(\s*[^)]*\bIF\b[^)]*\)\s*[,:]?\s*ASK\s+[A-Za-z]{0,4}\d", re.I)
# A scripter tag left sitting in respondent-facing text.
_LEFTOVER_TAG = re.compile(r"\[\s*(ANCHOR|EXCLUSIVE|TERMINATE|PN\b)[^\]]*\]", re.I)
# Prose, not an answer: long and sentence-shaped (mirrors the parser's own rule).
_PROSE_WORDS = 18


@dataclass
class Finding:
    check: str
    qids: list[str]
    detail: str


@dataclass
class StudyAudit:
    study_id: str
    questionnaire_id: str
    status: str
    n_questions: int
    findings: list[Finding] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return not self.findings


def _is_prose(text: str) -> bool:
    """Same rule the parser uses — one definition, so the audit cannot disagree
    with the parser about what an answer option is."""
    if is_note_line(text):
        return True
    return len(text.split()) >= _PROSE_WORDS and has_sentence_break(text)


def audit_definition(survey: SurveyDefinition) -> list[Finding]:
    """Every defect fingerprint this definition still carries."""
    findings: list[Finding] = []

    prose = [
        q.qid
        for q in survey.questions
        for o in q.options or []
        if _is_prose(o.label or "")
    ]
    if prose:
        findings.append(
            Finding(
                "prose_as_option",
                sorted(set(prose)),
                "A paragraph is stored as an answer option — an intro screen read as a "
                "punch. Those codes exist in no data map and break the question's type.",
            )
        )

    tagged = [
        q.qid
        for q in survey.questions
        for o in q.options or []
        if _LEFTOVER_TAG.search(o.label or "")
    ]
    if tagged:
        findings.append(
            Finding(
                "scripter_tag_in_label",
                sorted(set(tagged)),
                "A scripter tag is still inside an option label, so the respondent sees "
                "it and nothing acts on what it declared.",
            )
        )

    unrouted = [
        q.qid
        for q in survey.questions
        if _SECTION_PATH.search(q.source_text or "")
        and not any(
            other.show_if is not None
            for other in survey.questions
            if other.qid != q.qid
        )
    ]
    # A section header with NO routed question anywhere is the strong signal; a
    # partially routed document is reported per header instead.
    headers = [q.qid for q in survey.questions if _SECTION_PATH.search(q.source_text or "")]
    if headers and not survey.metadata.get("section_paths"):
        findings.append(
            Finding(
                "section_path_unapplied",
                sorted(set(headers)),
                "A recruitment path header was read as text and never applied, so the "
                "questions it governs are asked of everybody — and the path is missing "
                "from the data as a hidden variable.",
            )
        )
    elif unrouted:
        findings.append(
            Finding("section_path_unapplied", sorted(set(unrouted)), "As above.")
        )

    exclusive_single = [
        q.qid
        for q in survey.questions
        if q.type in (QuestionType.SINGLE, QuestionType.DROPDOWN)
        and any(o.exclusive for o in q.options or [])
    ]
    if exclusive_single:
        findings.append(
            Finding(
                "exclusive_on_single",
                sorted(set(exclusive_single)),
                "EXCLUSIVE is meaningless in a single-select: the question is a "
                "multi-punch typed wrongly, and its answers land in one column as null.",
            )
        )

    self_based = [
        q.qid
        for q in survey.questions
        if q.show_if is not None and q.qid in q.show_if.referenced_qids()
    ]
    if self_based:
        findings.append(
            Finding(
                "self_referential_base",
                sorted(set(self_based)),
                "A question is its own condition — a directive that belonged to the "
                "next screen was read as this question's base.",
            )
        )

    return findings


def audit_all(rows) -> list[StudyAudit]:
    """`rows` are questionnaire records with id / study_id / status / definition_json."""
    audits: list[StudyAudit] = []
    for row in rows:
        raw = getattr(row, "definition_json", None)
        if not raw:
            continue
        survey = SurveyDefinition.from_json(raw)
        audits.append(
            StudyAudit(
                study_id=getattr(row, "study_id", "?"),
                questionnaire_id=getattr(row, "id", "?"),
                status=getattr(row, "status", "?"),
                n_questions=len(survey.questions),
                findings=audit_definition(survey),
            )
        )
    return audits


def format_report(audits: list[StudyAudit]) -> str:
    affected = [a for a in audits if not a.clean]
    lines = [f"{len(audits)} questionnaire(s) audited, {len(affected)} affected.", ""]
    for audit in affected:
        lines.append(f"  {audit.study_id}  [{audit.status}]  {audit.n_questions} questions")
        for finding in audit.findings:
            lines.append(f"    - {finding.check}: {', '.join(finding.qids)}")
            lines.append(f"      {finding.detail}")
        lines.append("")
    if affected:
        lines += [
            "These were extracted before the parser fix that covers them.",
            "Re-extract each affected questionnaire and compare against the current",
            "definition before approving — nothing here has been changed for you.",
            "A questionnaire already FIELDED needs care: its link pins its own survey",
            "copy, so re-extraction does not alter data already collected.",
        ]
    return "\n".join(lines)

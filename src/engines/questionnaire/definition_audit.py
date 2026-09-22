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
from src.engines.questionnaire.study_overview import (
    StudyOverview,
    unenforced_requirements,
)

# A recruitment path header: "... (IF S0=1 AND S1=26-60) ASK P1-P3".
_SECTION_PATH = re.compile(r"\(\s*[^)]*\bIF\b[^)]*\)\s*[,:]?\s*ASK\s+[A-Za-z]{0,4}\d", re.I)
# A scripter tag left sitting in respondent-facing text.
_LEFTOVER_TAG = re.compile(r"\[\s*(ANCHOR|EXCLUSIVE|TERMINATE|PN\b)[^\]]*\]", re.I)
# Prose, not an answer: long and sentence-shaped (mirrors the parser's own rule).
_PROSE_WORDS = 18
# "[ASK IF Q3=1]", "[SHOW IF S11=1]", "[PN: ONLY ASK IF ...]" — a base stated in
# the source. Deliberately narrow: an exact qid comparison, not prose about one.
# Keyed on the COMPARISON, not on the qid's shape: a real questionnaire names
# questions "QP" and "C1" alike, and requiring letters-then-digits missed
# "[SHOW IF QP=1]" entirely. An exact comparison is what makes a base readable;
# prose about one ("[ASK IF MULTIPLE AT S6 SELECTED]") is not, and must not be
# reported as something the parser should have applied.
_BASE_STATED = re.compile(
    r"\[[^\]]*\b(?:ASK|SHOW|DISPLAY)\s+(?:ONLY\s+)?IF\b[^\]]*"
    r"[A-Za-z][A-Za-z0-9]{0,5}\s*=\s*\d[^\]]*\]",
    re.I,
)
# A base stated in WORDS, naming a question but no code: "[ASK IF MULTIPLE AT S6
# SELECTED]", "[SHOW IF FILLED AT LEAST ONE PRESCRIPTION TYPE AT Q14]". No
# parser should invent a condition from it — but it was invisible to the check
# above, which needs an exact comparison, so a question the document plainly
# gates went to everybody with nothing said about it. It is reported
# SEPARATELY: the answer is a person writing the rule, not a re-extraction.
_BASE_IN_WORDS = re.compile(
    r"\[[^\]]*\b(?:ASK|SHOW|DISPLAY)\s+(?:ONLY\s+)?IF\b[^\]]*"
    r"\b[A-Za-z]{1,6}\d+[A-Za-z]?\b[^\]]*\]",
    re.I,
)


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


def _on_an_option_line(question, match) -> bool:
    """True when the condition sits on an option's line, not the question's.

    The distinction matters for what a reviewer does next: a question base that
    was missed is a parser bug worth re-extracting for, while a base written on
    one answer option is a feature the platform does not have. Reporting them as
    the same thing sends people looking for the wrong fix.
    """
    source = question.source_text or ""
    line_start = source.rfind("\n", 0, match.start()) + 1
    line_end = source.find("\n", match.end())
    line = source[line_start : line_end if line_end != -1 else len(source)]
    stripped = line[: match.start() - line_start].strip(" \t.-")
    return any(
        (o.label or "").strip() and (o.label.strip()[:30] in stripped)
        for o in question.options or []
    )


def _is_prose(text: str) -> bool:
    """Same rule the parser uses — one definition, so the audit cannot disagree
    with the parser about what an answer option is."""
    if is_note_line(text):
        return True
    return len(text.split()) >= _PROSE_WORDS and has_sentence_break(text)


#: "How familiar are you with each of these pharmacies?" — a question asked
#: once PER ITEM. Held as a single answer it is one column where there should
#: be one per pharmacy, and the respondent is asked about all of them at once.
_PER_ITEM = re.compile(
    r"\b(?:for |with |about )?each of (?:these|the following|those|them)\b|"
    # "have you personally used each one?" — the list is named earlier in the
    # sentence ("these pharmacies"), and "each one" is what makes it per-item.
    r"\beach one\b|"
    r"\bfor each\b|\bper (?:brand|item|statement)\b",
    re.I,
)


def _flattened_grids(survey: SurveyDefinition) -> list[str]:
    """Questions whose wording asks about EACH thing but hold one answer.

    Seen on a live pharmacy study: "How familiar are you with each of these
    pharmacies?" came back as a single-choice question whose options were the
    familiarity scale. The pharmacies were gone — no rows, no columns for
    them, and a respondent asked to rate all of them with one click.
    """
    flattened = []
    for q in survey.questions:
        if q.type not in (QuestionType.SINGLE, QuestionType.MULTI, QuestionType.DROPDOWN):
            continue
        if not _PER_ITEM.search(q.text or ""):
            continue
        if q.rows:  # it kept its items: nothing lost
            continue
        flattened.append(q.qid)
    return flattened


def _masks_that_cannot_narrow(survey: SurveyDefinition) -> list[str]:
    """Masks whose source cannot possibly be the list they claim to narrow.

    "Show the pharmacies they have heard of" pointing at a Yes/No question:
    the source offers two answers and the list has thirteen rows, so whatever
    the mask does it is not what it says. Found on a live study, where the
    per-pharmacy grid had been flattened into a Yes/No question and the mask
    still pointed at it.
    """
    by_qid = {q.qid.upper(): q for q in survey.questions}
    wrong = []
    for q in survey.questions:
        own = len(q.options or q.rows or [])
        for mask in ((q.validation or {}).get("techniques") or {}).get("masks") or []:
            if mask.get("mode") == "full_list":
                continue
            against = mask.get("source_qid") or mask.get("exclude_qid") or ""
            source = by_qid.get(str(against).upper())
            if source is None:
                continue
            offered = len(source.options or source.rows or [])
            if offered and own and offered < own / 2:
                wrong.append(q.qid)
    return wrong


def audit_definition(survey: SurveyDefinition) -> list[Finding]:
    """Every defect fingerprint this definition still carries."""
    findings: list[Finding] = []

    flattened = _flattened_grids(survey)
    if flattened:
        findings.append(
            Finding(
                "per_item_question_flattened",
                sorted(set(flattened)),
                "The wording asks about EACH of several things, but the question holds "
                "one answer: the items are gone, and so are their data columns.",
            )
        )

    unnarrowable = _masks_that_cannot_narrow(survey)
    if unnarrowable:
        findings.append(
            Finding(
                "mask_cannot_narrow",
                sorted(set(unnarrowable)),
                "A mask points at a question with far fewer answers than the list it "
                "claims to narrow, so it is not narrowing what it says it does.",
            )
        )

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

    stated_not_applied: list[str] = []
    option_level: list[str] = []
    for q in survey.questions:
        if q.show_if is not None or q.type is QuestionType.INFO:
            continue
        match = _BASE_STATED.search(q.source_text or "")
        if not match:
            continue
        if _on_an_option_line(q, match):
            option_level.append(q.qid)
        else:
            stated_not_applied.append(q.qid)

    stated_in_words = [
        q.qid
        for q in survey.questions
        if q.show_if is None
        and q.type is not QuestionType.INFO
        and q.qid not in stated_not_applied
        and q.qid not in option_level
        and not _BASE_STATED.search(q.source_text or "")
        and _BASE_IN_WORDS.search(q.source_text or "")
    ]
    if stated_in_words:
        findings.append(
            Finding(
                "base_stated_in_words_only",
                sorted(set(stated_in_words)),
                "The source gates this question in words rather than codes — "
                '"ASK IF MULTIPLE AT S6 SELECTED" — so no parser can turn it into a '
                "condition without guessing what it means. It currently goes to "
                "everyone. Somebody has to write the rule.",
            )
        )

    if option_level:
        findings.append(
            Finding(
                "option_base_unsupported",
                sorted(set(option_level)),
                "A condition is stated on an ANSWER OPTION ('Yes - for a child under 18 "
                "[ONLY SHOW IF S11=1]'), not on the question. Per-option display is not "
                "something the platform models — this needs a scripting decision, not a "
                "re-extraction.",
            )
        )
    if stated_not_applied:
        findings.append(
            Finding(
                "base_stated_but_not_applied",
                sorted(set(stated_not_applied)),
                "The source text states a base for this question and none was applied, "
                "so it goes to everyone. The strongest check available: the document "
                "says who should see it, and the definition disagrees.",
            )
        )

    brief = survey.metadata.get("study_overview") or {}
    if brief.get("screening_requirements"):
        overview = StudyOverview(
            screening_requirements=list(brief["screening_requirements"])
        )
        unmet = unenforced_requirements(overview, survey)
        if unmet:
            findings.append(
                Finding(
                    "screening_requirement_not_enforced",
                    [],
                    "The study overview states who should qualify and nothing in the "
                    "instrument screens on it: " + "; ".join(unmet) + ". Either the "
                    "screen-out is missing, or it is handled by a quota — worth one "
                    "look before a single respondent is paid for.",
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

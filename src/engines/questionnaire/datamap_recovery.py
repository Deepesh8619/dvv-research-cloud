"""Reconcile a questionnaire against its data map, in both directions.

Extraction reads a Word document and guesses what is a question. The data map
does not guess: it is the script's own record of what was actually fielded —
every variable name, the question text, every option label with its punch code,
and the hidden variables the script computed. On a real client study the data
map described 113 questions while extraction had produced 24.

So the two halves inform each other:

    data map -> questionnaire   which questions SHOULD exist, and what they are
    questionnaire -> data map   which conditions, masks and routing were asked

A question the data map describes and extraction missed does not need to be
guessed at a second time. Its text and its options are already known, exactly
as they were fielded, and can be rebuilt.

**Recovered questions are marked, never silently merged.** A rebuilt question
carries `extraction_method="datamap"` and the RECOVERED_FROM_DATAMAP flag so a
reviewer can see that its wording came from the script rather than the client's
document — the two can legitimately differ, and pretending otherwise would hide
a real disagreement. Routing is never invented: the data map records what was
collected, not who was asked, so a recovered question has no `show_if` and says
so in `missing`.

Deterministic; no model is called.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from src.core.models import DataMap, Variable
from src.core.survey_definition import (
    Option,
    Question,
    QuestionType,
    QuestionVariableLink,
    SurveyDefinition,
)
from src.engines.qc.variable_roles import classify_variable

#: Set on any question rebuilt from the data map rather than read from the document.
FLAG_RECOVERED = "RECOVERED_FROM_DATAMAP"

#: Roles that represent something a respondent was actually asked.
_ANSWERABLE = frozenset({"question", "punch"})


@dataclass
class DatamapQuestion:
    """One question as the data map describes it."""

    qid: str
    text: str
    variables: list[Variable] = field(default_factory=list)

    @property
    def is_multi(self) -> bool:
        return len(self.variables) > 1


@dataclass
class Reconciliation:
    """What each side knows that the other does not."""

    #: Questions the data map describes that extraction did not produce.
    missing_from_questionnaire: list[DatamapQuestion] = field(default_factory=list)
    #: Extracted qids with no matching variable — a wrong read, or a display item.
    missing_from_datamap: list[str] = field(default_factory=list)
    #: Questions both sides agree exist.
    matched: list[str] = field(default_factory=list)

    def summary(self) -> dict:
        return {
            "in_both": len(self.matched),
            "only_in_datamap": len(self.missing_from_questionnaire),
            "only_in_questionnaire": len(self.missing_from_datamap),
        }


#: A punch column of a multi-select: the stem is the question, the digits are
#: the code. `materialize` WRITES this convention ("{qid}r{code}, Decipher
#: multi-punch naming so questionnaire<->data linking works for free") and the
#: linker reads it; only recovery did not.
_PUNCH_COLUMN = re.compile(r"^(?P<stem>.+?)[rc_](?P<code>\d+)$", re.I)


def _punch_stems(variables: list[Variable]) -> dict[str, str]:
    """Column name -> the question it is one punch of, where there is one.

    Only where a `question_ref` is absent, because an adapter that supplies one
    knows better than a naming convention does. And only for a stem with SEVERAL
    such columns that is not itself a column: a lone `Q5r1` is not evidence of a
    multi, and a file holding both `Q5` and `Q5r1` is describing two different
    things.

    Without this a 22-option multi-punch came back as 22 separate yes/no
    questions, each labelled with one of its own OPTIONS — on a real client
    study, 483 punch columns arriving as 483 questions from 46 real ones.
    """
    named = {v.name for v in variables}
    candidates: dict[str, list[str]] = {}
    for variable in variables:
        if getattr(variable, "question_ref", None):
            continue
        found = _PUNCH_COLUMN.match(variable.name)
        if found and found.group("stem") not in named:
            candidates.setdefault(found.group("stem"), []).append(variable.name)
    return {
        name: stem
        for stem, names in candidates.items()
        if len(names) > 1
        for name in names
    }


def questions_from_datamap(datamap: DataMap) -> list[DatamapQuestion]:
    """Group answerable columns into the questions they belong to.

    A multi-select is one question over many punch columns; everything else is
    one question per column. Hidden variables, MaxDiff machinery, system fields
    and open-end companions are excluded — they were never asked.
    """
    answerable = [
        variable
        for variable in datamap.variables
        if classify_variable(
            variable.name, variable.label or "", getattr(variable, "question_ref", None)
        )
        in _ANSWERABLE
    ]
    stems = _punch_stems(answerable)

    grouped: dict[str, list[Variable]] = {}
    for variable in answerable:
        key = (
            getattr(variable, "question_ref", None)
            or stems.get(variable.name)
            or variable.name
        )
        grouped.setdefault(key, []).append(variable)

    out: list[DatamapQuestion] = []
    for qid, variables in grouped.items():
        # For a multi the parent header holds the question; for a single the
        # variable's own label is the question.
        text = ""
        for variable in variables:
            text = getattr(variable, "question_text", None) or ""
            if text:
                break
        if not text and len(variables) == 1:
            # One column: its own label IS the question. Several columns: each
            # label is an OPTION, and using the first would call the question
            # "Bubble" because that is the first brand in the list.
            text = variables[0].label or ""
        out.append(DatamapQuestion(qid=qid, text=text, variables=list(variables)))
    return sorted(out, key=lambda q: q.qid)


def normalize_qid(qid: str) -> str:
    return qid.strip().lower().replace("_", "").replace("-", "")


def reconcile(survey: SurveyDefinition, datamap: DataMap) -> Reconciliation:
    """Compare both sides by question id, tolerantly."""
    extracted = {normalize_qid(q.qid): q.qid for q in survey.questions}
    result = Reconciliation()

    for candidate in questions_from_datamap(datamap):
        if normalize_qid(candidate.qid) in extracted:
            result.matched.append(candidate.qid)
        else:
            result.missing_from_questionnaire.append(candidate)

    described = {normalize_qid(q.qid) for q in questions_from_datamap(datamap)}
    for question in survey.questions:
        if normalize_qid(question.qid) not in described:
            result.missing_from_datamap.append(question.qid)
    return result


def rebuild_question(candidate: DatamapQuestion) -> Question:
    """Rebuild one question from what the data map records about it.

    Options come from the punch labels for a multi, and from the value labels
    for a single — in both cases these are the codes as actually fielded, so a
    later data check compares against the truth rather than a re-read.

    `missing` names what the data map cannot supply: it records what was
    COLLECTED, never who was asked, so routing is absent by construction and
    must not be inferred.
    """
    options: list[Option] = []
    if candidate.is_multi:
        for index, variable in enumerate(candidate.variables, start=1):
            code = index
            # Decipher names a punch `<parent>r<code>`; the suffix IS the code.
            suffix = variable.name[len(candidate.qid) :].lstrip("rR")
            if suffix.isdigit():
                code = int(suffix)
            options.append(Option(code=code, label=variable.label or f"Option {code}"))
        qtype = QuestionType.MULTI
    else:
        variable = candidate.variables[0]
        for code, label in sorted((variable.values or {}).items()):
            options.append(Option(code=code, label=label))
        qtype = QuestionType.SINGLE if options else QuestionType.OPEN_TEXT

    question = Question(
        qid=candidate.qid,
        text=candidate.text,
        type=qtype,
        options=options,
        required=False,
        source_text=(
            "Rebuilt from the data map: "
            + ", ".join(v.name for v in candidate.variables[:12])
        ),
        source_location="data map",
        extraction_method="datamap",
        extraction_evidence={
            "text": "the data map's question text",
            "options": f"{len(options)} punch labels from the data map",
        },
        validation={
            "recovered_from_datamap": True,
            # Said plainly so nobody mistakes silence for "no routing".
            "missing": ["routing"],
        },
    )
    question.flags.append(FLAG_RECOVERED)
    return question


def fill_from_datamap(fields, candidate: DatamapQuestion) -> list[str]:
    """Answer a parser's open questions from the script's own record.

    `fields` is a SpecTableFields or ProseFields whose `missing` list says what
    the document did not state plainly. The data map states all three of text,
    type and options exactly as fielded, so anything still missing that it can
    answer is filled here — BEFORE a model is asked, which is the whole point:
    on a real study this removed 50 residue calls that would each have been
    asked for something already written down.

    Routing is never filled. A data map records what was COLLECTED, never who
    was ASKED, so `base`/`terminate` stay missing and still escalate.

    Returns the field names it filled, for provenance. Mutates `fields`.
    """
    filled: list[str] = []

    if "text" in fields.missing and candidate.text:
        fields.text = candidate.text
        filled.append("text")

    if "options" in fields.missing or (not fields.options and candidate.variables):
        rebuilt = rebuild_question(candidate).options
        if rebuilt:
            fields.options = rebuilt
            if "options" in fields.missing:
                filled.append("options")

    if "type" in fields.missing:
        fields.qtype = QuestionType.MULTI if candidate.is_multi else (
            QuestionType.SINGLE if fields.options else QuestionType.OPEN_TEXT
        )
        filled.append("type")

    for name in filled:
        if name in fields.missing:
            fields.missing.remove(name)
    return filled


def non_answerable_qids(datamap: DataMap) -> dict[str, str]:
    """Normalized qid -> role, for every column that was never asked.

    A questionnaire document often describes its hidden variables inline
    ("HID_COMP_SIZE: Hidden variable for company size classification"), and the
    reader dutifully treats each as a question — then pays a model call trying
    to work out its answer type. The data map already knows they are computed,
    not asked. Consulting it removes the call AND gives the right answer, which
    is the better argument of the two.
    """
    roles: dict[str, str] = {}
    for variable in datamap.variables:
        role = classify_variable(
            variable.name,
            getattr(variable, "label", "") or "",
            getattr(variable, "question_ref", None),
        )
        if role in _ANSWERABLE:
            continue
        key = normalize_qid(getattr(variable, "question_ref", None) or variable.name)
        roles.setdefault(key, role)
    return roles


def index_by_qid(datamap: DataMap) -> dict[str, DatamapQuestion]:
    """Data-map questions keyed by normalized qid, for fast lookup per chunk."""
    return {normalize_qid(q.qid): q for q in questions_from_datamap(datamap)}


def recover_missing(
    survey: SurveyDefinition, datamap: DataMap, linked: set[str] | None = None
) -> tuple[list[Question], list[QuestionVariableLink]]:
    """Every question the data map describes and the questionnaire lacks.

    Returns the rebuilt questions AND their variable links. The link is not
    re-derived by name matching: a recovered question was built FROM named
    columns, so its link is already known exactly. Sending it back through the
    fuzzy matcher would be both wasteful and lossy — a multi-select rebuilt
    from `DA_GOALSr1..r9` would have to re-guess the very columns it came from.
    """
    questions: list[Question] = []
    links: list[QuestionVariableLink] = []
    split_grids = {
        (q.extraction_evidence or {}).get("split_from") for q in survey.questions
    } - {None}
    grid_cell = re.compile(
        rf"^({'|'.join(map(re.escape, split_grids))})r\d+c\d+$", re.I
    ) if split_grids else None
    for candidate in reconcile(survey, datamap).missing_from_questionnaire:
        names = {v.name for v in candidate.variables}
        if linked and names <= linked:
            # Its columns already belong to an extracted question (Q2r5c1 is
            # Q2a's): rebuilding it would check the same data twice, wrongly.
            continue
        if grid_cell and all(grid_cell.match(n) for n in names):
            # The grid Q2a/Q2b were split from, kept by the script beside
            # their own columns: a copy of questions we already have.
            continue
        questions.append(rebuild_question(candidate))
        links.append(
            QuestionVariableLink(
                qid=candidate.qid,
                variable_names=[v.name for v in candidate.variables],
                match_method="exact",
                confidence=1.0,
                notes="rebuilt from the data map",
            )
        )
    return questions, links

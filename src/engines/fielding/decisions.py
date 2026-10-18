"""The rules the field engine applies to one respondent, as pure functions.

These lived inline in the respondent HTTP route, which meant the only way to
find out what a survey would do to people was to point real people at it. They
are extracted here so the dry run exercises the SAME code the live link runs —
not a second implementation of the same idea, which would be free to drift and
would make a clean dry run meaningless.

Nothing here touches the database or the network: a question, a set of answers,
a decision.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.core.survey_definition import Expression, Question, SurveyDefinition, evaluate

# Lucid/Cint dispositions. A vendor reads these off the redirect, so they are
# part of our contract with the panel, not an internal enum.
STATUS_COMPLETE = 10
STATUS_TERMINATE = 20
STATUS_SECURITY = 30
STATUS_OVERQUOTA = 40


def is_decidable(expression: Expression | None, answers: dict) -> bool:
    """True when every question the condition names has been answered.

    A condition must not be judged before the answers it depends on exist. A
    negated screen-out ("TERMINATE if 1 not selected", stored as `not(in ...)`)
    is the case that proves it: against an empty answer set the inner `in` is
    false, `not false` is true, and every respondent is screened out on their
    first submit. This guard is why that stopped happening, and both the live
    route and the dry run have to apply it identically or they disagree about
    who gets terminated.
    """
    if expression is None:
        return False
    return expression.referenced_qids().issubset(answers.keys())


def terminating_question(survey: SurveyDefinition, answers: dict) -> Question | None:
    """The first question whose screen-out fires on these answers, if any.

    Approved hidden-variable rules are computed first: "TERMINATE IF D2a = 4"
    tests a value no respondent ever typed.
    """
    from src.engines.questionnaire.derived import with_derived

    answers = with_derived(survey, answers)
    for question in survey.questions:
        if question.terminate_if is None:
            continue
        if not is_decidable(question.terminate_if, answers):
            continue
        if evaluate(question.terminate_if, answers):
            return question
    return None


def is_visible(question: Question, answers: dict) -> bool:
    """Whether this question is shown, given what has been answered so far.

    A base whose referenced questions are unanswered has not been satisfied —
    the respondent has not reached the point where it could be. Only a question
    with no base at all is unconditionally shown.
    """
    if question.show_if is None:
        return True
    if not is_decidable(question.show_if, answers):
        return False
    return evaluate(question.show_if, answers)


@dataclass(frozen=True)
class QuotaCell:
    """A quota cell, independent of how it is stored."""

    id: str
    condition: Expression
    limit: int
    count: int


def matching_cells(
    cells: list[QuotaCell], answers: dict, decidable_only: bool = False
) -> list[QuotaCell]:
    """Cells whose condition matches `answers`.

    `decidable_only` keeps only cells whose match can no longer change with a
    future answer — the correctness gate for screening a doomed respondent out
    early instead of making them answer the rest of the survey.
    """
    return [
        cell
        for cell in cells
        if not (decidable_only and not is_decidable(cell.condition, answers))
        and evaluate(cell.condition, answers)
    ]


def unsatisfiable_conditions(survey: SurveyDefinition) -> list[dict[str, str]]:
    """Conditions that cannot fire for anyone, provable from the survey alone.

    Not "did not fire in a simulation" — that is evidence. This is arithmetic:
    a screen-out that tests a question the survey does not contain, or tests a
    code that question never offers, will not fire for any respondent who ever
    lives. A screener that cannot screen looks perfect right up until the data
    comes back with the wrong people in it.
    """
    by_qid = {q.qid: q for q in survey.questions}
    findings: list[dict[str, str]] = []

    for question in survey.questions:
        for kind, expression in (
            ("screen-out", question.terminate_if),
            ("base", question.show_if),
        ):
            if expression is None:
                continue
            for ref in sorted(expression.referenced_qids()):
                target = by_qid.get(ref)
                if target is None:
                    findings.append(
                        {
                            "qid": question.qid,
                            "kind": "unknown_question",
                            "detail": (
                                f"{question.qid}'s {kind} tests {ref}, which is not a "
                                "question in this survey, so it can never be true."
                            ),
                        }
                    )
                    continue
                offered = {option.code for option in target.options}
                tested = _codes_tested(expression, ref)
                if offered and tested and not (tested & offered):
                    findings.append(
                        {
                            "qid": question.qid,
                            "kind": "impossible_codes",
                            "detail": (
                                f"{question.qid}'s {kind} tests {ref} for "
                                f"{sorted(tested)}, which {ref} never offers "
                                f"({sorted(offered)}). It can never be true."
                            ),
                        }
                    )
    return findings


def _codes_tested(expression: Expression, qid: str) -> set[int]:
    """Every code the expression requires `qid` to hold.

    `in`, `selected` and `eq` all name a code the respondent must have given.
    `ne` does not — "not 9" is satisfied by everyone, so an out-of-range code
    there is harmless rather than impossible. `gt`/`lt` are numeric, and a
    question with no options cannot contradict them.
    """
    found: set[int] = set()
    if expression.qid == qid:
        if expression.op == "in":
            found |= {v for v in (expression.values or []) if isinstance(v, int)}
        elif expression.op in ("selected", "eq") and isinstance(expression.value, int):
            found.add(expression.value)
    for child in expression.children or []:
        found |= _codes_tested(child, qid)
    return found

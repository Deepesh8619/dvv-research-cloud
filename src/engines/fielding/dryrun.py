"""Field a survey against synthetic respondents, before real ones arrive.

The point is not to produce data — it is to find out what the script DOES to
people. A screen-out that fires for nobody, a base that hides a question from
everyone, a mask that never narrows anything, a quota that fills at respondent
40 of 500: each of those is invisible in the questionnaire and obvious in the
run, and each costs real money once a panel is pointed at the link.

Every decision here comes from `decisions.py`, which is the same module the
live respondent route uses. A dry run that reimplemented the rules would be
free to disagree with the engine, and a clean report would mean nothing.

Deterministic: the same survey and seed give the same run, so a result can be
argued with.
"""

from __future__ import annotations

import random
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from src.core.survey_definition import Option, Question, QuestionType, SurveyDefinition
from src.engines.fielding.decisions import (
    STATUS_COMPLETE,
    STATUS_OVERQUOTA,
    STATUS_TERMINATE,
    QuotaCell,
    is_decidable,
    is_visible,
    matching_cells,
    terminating_question,
    unsatisfiable_conditions,
)

DEFAULT_RESPONDENTS = 500
MAX_RESPONDENTS = 5000


@dataclass
class ConditionResult:
    """What one condition did across the whole run."""

    qid: str
    kind: str  # screen_out | base | mask | quota
    fired: int = 0
    eligible: int = 0
    #: Quotas only: the respondent number at which the cell reached its limit.
    #: The single most useful number in a field report — it says how long the
    #: sample will last before the cell starts turning people away.
    filled_at: int | None = None
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "qid": self.qid,
            "kind": self.kind,
            "fired": self.fired,
            "eligible": self.eligible,
            "filled_at": self.filled_at,
            "detail": self.detail,
        }


@dataclass
class DryRunReport:
    n_respondents: int = 0
    seed: int = 0
    #: Share of synthetic respondents who answer each screener so as to pass it.
    qualify_rate: float = 0.0
    dispositions: dict[str, int] = field(default_factory=dict)
    conditions: list[ConditionResult] = field(default_factory=list)
    never_fired: list[str] = field(default_factory=list)
    impossible: list[dict[str, str]] = field(default_factory=list)
    questions_never_shown: list[str] = field(default_factory=list)
    # (answers, disposition) per respondent, only when `collect` is set. The dry
    # run walks the survey's real conditions — bases, masks, screen-outs,
    # quotas — so this is the one place a test dataset can come from without a
    # second implementation of the same rules drifting away from it.
    respondents: list[tuple[dict[str, Any], int]] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        """Nothing structurally impossible, and every condition did something."""
        return not self.impossible and not self.never_fired

    def to_dict(self) -> dict[str, Any]:
        return {
            "n_respondents": self.n_respondents,
            "seed": self.seed,
            "qualify_rate": self.qualify_rate,
            "passed": self.passed,
            "dispositions": self.dispositions,
            "conditions": [c.to_dict() for c in self.conditions],
            "never_fired": self.never_fired,
            "impossible": self.impossible,
            "questions_never_shown": self.questions_never_shown,
        }


def _mask_allowed_codes(
    question: Question, answers: dict, by_qid: dict[str, Question]
) -> set[int] | None:
    """Codes a masked question may offer this respondent, or None if unmasked.

    The simulator has to respect the mask for the same reason the link does: a
    synthetic respondent picking an option the mask hides would produce exactly
    the violation the cross-check is built to catch, and the run would report
    our own generator as a defect.
    """
    techniques = (question.validation or {}).get("techniques") or {}
    for mask in techniques.get("masks") or []:
        mode = mask.get("mode")
        if mode == "full_list":
            continue
        against = mask.get("exclude_qid") if mode == "list_except" else mask.get("source_qid")
        source = by_qid.get(against or "")
        if source is None:
            continue
        chosen = answers.get(against)
        if chosen is None:
            continue
        picked = set(chosen) if isinstance(chosen, list) else {chosen}
        offered = {o.code for o in question.options}
        return picked & offered if mode == "selected" else offered - picked
    return None


def _answer(
    question: Question, rng: random.Random, allowed: set[int] | None
) -> Any:
    """A plausible answer, sampled from what the question itself offers."""
    codes = [o.code for o in question.options]
    if allowed is not None:
        codes = [c for c in codes if c in allowed]

    if question.type in (QuestionType.SINGLE, QuestionType.DROPDOWN):
        return rng.choice(codes) if codes else None
    if question.type == QuestionType.MULTI:
        if not codes:
            return None
        return rng.sample(codes, rng.randint(1, len(codes)))
    if question.type == QuestionType.RANK:
        return rng.sample(codes, len(codes)) if codes else None
    if question.type == QuestionType.GRID_SINGLE:
        rows: list[Option] = question.rows or []
        cols: list[Option] = question.cols or []
        if not rows or not cols:
            return None
        return {str(r.code): rng.choice([c.code for c in cols]) for r in rows}
    if question.type == QuestionType.NUMERIC and question.rows:
        # Shares that must reach the stated total: split it at random.
        total = int((question.validation or {}).get("sum") or 100)
        cuts = sorted(rng.randint(0, total) for _ in range(len(question.rows) - 1))
        parts = [b - a for a, b in zip([0, *cuts], [*cuts, total], strict=True)]
        return {str(row.code): part for row, part in zip(question.rows, parts, strict=True)}
    if question.type == QuestionType.NUMERIC:
        validation = question.validation or {}
        low = int(validation.get("min", 1) or 1)
        high = int(validation.get("max", 100) or 100)
        return rng.randint(low, high) if low <= high else low
    if question.type == QuestionType.OPEN_TEXT:
        return rng.choice(
            ["it was fine", "nothing to add", "too expensive", "good value", "not sure"]
        )
    return None


#: Share of synthetic respondents who are on-target and pass every screener.
#: Each of the rest fails one screen-out, chosen at random, on purpose.
#: Answering every screener at random is not a panel anyone fields to: ten
#: screeners each ruling out part of the range let ~1% through, so the run was
#: 99% screen-outs and every question after the screener was reached by a
#: handful — the bases and quotas it exists to test went untested. A real panel
#: is targeted. The rest still answer at random, so every screen-out fires.
QUALIFY_RATE = 0.85
#: Draws tried before accepting that no answer passes (an impossible screener).
_QUALIFY_TRIES = 12


def _qualifying(
    survey: SurveyDefinition,
    question: Question,
    rng: random.Random,
    allowed: set[int] | None,
    answers: dict[str, Any],
    fallback: Any,
) -> Any:
    """An answer to this question that does not screen the respondent out.

    Drawn and tested through `terminating_question` — the live engine's own
    rule — so a screen-out written against another question's answer counts too.
    """
    for _ in range(_QUALIFY_TRIES):
        candidate = _answer(question, rng, allowed)
        if candidate is None:
            return fallback
        if terminating_question(survey, {**answers, question.qid: candidate}) is None:
            return candidate
    return fallback


def _tripping(
    survey: SurveyDefinition,
    question: Question,
    rng: random.Random,
    allowed: set[int] | None,
    answers: dict[str, Any],
    aim: str,
) -> Any:
    """An answer here that fires the screen-out on `aim`, or None if none does.

    Tested through the live rule, so a screen-out stored on one question but
    written against another's answer is tripped where the answer is given.
    """
    for _ in range(_QUALIFY_TRIES):
        candidate = _answer(question, rng, allowed)
        if candidate is None:
            return None
        hit = terminating_question(survey, {**answers, question.qid: candidate})
        if hit is not None and hit.qid == aim:
            return candidate
    return None


def simulate(
    survey: SurveyDefinition,
    quota_cells: list[QuotaCell] | None = None,
    n: int = DEFAULT_RESPONDENTS,
    seed: int = 20260903,
    collect: bool = False,
    qualify_rate: float = QUALIFY_RATE,
) -> DryRunReport:
    """Run `n` synthetic respondents through the survey's own conditions.

    `collect` keeps each respondent's answers and disposition on the report, so
    a test dataset can be built from the same walk that produces the dry run.
    Generating it anywhere else would mean a second implementation of bases,
    masks, screen-outs and quotas — and the moment those two disagree, QA is
    testing something the respondent will never see.
    """
    n = max(1, min(int(n), MAX_RESPONDENTS))
    rng = random.Random(seed)
    by_qid = {q.qid: q for q in survey.questions}
    cells = list(quota_cells or [])
    counts = {cell.id: cell.count for cell in cells}

    report = DryRunReport(n_respondents=n, seed=seed, qualify_rate=qualify_rate)
    report.impossible = unsatisfiable_conditions(survey)

    screen_outs = {q.qid: ConditionResult(q.qid, "screen_out") for q in survey.questions
                   if q.terminate_if is not None}
    bases = {q.qid: ConditionResult(q.qid, "base") for q in survey.questions
             if q.show_if is not None}
    masks = {
        q.qid: ConditionResult(q.qid, "mask")
        for q in survey.questions
        if ((q.validation or {}).get("techniques") or {}).get("masks")
    }
    quotas = {
        cell.id: ConditionResult(cell.id, "quota", detail=f"limit {cell.limit}")
        for cell in cells
    }
    shown_at_least_once: set[str] = set()
    dispositions: Counter = Counter()

    for respondent in range(1, n + 1):
        answers: dict[str, Any] = {}
        status = STATUS_COMPLETE
        # Most of a panel is on-target and passes every screener. Each of the
        # rest is aimed at ONE screen-out and fails exactly that one: answering
        # the rest at random, they all fell at the first screener and the tenth
        # was never tested.
        aim = (
            None if not screen_outs or rng.random() < qualify_rate
            else rng.choice(sorted(screen_outs))
        )

        for qid in survey.question_order():
            question = by_qid.get(qid)
            if question is None:
                continue
            if question.type == QuestionType.INFO:
                # A hidden variable with codes — "CODE SAMPLE SOURCE", a sample
                # split — is set by the script, not asked. Left empty, every
                # path gated on it (S0=1: the whole consumer screener) was
                # unreachable, and the run reported a third of the survey as
                # never shown. Each synthetic respondent is given one of its
                # codes, as the panel or the script would.
                # ponytail: uniform over codes; a computed variable (age bands)
                # can disagree with the answers it is computed from.
                if question.options and qid not in answers:
                    answers[qid] = rng.choice([o.code for o in question.options])
                continue

            if qid in bases:
                bases[qid].eligible += 1
            if not is_visible(question, answers):
                continue
            if qid in bases:
                bases[qid].fired += 1
            shown_at_least_once.add(qid)

            allowed = _mask_allowed_codes(question, answers, by_qid)
            if qid in masks:
                masks[qid].eligible += 1
                if allowed is not None and len(allowed) < len(question.options):
                    masks[qid].fired += 1

            value = _answer(question, rng, allowed)
            tripped = _tripping(survey, question, rng, allowed, answers, aim) if aim else None
            if tripped is not None:
                value = tripped
            elif value is not None and terminating_question(
                survey, {**answers, qid: value}
            ) is not None:
                value = _qualifying(survey, question, rng, allowed, answers, value)
            if value is not None:
                answers[qid] = value

            terminated = terminating_question(survey, answers)
            if terminated is not None:
                screen_outs[terminated.qid].fired += 1
                status = STATUS_TERMINATE
                break

            full = [
                cell
                for cell in matching_cells(cells, answers, decidable_only=True)
                if counts[cell.id] >= cell.limit
            ]
            if full:
                quotas[full[0].id].fired += 1
                status = STATUS_OVERQUOTA
                break

        # A screen-out was only ever in a position to fire for a respondent who
        # answered everything it tests. Counting it against all n would report a
        # working screener as one that mostly misses.
        for qid, result in screen_outs.items():
            if is_decidable(by_qid[qid].terminate_if, answers):
                result.eligible += 1

        for cell in matching_cells(cells, answers):
            quotas[cell.id].eligible += 1

        if status == STATUS_COMPLETE:
            for cell in matching_cells(cells, answers):
                if counts[cell.id] >= cell.limit:
                    status = STATUS_OVERQUOTA
                    quotas[cell.id].fired += 1
                    break
                counts[cell.id] += 1
                if counts[cell.id] >= cell.limit and quotas[cell.id].filled_at is None:
                    quotas[cell.id].filled_at = respondent
        dispositions[status] += 1
        if collect:
            report.respondents.append((dict(answers), status))
    report.conditions = [
        *sorted(screen_outs.values(), key=lambda c: c.qid),
        *sorted(bases.values(), key=lambda c: c.qid),
        *sorted(masks.values(), key=lambda c: c.qid),
        *sorted(quotas.values(), key=lambda c: c.qid),
    ]
    # A condition nothing exercised is the finding this whole run exists for.
    # It is evidence, not proof — 500 synthetic respondents may simply have
    # missed a narrow path — which is why it is reported separately from the
    # structurally impossible ones above.
    report.never_fired = [c.qid for c in report.conditions if c.fired == 0]
    report.questions_never_shown = [
        qid
        for qid in survey.question_order()
        if qid in by_qid
        and by_qid[qid].type != QuestionType.INFO
        and qid not in shown_at_least_once
    ]
    report.dispositions = {
        "complete": dispositions[STATUS_COMPLETE],
        "terminate": dispositions[STATUS_TERMINATE],
        "overquota": dispositions[STATUS_OVERQUOTA],
    }
    return report


def path_to(
    survey: SurveyDefinition,
    target: str,
    given: dict[str, Any] | None = None,
    seed: int = 0,
    tries: int = 300,
) -> tuple[dict[str, Any], bool] | None:
    """Answers that take one respondent to `target` the way a qualifier would.

    For testing a live link: jump straight to Q30 and see it exactly as a
    respondent who got there would — the screeners passed, the paths that lead
    to it taken, the lists narrowed by what they said. Every skipped question is
    answered through the same rules the dry run uses; a question the path does
    not show gets no answer at all, as it would in the field.

    The tester's own answers (`given`) are kept where they allow the target. If
    they rule it out — a screener answered to fail, a base not met — a fresh
    path is taken instead, and the second value says so.

    Returns (answers, kept_given), or None when no attempt reaches the target:
    a question nobody can be shown, which the pre-link check reports too.
    """
    by_qid = {q.qid: q for q in survey.questions}
    order = survey.question_order()
    if target not in by_qid or target not in order:
        return None
    before = order[: order.index(target)]
    seeded = {k: v for k, v in (given or {}).items() if k in before}

    for attempt in range(tries):
        # Half the attempts try to keep what the tester answered.
        keep = bool(seeded) and attempt < tries // 2
        rng = random.Random(seed + attempt)
        answers: dict[str, Any] = dict(seeded) if keep else {}
        failed = False
        for qid in before:
            question = by_qid.get(qid)
            if question is None:
                continue
            if question.type == QuestionType.INFO:
                if question.options and qid not in answers:
                    answers[qid] = rng.choice([o.code for o in question.options])
                continue
            if not is_visible(question, answers):
                answers.pop(qid, None)  # not shown on this path: no answer
                continue
            if qid not in answers:
                allowed = _mask_allowed_codes(question, answers, by_qid)
                value = _answer(question, rng, allowed)
                if value is not None and terminating_question(
                    survey, {**answers, qid: value}
                ) is not None:
                    value = _qualifying(survey, question, rng, allowed, answers, value)
                if value is not None:
                    answers[qid] = value
            if terminating_question(survey, answers) is not None:
                failed = True
                break
        if not failed and is_visible(by_qid[target], answers):
            return answers, keep
    return None

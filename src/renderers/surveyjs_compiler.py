"""SurveyJS compiler.

Compiles a `SurveyDefinition` (our authored instrument model) into a SurveyJS
survey JSON model — the schema consumed by the SurveyJS runtime/creator
(https://surveyjs.io). Blocks become pages, questions become typed elements,
and routing `Expression`s become SurveyJS `visibleIf` condition strings.

This is a pure transformation: no I/O, no side effects, deterministic dict out.
Termination logic (`terminate_if`) and quotas are intentionally *not* emitted:
SurveyJS has no native mid-survey terminate, and terminates/quotas are meant to
be server-authoritative (a future respondent-runtime task enforces them) since
quota cells cost money if gamed client-side. `terminate_if` is therefore
dropped here by design, not by omission.
"""

from __future__ import annotations

import random
import re
from typing import Any

from src.core.survey_definition import (
    GRID_TYPES,
    Block,
    Expression,
    Question,
    QuestionType,
    SurveyDefinition,
)
from src.engines.questionnaire.bipolar import is_bipolar, split
from src.engines.questionnaire.grid_masks import affirmative_codes, mask_code_map, variable_name
from src.engines.questionnaire.grid_subquestions import without_question_label
from src.engines.questionnaire.prose_directives import is_derived_wording, is_terminal_option
from src.engines.questionnaire.symbols import symbols_for_survey
from src.engines.questionnaire.write_ins import write_in_name, write_in_prompt, write_ins

# Square brackets are a SCRIPTING convention, not respondent language. Across
# every instrument we have — client documents and public federal ones alike —
# wording aimed at the respondent uses parentheses ("Other (please specify)")
# and wording aimed at the scripter uses brackets ("[SP]", "[IF S2 = 2]",
# "[Num box 1900-2014]", "[SHOW ONLY ITEMS SELECTED AT Q1]"). So nothing
# bracketed is ever shown to a person: the compiler removes it on the way out,
# while the stored question keeps its original wording for review.
_BRACKETED = re.compile(r"\[[^\]]*\]")
# A pipe marker may be bracketed, doubled-angle, angled or guillemeted.
_PIPE_GROUP = re.compile(r"\[[^\]]*\]|<<[^<>]*>>|<[A-Za-z][^<>]{0,40}>|«[^»]*»")
_WHITESPACE = re.compile(r"\s+")
# " ?" and " ," left behind once a directive is lifted out of a sentence.
_STRANDED_PUNCTUATION = re.compile(r"\s+([?.,;:!])")

# Question types whose answer is a LIST, so a mask tests membership rather than
# equality.
_MULTI_VALUED = frozenset({QuestionType.MULTI, QuestionType.RANK})


def _normalize(text: str) -> str:
    # Brackets are the directive's frame, not its words: a pipe stored as
    # "INSERT RATING]" still names "[INSERT RATING]".
    return _WHITESPACE.sub(" ", (text or "").strip("[] ")).strip().casefold()


_WRITE_IN = re.compile(r"\(\s*specify\s*\)|\bnot listed\b", re.I)


def _find(by_qid: dict[str, Question], qid: str | None) -> Question | None:
    """A question by id, whatever case the document wrote it in.

    The technique parser upper-cases what it reads ("Q2C"), and the question is
    "Q2c": the pipe resolved to nothing, and a respondent read "the last time you
    purchased Bubble was ." with the answer missing from the sentence.
    """
    if not qid:
        return None
    if qid in by_qid:
        return by_qid[qid]
    wanted = qid.upper()
    return next((q for k, q in by_qid.items() if k.upper() == wanted), None)


# "[INSERT Q7 #1 RANK]", "...their TOP choice", "the LEAST believable one".
_PIPE_POSITION = re.compile(
    r"#\s*(?P<n>\d+)|\b(?P<nth>\d+)(?:st|nd|rd|th)\b|\b(?P<first>top|first|most)\b"
    r"|\b(?P<last>last|least|bottom)\b",
    re.I,
)


def _pipe_position(body: str) -> int | None:
    """Which place in a ranking a pipe wants: 1 for the top, -1 for the last."""
    found = _PIPE_POSITION.search(body or "")
    if not found:
        return None
    if found.group("last"):
        return -1
    if found.group("first"):
        return 1
    return int(found.group("n") or found.group("nth"))


def _rank_variable(qid: str, position: int) -> str:
    return f"{qid}_rank{'last' if position < 0 else position}"


def _pipe_marker(source: Question, body: str) -> str:
    """{Q7} for a question; {Q2c.1} for ONE row of a grid named in the pipe.

    "[INSERT Q2c RESPONSE FOR BUBBLE]" wants the answer on Bubble's row — the
    whole grid's answer is a map no sentence can hold. SurveyJS prints a cell's
    column LABEL for {Q2c.1}, which is what the sentence needs.
    """
    if source.type is QuestionType.RANK:
        # A ranking's answer is an ordered list. SurveyJS prints the WHOLE list
        # for {Q7} and has no way to say "the one they put first" — a question
        # about their top choice named every brand they ranked. The link
        # computes the position as they answer; see `pipe_variables`.
        position = _pipe_position(body)
        if position is not None:
            return "{" + _rank_variable(source.qid, position) + "}"
    # One row of a grid, or one box of a constant sum ("...FOR ECT"): a single
    # value the sentence can hold. The whole question cannot — see qa_loop.
    if source.rows and (
        source.type is QuestionType.GRID_SINGLE or source.type is QuestionType.NUMERIC
    ):
        lowered = body.casefold()
        for row in sorted(source.rows, key=lambda r: -len(r.label or "")):
            label = (row.label or "").casefold()
            if label and re.search(rf"\b(?:for|of|on)\s+{re.escape(label)}\b", lowered):
                return "{" + f"{source.qid}.{row.code}" + "}"
    return "{" + source.qid + "}"


def _resolve_pipes(text: str, pipes: list[dict], by_qid: dict[str, Question]) -> str:
    """Turn "[INSERT RESPONSE FROM Q2]" into the marker SurveyJS interpolates.

    The compiler used to assume piping needed no work because SurveyJS already
    substitutes `{qid}` markers "present in question text". They are never
    present: a questionnaire writes the instruction in prose, and what reached
    the respondent was the instruction itself.
    """
    for pipe in pipes:
        question = _find(by_qid, pipe.get("source_qid"))
        if question is None:
            continue  # unresolvable — the strip below removes it
        source = question.qid
        wanted = _normalize(pipe.get("verbatim") or "")
        for group in _PIPE_GROUP.findall(text):
            body = _normalize(group[1:-1])
            if body == wanted or (wanted and wanted in body) or source.casefold() in body:
                # EVERY occurrence: a question that names the brand twice
                # ("You mentioned X ... what draws you to X?") had the second
                # one stripped, leaving the sentence with a hole in it.
                text = text.replace(group, _pipe_marker(question, group[1:-1]))
    return text


def _for_respondent(text: str, pipes: list[dict], by_qid: dict[str, Question]) -> str:
    """`text` as a person should read it: pipes resolved, directives removed."""
    piped = _resolve_pipes(text or "", pipes, by_qid)
    stripped = _BRACKETED.sub(" ", piped)
    return _STRANDED_PUNCTUATION.sub(r"\1", _WHITESPACE.sub(" ", stripped)).strip()


def _grid_mask_sources(survey: SurveyDefinition) -> list[dict[str, Any]]:
    """Every grid a mask reads, with the columns that mean "this row counts".

    The browser cannot work this out from the model: SurveyJS has no way to
    say "the rows of Q5 whose cell says Yes" in a condition. So the server
    says which columns are affirmative and the page turns the grid's answer
    into a plain list of row codes, which `contains` can then test. One
    reading, decided in one place — see grid_masks.
    """
    by_qid = {q.qid: q for q in survey.questions}
    wanted: dict[str, dict[str, Any]] = {}
    for question in survey.questions:
        for mask in _techniques(question).get("masks") or []:
            if mask.get("mode") == "full_list":
                continue
            against = (
                mask.get("exclude_qid") if mask.get("mode") == "list_except"
                else mask.get("source_qid")
            )
            source = _find(by_qid, against)
            if source is None or source.type is not QuestionType.GRID_SINGLE:
                continue
            wanted[source.qid] = {
                "name": variable_name(source.qid),
                "qid": source.qid,
                "positive": affirmative_codes(source),
            }
    return sorted(wanted.values(), key=lambda v: v["name"])


def _choices_visible_if(mask: dict, by_qid: dict[str, Question]) -> str | None:
    """A per-choice condition that narrows a masked list to what was answered.

    SurveyJS evaluates `choicesVisibleIf` once per option with `{item}` bound to
    that option's value, which is exactly the shape a mask needs. Returns None
    when the mask cannot be expressed — a full list has nothing to narrow, and a
    source we cannot see is not something to guess at.
    """
    mode = mask.get("mode")
    if mode == "full_list":
        return None
    against = mask.get("exclude_qid") if mode == "list_except" else mask.get("source_qid")
    source = _find(by_qid, against)
    if source is None:
        return None
    against = source.qid  # the parser upper-cases what it reads ("Q2B" is "Q2b")
    keeps_what_was_chosen = mode == "selected"
    if source.type is QuestionType.GRID_SINGLE:
        # A grid's answer is {row: column}: `{Q5} = {item}` compared an object
        # to a code, was never true, and left the masked question with NO
        # options at all. The page publishes the picked rows as a variable.
        operator = "contains" if keeps_what_was_chosen else "notcontains"
        return f"{{{variable_name(against)}}} {operator} {{item}}"
    if source.type in _MULTI_VALUED:
        operator = "contains" if keeps_what_was_chosen else "notcontains"
        return f"{{{against}}} {operator} {{item}}"
    operator = "=" if keeps_what_was_chosen else "<>"
    return f"{{{against}}} {operator} {{item}}"


def _techniques(question: Question) -> dict:
    return (question.validation or {}).get("techniques") or {}


# SurveyJS operator strings keyed by our leaf comparison ops.
_LEAF_OPERATORS = {
    "eq": "=",
    "ne": "<>",
    "gt": ">",
    "lt": "<",
}


def _format_value(value: Any) -> str:
    """Render a scalar into SurveyJS condition-string syntax.

    Numbers are bare; everything else is single-quoted (SurveyJS accepts single
    quotes for string literals in its expression grammar).
    """
    if isinstance(value, bool):
        # bool is an int subclass — guard before the numeric branch.
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return "'{}'".format(str(value).replace("'", "\\'"))


def _expression_to_visibleif_string(expr: Expression) -> str:
    """Translate a routing `Expression` into a SurveyJS `visibleIf` string.

    Uses SurveyJS's condition grammar: `{qid}` variable references, `= <> > <`
    comparisons, `contains` for membership, `and`/`or`/`not`, `anyof` for
    multi-value `in`. Logical sub-expressions are parenthesised so precedence is
    unambiguous.
    """
    op = expr.op

    if op == "and":
        parts = [_expression_to_visibleif_string(c) for c in expr.children or []]
        return " and ".join(f"({p})" for p in parts)
    if op == "or":
        parts = [_expression_to_visibleif_string(c) for c in expr.children or []]
        return " or ".join(f"({p})" for p in parts)
    if op == "not":
        parts = [_expression_to_visibleif_string(c) for c in expr.children or []]
        inner = " and ".join(f"({p})" for p in parts)
        return f"not ({inner})"

    # A condition on ONE row of a grid names that row: SurveyJS addresses a
    # matrix cell as {question.row}, and without it the test runs against the
    # whole {row: column} answer and matches nobody.
    ref = f"{{{expr.qid}.{expr.row}}}" if expr.row is not None else f"{{{expr.qid}}}"

    if op == "selected":
        # "was option X chosen" — works for both single (=) and multi (contains);
        # `contains` in SurveyJS is true when the answer array holds the value.
        return f"{ref} contains {_format_value(expr.value)}"
    if op == "in":
        values = expr.values or []
        formatted = ", ".join(_format_value(v) for v in values)
        # `anyof` tests intersection of the answer with a value set.
        return f"{ref} anyof [{formatted}]"

    operator = _LEAF_OPERATORS.get(op)
    if operator is None:
        raise ValueError(f"cannot translate expression op to visibleIf: {op!r}")
    return f"{ref} {operator} {_format_value(expr.value)}"


def _choices(options: list, by_qid: dict[str, Question]) -> list[dict[str, Any]]:
    # An option's label carries scripter tags too — "Other (Please specify):
    # [ TXT ] ____" — so it goes through the same public-facing filter.
    compiled: list[dict[str, Any]] = []
    for o in options:
        item: dict[str, Any] = {
            "value": o.code,
            "text": _for_respondent(o.label, [], by_qid),
        }
        if o.show_if is not None:
            # Per-item visibility: SurveyJS honours `visibleIf` on a choice and
            # on a matrix row, so a condition written against one answer is
            # enforced where the respondent actually is, not left as a note.
            item["visibleIf"] = _expression_to_visibleif_string(o.show_if)
        compiled.append(item)
    return compiled


def _numeric_validators(validation: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Build SurveyJS `numericvalidator`(s) from a numeric question's validation."""
    if not validation:
        return []
    minimum = validation.get("min")
    maximum = validation.get("max")
    if minimum is None and maximum is None:
        return []
    validator: dict[str, Any] = {"type": "numeric"}
    if minimum is not None:
        validator["minValue"] = minimum
    if maximum is not None:
        validator["maxValue"] = maximum
    return [validator]


def _is_numeric_scale(options: list) -> bool:
    """A 0-10 style scale: consecutive codes, labelled at most at a few points."""
    if len(options) < 5:
        return False
    codes = [o.code for o in options]
    if codes != list(range(codes[0], codes[0] + len(codes))):
        return False
    bare = sum(1 for o in options if (o.label or "").strip() == str(o.code))
    return bare * 2 >= len(options)


def _scale_end(option) -> str:
    """ "Very Unfavorable" from "0=Very Unfavorable"; nothing when it is just a number."""
    label = (option.label or "").strip()
    return "" if label == str(option.code) else label


def _numeric_validators_with_opt_outs(
    qid: str, validation: dict[str, Any] | None, codes: list[int]
) -> list[dict[str, Any]]:
    """The range, unless the answer is one of the opt-out codes."""
    minimum = (validation or {}).get("min")
    maximum = (validation or {}).get("max")
    bounds = [f"{{{qid}}} >= {minimum}" if minimum is not None else "",
              f"{{{qid}}} <= {maximum}" if maximum is not None else ""]
    in_range = " and ".join(b for b in bounds if b)
    if not in_range:
        return []
    opt_out = " or ".join(f"{{{qid}}} = {code}" for code in codes)
    return [{
        "type": "expression",
        "expression": f"{opt_out} or ({in_range})",
        "text": f"Please enter a number from {minimum if minimum is not None else ''}"
                f"{' to ' + str(maximum) if maximum is not None else ''}.",
    }]


def _compile_question(question: Question, by_qid: dict[str, Question]) -> dict[str, Any]:
    """Compile one `Question` into its SurveyJS element dict."""
    qtype = question.type
    techniques = _techniques(question)
    title = _for_respondent(question.text, techniques.get("pipes") or [], by_qid)

    # Info content carries no answer — no `name`, no `isRequired`.
    if qtype == QuestionType.INFO:
        return {"type": "html", "html": title}

    element: dict[str, Any] = {"name": question.qid, "title": title}

    if qtype == QuestionType.SINGLE and _is_numeric_scale(question.options):
        # "0=Very Unfavorable, 1 ... 10=Very Favorable": eleven stacked radio
        # cards pushed the question off the screen. A rating row keeps it in
        # one line; the codes are the same values, so the data is unchanged.
        options = question.options
        element["type"] = "rating"
        element["rateValues"] = [{"value": o.code, "text": str(o.code)} for o in options]
        element["minRateDescription"] = _scale_end(options[0])
        element["maxRateDescription"] = _scale_end(options[-1])
        element["displayMode"] = "buttons"
    elif qtype == QuestionType.SINGLE:
        element["type"] = "radiogroup"
        element["choices"] = _choices(question.options, by_qid)
    elif qtype == QuestionType.MULTI:
        element["type"] = "checkbox"
        element["choices"] = _choices(question.options, by_qid)
        # "Select up to 2." Stated in the wording and, until this, enforced
        # nowhere — so the respondent could tick the whole list. The server
        # rejects an over-cap page too (api/routes/fielding.py): this stops a
        # respondent making the mistake, that stops a client that does not.
        limits = question.validation or {}
        if isinstance(limits.get("max_select"), int) and limits["max_select"] > 0:
            element["maxSelectedChoices"] = limits["max_select"]
        if isinstance(limits.get("min_select"), int) and limits["min_select"] > 0:
            element["minSelectedChoices"] = limits["min_select"]
    elif qtype == QuestionType.DROPDOWN:
        element["type"] = "dropdown"
        element["choices"] = _choices(question.options, by_qid)
    elif qtype == QuestionType.MAXDIFF:
        # Never reached through a normal page: the block is replaced by one page
        # per screen. Compiled as display text so a MaxDiff sharing a page with
        # other questions — which preflight refuses — cannot silently field a
        # broken exercise as an answer list.
        element["type"] = "html"
        element["html"] = f"<p>{element.pop('title', '')}</p>"
    elif qtype == QuestionType.RANK:
        element["type"] = "ranking"
        element["choices"] = _choices(question.options, by_qid)
        # "Rank your top three." over eleven brands — the respondent was being
        # made to order all eleven, because a plain ranking has no notion of a
        # top N. That is not what the question asks, and ranks 4-11 are data
        # the document never asked anyone for. Select-to-rank gives them the
        # three the wording says.
        #
        # ONLY when the document states a cap smaller than the list: a ranking
        # with no cap keeps exactly the interaction it has today.
        top_n = (question.validation or {}).get("max_select")
        if isinstance(top_n, int) and 0 < top_n < len(question.options or []):
            element["selectToRankEnabled"] = True
            element["maxSelectedChoices"] = top_n
    elif qtype in GRID_TYPES:
        element["type"] = "matrix"
        if qtype is QuestionType.GRID_MULTI:
            # Several answers per row; the respondent page reads the flag and
            # stores each row as a list of column codes.
            element["multiAnswer"] = True
        element["columns"] = _choices(question.cols or [], by_qid)
        # A definition stored before grids like Q2a/Q2b were split still heads
        # its columns "Q2a. ever heard of?" — a label the respondent cannot read.
        for column in element["columns"]:
            column["text"] = without_question_label(column["text"])
        element["rows"] = _choices(question.rows or [], by_qid)
        if is_bipolar(question):
            # Two opposite statements per row, the scale between them. Each
            # side is carried separately so a flipped arm can swap them.
            for row in element["rows"]:
                left, right = split(row["text"])
                if left and right:
                    row.update(leftText=left, rightText=right, text=f"{left}  ⟷  {right}")
            element["bipolar"] = True
    elif qtype == QuestionType.NUMERIC and question.rows:
        # One box per item, answered as {code: number} like a grid's row map,
        # and — when the document states one — a total they must reach.
        validation = question.validation or {}
        element["type"] = "multipletext"
        element["items"] = [
            {
                "name": str(row.code),
                "title": _for_respondent(row.label, [], by_qid),
                "inputType": "number",
                "validators": _numeric_validators(validation),
            }
            for row in question.rows
        ]
        total, floor = validation.get("sum"), validation.get("sum_min")
        if total is not None or floor is not None:
            summed = " + ".join(f"{{{question.qid}.{row.code}}}" for row in question.rows)
            element["validators"] = [{
                "type": "expression",
                "expression": (
                    f"{summed} = {total}" if total is not None else f"{summed} >= {floor}"
                ),
                "text": (
                    f"These should add up to {total}." if total is not None
                    else f"These should add up to at least {floor}."
                ),
            }]
    elif qtype == QuestionType.NUMERIC:
        element["type"] = "text"
        element["inputType"] = "number"
        validators = _numeric_validators(question.validation)
        if question.options:
            # "99: Prefer not to answer" beside a number box: a way out of
            # typing. It is stored as its code, so the range check must let the
            # code through even when the code sits outside the range.
            element["optOuts"] = _choices(question.options, by_qid)
            validators = _numeric_validators_with_opt_outs(
                question.qid, question.validation, [o.code for o in question.options]
            )
        if validators:
            element["validators"] = validators
    elif qtype == QuestionType.OPEN_TEXT:
        # A max-length constraint reads as a short single-line answer; otherwise
        # default to a multi-line comment box.
        max_len = (question.validation or {}).get("max_length")
        if max_len is not None:
            element["type"] = "text"
            element["maxLength"] = max_len
        else:
            element["type"] = "comment"
    else:  # pragma: no cover - defensive; enum is exhaustive above
        raise ValueError(f"unsupported question type: {qtype!r}")

    element["isRequired"] = question.required

    if question.randomize_options and "choices" in element:
        # Ordered HERE, once per respondent (the survey is compiled per entry),
        # rather than with SurveyJS's choicesOrder: an anchored option must keep
        # its place. "None of the above [ANCHOR]" shuffled into the middle of
        # the list is a scripting error a client would reject the data for, and
        # the library has no way to pin one choice.
        element["choices"] = _shuffled_choices(question, element["choices"])
    if question.randomize_options and element.get("type") in ("matrix", "matrixdropdown"):
        # A grid's randomisation is its ROWS. Only choices were ever shuffled,
        # so every "[ROWS, RANDOMIZE]" grid reached respondents in one order.
        element["rowsOrder"] = "random"

    if question.show_if is not None:
        element["visibleIf"] = _expression_to_visibleif_string(question.show_if)

    # A mask narrows the option list to what this respondent said earlier. It
    # was being detected, stored, and then ignored — so everyone was offered
    # every option, and picked ones they had never named. The first condition
    # we can express wins; a question carrying two contradictory masks is a
    # scripting error for a human, not something to silently AND together.
    # A grid is masked on its ROWS: "FOR Q2c SHOW BRANDS SELECTED AT Q2b" asks
    # when each brand was last bought only for the brands actually bought.
    masked = "choicesVisibleIf" if "choices" in element else (
        "rowsVisibleIf" if "rows" in element else None
    )
    if masked:
        for mask in techniques.get("masks") or []:
            condition = _choices_visible_if(mask, by_qid)
            if condition:
                # An answer the source never offered — "98 Another brand not
                # listed (Specify)" under an awareness mask — cannot have been
                # chosen there, so the mask hid it from everyone.
                # A write-in or "None" belongs to THIS question even when the
                # source has the same code: "Another brand not listed" in a
                # carousel was shown only to people who ticked Other at Q2a.
                source = _find(by_qid, mask.get("source_qid"))
                listed = (source.options or source.rows or []) if source else []
                offered = {o.code for o in listed}
                own = question.options if masked == "choicesVisibleIf" else (question.rows or [])
                exempt = [
                    o.code for o in own
                    if o.code not in offered or o.open_other or is_terminal_option(o.label)
                    or _WRITE_IN.search(o.label or "")
                ]
                # Lists that code a brand differently (Bubble Q6 by its own
                # 1..10 list, Q2a alphabetically) are matched by name: each
                # choice tests the source's code for the same brand.
                to_own = mask_code_map(question, source) if source is not None else {}
                to_source = {v: k for k, v in to_own.items()}
                if any(k != v for k, v in to_own.items()):
                    exempt = [o.code for o in own if o.code not in to_source
                              or o.open_other or is_terminal_option(o.label)
                              or _WRITE_IN.search(o.label or "")]
                    for item in element["choices" if masked == "choicesVisibleIf" else "rows"]:
                        if item.get("value") in to_source and item.get("value") not in exempt:
                            item["visibleIf"] = condition.replace(
                                "{item}", _format_value(to_source[item["value"]]))
                else:
                    if exempt and mask.get("mode") in ("selected", None):
                        condition = (f"({condition}) or {{item}} anyof "
                                     f"[{', '.join(map(str, exempt))}]")
                    element[masked] = condition
                carried = [to_source.get(o.code, o.code) for o in own
                           if o.code not in exempt and to_source.get(o.code, o.code) in offered]
                if (
                    source is not None and carried and mask.get("mode") in ("selected", None)
                    and source.type in (QuestionType.MULTI, QuestionType.SINGLE)
                ):
                    # Nothing to carry over means nothing to ask: someone who
                    # picked none of Q2a's brands was shown Q2b with only
                    # "None of these" left to tick.
                    picked = f"{{{source.qid}}} anyof [{', '.join(map(str, carried))}]"
                    element["visibleIf"] = (
                        f"({element['visibleIf']}) and {picked}" if element.get("visibleIf")
                        else picked
                    )
                break

    return element


# Content that exists for the scripter, not the respondent. A derived variable
# is computed from other answers and its "wording" is an instruction — showing
# "CODE AGE - BASED ON RESPONSES AT S1" to a member of the public is a defect,
# not display text. `info` alone is not enough to exclude: a genuine intro or
# transition screen is also `info` and SHOULD be shown.
_SCRIPTER_ONLY_FLAGS = frozenset({"DERIVED_VARIABLE"})
# The flag is set at extraction, so a definition stored BEFORE that rule existed
# carries none — and a respondent was shown a page reading "CODE AGE". The
# wording is the same signal the extractor uses, and checking it here means an
# old definition stops leaking without being re-extracted first.


# "Recent Bubble Buyer (Q2c.1=1 OR 2)", "Unaware (Not selected at Q2a)": an
# answer list where the answers are RULES over other questions is a computed
# variable's definition, whatever type it was stored as.
_RULE_OPTION = re.compile(r"\(\s*[A-Z][A-Za-z0-9_.]*\s*=|\bselected at\s+[A-Z]", re.I)


def _options_are_rules(question: Question) -> bool:
    labels = [o.label for o in question.options or []]
    return len(labels) >= 2 and sum(bool(_RULE_OPTION.search(x)) for x in labels) * 2 > len(labels)


def _is_respondent_facing(question: Question) -> bool:
    if _SCRIPTER_ONLY_FLAGS & set(question.flags or ()):
        return False
    # Any type: "BUBBLE AUDIENCE CODE" was stored as a single choice and a
    # respondent was asked to pick which audience segment they are.
    if is_derived_wording(question.text or ""):
        return False
    return not _options_are_rules(question)


def _write_in_elements(question: Question) -> list[dict[str, Any]]:
    """One text box per "Other (Specify)" option, shown only when it is picked.

    Named `<qid>r<code>oe`, as the data file names it. Required while visible:
    a respondent who picked "Other" and typed nothing has told us nothing.
    The parent's own base is part of the condition, so an answer left behind on
    a page the respondent has since been routed away from cannot show the box.
    """
    out: list[dict[str, Any]] = []
    for option in write_ins(question):
        if question.type in (QuestionType.GRID_SINGLE, QuestionType.NUMERIC):
            ref = f"{{{question.qid}.{option.code}}}"  # a row of the answer map
            picked = f"{ref} > 0" if question.type is QuestionType.NUMERIC else f"{ref} notempty"
        else:
            ref = f"{{{question.qid}}}"
            op = "contains" if question.type in _MULTI_VALUED else "="
            picked = f"{ref} {op} {_format_value(option.code)}"
        conditions = [picked]
        if question.show_if is not None:
            conditions.insert(0, _expression_to_visibleif_string(question.show_if))
        if option.show_if is not None:
            conditions.insert(0, _expression_to_visibleif_string(option.show_if))
        visible = picked if len(conditions) == 1 else " and ".join(f"({c})" for c in conditions)
        out.append({
            "type": "text",
            "name": write_in_name(question.qid, option.code),
            "title": write_in_prompt(option.label),
            "isRequired": True,
            "visibleIf": visible,
        })
    return out


def _probe_elements(question: Question) -> list[dict[str, Any]]:
    """The follow-up box after an open question marked for probing.

    Shown only when the answer is thin (`isThin`, registered by the respondent
    page with the same rule as `probes.is_thin`). The title is a fixed probe
    until the page fetches the one written for this answer. Optional: a
    follow-up is an invitation, and a forced one gets "no" typed into it.
    """
    from src.engines.fielding.probes import FALLBACK, PROBE, probed

    if not probed(question):
        return []
    return [{"type": "comment", "name": f"{question.qid}{PROBE}", "title": FALLBACK,
             "isRequired": False, "visibleIf": f"isThin({{{question.qid}}})"}]


def _compile_block(block: Block, survey: SurveyDefinition) -> dict[str, Any]:
    """Compile one `Block` into a SurveyJS page dict."""
    elements: list[dict[str, Any]] = []
    by_qid = {q.qid: q for q in survey.questions}
    for qid in block.question_ids:
        if qid in survey and _is_respondent_facing(survey[qid]):
            elements.append(_compile_question(survey[qid], by_qid))
            elements.extend(_write_in_elements(survey[qid]))
            elements.extend(_probe_elements(survey[qid]))

    page: dict[str, Any] = {"name": block.block_id, "elements": elements}
    if block.label:
        page["title"] = block.label
    if block.randomize_questions:
        page["questionsOrder"] = "random"
    if block.show_if is not None:
        page["visibleIf"] = _expression_to_visibleif_string(block.show_if)
    return page


def compile_to_surveyjs(survey: SurveyDefinition) -> dict[str, Any]:
    """Compile a `SurveyDefinition` into a SurveyJS survey JSON model.

    Returns ``{"title": ..., "pages": [...]}``.

    Everything a respondent reads passes through `_for_respondent` first: a pipe
    becomes the ``{qid}`` marker SurveyJS interpolates, and every remaining
    bracketed directive is removed. The stored question keeps its original
    wording — this transformation exists only at the boundary where text stops
    being ours and starts being the public's.
    """
    pages = [_compile_block(block, survey) for block in survey.blocks]
    # A MaxDiff question is many screens, one per page — its choices are filled
    # in per respondent at entry, because which items somebody sees is their
    # design version. See engines/fielding/maxdiff_screens.
    from src.engines.fielding.maxdiff_screens import expand_pages

    pages = expand_pages(survey, pages)
    compiled = {"title": survey.title, "pages": pages}
    # One mark per question, read from its own wording. Sent as a map rather
    # than per element so it costs nothing in SurveyJS's own model — the
    # respondent page draws it above the card.
    compiled["questionSymbols"] = symbols_for_survey(survey)
    # The parts of the study, as the SCRIPT names them. A block carries a
    # label only when the document gave it one — "Google Ads", "Meta Ads" from
    # the scripter note that groups them — and those are the two arms the study
    # exists to compare. A respondent moved from one to the other with nothing
    # said has no idea the subject changed, and answers the second arm as if it
    # were more of the first.
    #
    # Only the blocks the NOTE named. A block label is otherwise a scripter's
    # own section header — "Screener", "Main", "PLATFORM USAGE" — written for
    # the person building the survey, and a respondent shown that is being
    # shown the scaffolding. "blk_main", where everything else happens to sit,
    # is not a section either.
    from_note = (survey.metadata or {}).get("blocks_from_note") or {}
    respondent_facing = set(from_note.get("block_ids") or ())
    sections = [
        {"page": block.block_id, "label": block.label}
        for block in survey.blocks
        if block.block_id in respondent_facing and (block.label or "").strip()
    ]
    if sections:
        # Read by the respondent page, not by SurveyJS itself.
        compiled["sections"] = sections

    # The pages a scripter note asked to rotate. Carried on the model so the
    # entry point can order them per respondent without re-reading the note —
    # and so a study that asked for no rotation carries nothing at all.
    from src.engines.fielding.rotation import rotating_blocks

    rotating = [b for b in rotating_blocks(survey) if any(p["name"] == b for p in pages)]
    if len(rotating) >= 2:
        compiled["rotatingPages"] = rotating

    # Hidden variables an approved rule computes, so a later question whose
    # base names one ("ASK IF D2a = 4") can be shown in the browser. The server
    # computes the same values itself and never takes these from the client.
    calculated = _calculated_values(survey)
    if calculated:
        compiled["calculatedValues"] = calculated

    grid_masks = _grid_mask_sources(survey)
    if grid_masks:
        # Read by the respondent page, not by SurveyJS itself.
        compiled["gridMaskSources"] = grid_masks
    variables = pipe_variables(compiled)
    if variables:
        # Read by the respondent page, not by SurveyJS itself.
        compiled["pipeVariables"] = variables
    return compiled


def _calculated_values(survey: SurveyDefinition) -> list[dict[str, Any]]:
    from src.engines.questionnaire.derived import has_rule, is_coded_hidden, is_multi_coded

    out = []
    for q in survey.questions:
        if not is_coded_hidden(q) or not has_rule(q) or is_multi_coded(q):
            continue
        expression = "''"
        for option in reversed([o for o in q.options if o.derive_if is not None]):
            condition = _expression_to_visibleif_string(option.derive_if)
            expression = f"iif({condition}, {_format_value(option.code)}, {expression})"
        out.append({"name": q.qid, "expression": expression, "includeIntoResult": False})
    return out


def _shuffled_choices(question: Question, choices: list[dict]) -> list[dict]:
    """Randomise the options that may move; anchored ones stay where they are."""
    movable = [i for i, opt in enumerate(question.options) if not opt.anchored]
    if len(movable) < 2 or len(choices) != len(question.options):
        return choices
    picked = [choices[i] for i in movable]
    random.shuffle(picked)
    out = list(choices)
    for slot, choice in zip(movable, picked, strict=True):
        out[slot] = choice
    return out


_RANK_VARIABLE = re.compile(r"\{([A-Za-z][\w]*)_rank(last|\d+)\}")


def pipe_variables(compiled: dict[str, Any]) -> list[dict[str, Any]]:
    """The per-respondent values a piped ranking needs, from the compiled text.

    Each is "position N of question Q, as the label the respondent read". The
    respondent page computes them as answers change and hands them to SurveyJS
    as variables, which is the only way one place in a ranking can be piped.
    """
    wanted: dict[str, dict[str, Any]] = {}
    for page in compiled.get("pages") or []:
        for element in page.get("elements") or []:
            for text in (element.get("title"), element.get("html")):
                for qid, place in _RANK_VARIABLE.findall(text or ""):
                    name = f"{qid}_rank{place}"
                    wanted[name] = {
                        "name": name,
                        "qid": qid,
                        "position": -1 if place == "last" else int(place),
                    }
    return sorted(wanted.values(), key=lambda v: v["name"])


def display_warnings(survey: SurveyDefinition) -> list[dict[str, str]]:
    """Questions whose respondent-facing text lost something we could not resolve.

    Stripping an unresolvable directive is the right thing to serve — nobody
    should read "[PHARMACY]" — but it leaves a sentence with a hole in it, and
    that is a scripting decision a human has to make before the link goes to a
    panel. Reported rather than silently shipped.
    """
    by_qid = {q.qid: q for q in survey.questions}
    warnings: list[dict[str, str]] = []
    for qid in survey.question_order():
        if qid not in survey:
            continue
        question = survey[qid]
        if not _is_respondent_facing(question):
            continue
        techniques = _techniques(question)
        for pipe in techniques.get("pipes") or []:
            source = pipe.get("source_qid")
            source = _find(by_qid, source).qid if _find(by_qid, source) else source
            verbatim = pipe.get("verbatim") or ""
            if verbatim and verbatim not in (question.text or ""):
                # Already filled in: a loop writes each brand's name where
                # "[INSERT PROVIDER]" stood, and the copied pipe record was
                # reported as a gap in a sentence that no longer has one.
                continue
            if not source or source not in by_qid:
                warnings.append(
                    {
                        "qid": qid,
                        "kind": "unresolved_pipe",
                        "detail": (
                            f"{qid} pipes in '{pipe.get('name') or pipe.get('verbatim')}', "
                            "which is not a question in this survey. The text is removed "
                            "before a respondent sees it, leaving a gap in the sentence."
                        ),
                    }
                )
        for mask in techniques.get("masks") or []:
            if mask.get("mode") == "full_list":
                continue
            if _choices_visible_if(mask, by_qid) is None:
                warnings.append(
                    {
                        "qid": qid,
                        "kind": "unenforceable_mask",
                        "detail": (
                            f"{qid} is masked from "
                            f"{mask.get('exclude_qid') or mask.get('source_qid')}, which is "
                            "not a question in this survey, so its option list cannot be "
                            "narrowed and everyone will see every option."
                        ),
                    }
                )
        if _BRACKETED.search(question.text or ""):
            resolved = _for_respondent(
                question.text, techniques.get("pipes") or [], by_qid
            )
            if not resolved:
                warnings.append(
                    {
                        "qid": qid,
                        "kind": "empty_after_stripping",
                        "detail": (
                            f"{qid}'s wording is entirely scripter directives, so a "
                            "respondent would be shown a question with no text at all."
                        ),
                    }
                )
    return warnings

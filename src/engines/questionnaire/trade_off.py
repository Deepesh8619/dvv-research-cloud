"""MaxDiff and conjoint: experiments, not questions. Detected and refused.

    Q20. [MAXDIFF EXERCISE — 12 SCREENS, 4 ITEMS PER SCREEN]
         Of these features, which is MOST and which is LEAST important?

    Q25. [CONJOINT — 10 TASKS, 3 CONCEPTS + NONE. DESIGN FILE FROM ANALYTICS]
         Which of these plans would you choose?
         Price      $10/mo     $20/mo     $30/mo
         Data       5GB        10GB       Unlimited

Both are experimental designs wearing a question's clothes, and what makes
them work is the part a document never writes down:

* **MaxDiff** shows each respondent a different SUBSET of the items, drawn
  from a balanced incomplete block design so every item appears equally often
  and against every other item equally often. Twelve screens of four items
  from a list of twenty is twelve questions generated per person, no two
  people alike. The answer is not a choice, it is an input to a utility model.
* **Conjoint** builds each concept on screen out of one level per attribute,
  from an orthogonal or D-optimal design, repeated over tasks. The data file
  is long — one row per respondent per task per concept — and the deliverable
  is part-worth utilities, not frequencies.

This platform has neither a design generator nor a utility model. So what
happened before this module was that the block extracted as an ordinary
question: whichever items happened to sit in the paragraph became a static
answer list, every respondent saw the same screen, and the export had one
column where the analyst needed a task-by-concept file. The output looks like
data and cannot be analysed, which is worse than an empty question — it is a
result nobody can tell is wrong until the model refuses to run.

So this does not attempt the exercise. It names the technique, flags the
question in a way that blocks approval, and says the block must be scripted in
a tool that does trade-offs. Refusing an exercise costs a conversation.
Fielding a broken one costs the sample.

Three tiers, in falling certainty, and nothing below them:

1. **The technique is named.** "MAXDIFF", "conjoint", "best-worst". Nobody
   writes these words in a respondent-facing question, so the word decides it.
2. **The machinery is named.** "items per screen", "concepts per task",
   "design file", "Sawtooth" — the wording of somebody who knows this is an
   experiment. The short acronyms (CBC, DCE) only count alongside one of
   these, because three capital letters are also a brand.
3. **The best-worst SHAPE.** A grid with exactly two columns, one meaning
   most and the other least. That is a best-worst exercise whatever it is
   called — and the "exactly two" is what keeps a "Most / Somewhat / Least
   important" rating scale out of it.

WHERE it may read matters as much as what it reads, which three real
questionnaires settled. A spec-table document states the answer outright:

    Q Name: MAXDIFF_LIKELY
    Q type: MaxDiff                 <- the author says so
    Other details: ... motivators maxdiff exercise

...and the SAME words turn up in the "Other details" notes of an ordinary
multi-select four questions earlier, where the author was writing himself a
reminder about a section still to come. Reading the whole source text flagged
that question — an everyday "what are the top barriers?" — as unfieldable.

So only positions that SPEAK FOR THIS QUESTION decide it: the declared type
cell, the question's own name, its wording and choice labels, and bracketed
scripter directives. Free prose in a notes field does not, because a note is
as likely to be about another question as about this one. A question that
merely mentions the exercise ("holds the top benefit from MAXDIFF_LIKELY")
is left alone: it refers to the thing, it is not the thing.

Display text is skipped outright. An intro screen is fieldable whatever it
introduces, and the study-background blob at the top of a document mentions
every technique the project uses.
"""

from __future__ import annotations

import re
from typing import Any

MAXDIFF = "MaxDiff"
CONJOINT = "conjoint"
BEST_WORST = "best-worst"

#: What a reviewer needs to know to act, per technique.
WHY: dict[str, str] = {
    MAXDIFF: (
        "a MaxDiff exercise: each respondent sees a different subset of the items "
        "from a balanced design, and the result is a utility score rather than "
        "counts. There is no design generator here, so every respondent would see "
        "the same screen and the scores could not be computed."
    ),
    CONJOINT: (
        "a conjoint exercise: concepts are built from attribute levels by an "
        "experimental design and repeated over tasks, and the export is one row "
        "per task per concept. There is no design generator here, so the block "
        "would field as a single static question."
    ),
    BEST_WORST: (
        "a best-worst exercise: the respondent picks a most and a least from a "
        "rotating subset of items. Without a design to rotate them, every "
        "respondent sees the same items and the scores are not comparable."
    ),
}

# --- 1. The technique, named -----------------------------------------------
_NAMED: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\bmax[\s-]?diff\b|\bmaximum\s+difference\b", re.I), MAXDIFF),
    (re.compile(r"\bbest[\s/-]?(?:and[\s/-]?)?worst\b", re.I), BEST_WORST),
    (
        re.compile(
            r"\bconjoint\b|\bdiscrete[\s-]choice\b|\bchoice[\s-]based\b|"
            r"\bchoice\s+(?:task|set|exercise|experiment)s?\b|"
            r"\btrade[\s-]?offs?\s+exercise\b",
            re.I,
        ),
        CONJOINT,
    ),
]

# --- 2. The machinery, named -----------------------------------------------
# Wording that only appears when somebody is describing an experimental design.
_MACHINERY = re.compile(
    r"\bitems?\s+per\s+(?:screen|task|set)\b|\bconcepts?\s+per\b|"
    r"\b(?:attributes?\s+and\s+levels?|levels?\s+per\s+attribute)\b|"
    r"\borthogonal\s+design\b|\bbalanced\s+incomplete\s+block\b|\bBIBD\b|"
    r"\bd[\s-]?optimal\b|\bdesign\s+file\b|\bpart[\s-]worth\b|"
    r"\bhierarchical\s+bayes\b|\bsawtooth\b|\blighthouse\s+studio\b",
    re.I,
)
# Three capital letters are also a brand, a clinic and a broadcaster. They
# count only next to machinery or the word design/exercise/task.
_ACRONYM = re.compile(r"\b(?:ACBC|CBC|DCE|DCM)\b")
_DESIGN_WORD = re.compile(r"\b(?:design|exercise|task|concept|attribute)s?\b", re.I)

# --- 3. The best-worst shape ------------------------------------------------
_MOST = re.compile(r"\b(?:most|best|first\s+choice)\b", re.I)
_LEAST = re.compile(r"\b(?:least|worst|last\s+choice)\b", re.I)


def _labels(question: Any) -> list[str]:
    """The choice labels: a grid's columns, else its options."""
    holder = getattr(question, "cols", None) or getattr(question, "options", None) or []
    return [o.label or "" for o in holder]


def _is_best_worst_shape(question: Any) -> bool:
    """Exactly two choices, one meaning most and the other least.

    "Exactly two" is the whole guard. "Most important / Somewhat important /
    Least important" is a three-point rating scale that a real questionnaire
    asks about one thing at a time, and reading it as a trade-off would refuse
    to field an ordinary question.
    """
    labels = _labels(question)
    if len(labels) != 2:
        return False
    most = [bool(_MOST.search(text)) for text in labels]
    least = [bool(_LEAST.search(text)) for text in labels]
    # One column each way round — never one label that says both.
    return sorted(zip(most, least, strict=True)) == [(False, True), (True, False)]


#: A bracketed scripter directive: "[MAXDIFF EXERCISE - 12 SCREENS]".
_BRACKETED = re.compile(r"[\[(]([^\])]{1,200})[\])]")
#: The spec table's own type cell. Letters and spaces only, so the capture
#: stops before the next "Label:" field rather than swallowing the row.
_DECLARED_TYPE = re.compile(r"\bQ\s*type\s*:\s*([A-Za-z][A-Za-z /-]{0,20})", re.I)


def _decisive(question: Any) -> str:
    """The parts of a question that speak FOR it, joined.

    Not the whole source text. A "Other details" note four questions before a
    MaxDiff said "motivators maxdiff exercise", and reading it wholesale
    refused to field an ordinary "what are the top barriers?" multi-select.
    """
    qid = getattr(question, "qid", "") or ""
    source = getattr(question, "source_text", "") or ""
    # The HEADING speaks for the question under it — "MAXDIFF EXERCISE" on the
    # line above the wording is the author labelling this block. A note buried
    # in the middle of the spec does not, which is the whole distinction.
    heading = source.lstrip().split("\n", 1)[0][:200]
    parts = [
        heading,
        # Underscores are word characters, so MAXDIFF_OE hides the word from a
        # \b-anchored pattern until they are spaces — while a REFERENCE to
        # another question ("...from MAXDIFF_LIKELY") stays hidden, which is
        # the difference between being the exercise and mentioning it.
        qid.replace("_", " "),
        getattr(question, "text", "") or "",
        *_labels(question),
        *_BRACKETED.findall(source),
        *_DECLARED_TYPE.findall(source),
    ]
    return " ".join(part for part in parts if part)


def technique(question: Any) -> str | None:
    """Which trade-off exercise this question is, or None.

    Re-read rather than stored, so a questionnaire extracted before this
    existed is told about too — the whole point of a check is that it also
    speaks for definitions nobody is about to re-extract.
    """
    if getattr(question, "type", None) is not None and str(
        getattr(question.type, "value", question.type)
    ) == "info":
        return None
    text = _decisive(question)
    for pattern, name in _NAMED:
        if pattern.search(text):
            return name
    if _MACHINERY.search(text):
        return CONJOINT if _DESIGN_WORD.search(text) else MAXDIFF
    if _ACRONYM.search(text) and (_MACHINERY.search(text) or _DESIGN_WORD.search(text)):
        return CONJOINT
    if _is_best_worst_shape(question):
        return BEST_WORST
    return None


def apply(survey: Any) -> list[str]:
    """Flag every trade-off exercise in the survey. Returns the qids flagged."""
    from src.engines.questionnaire import schema

    flagged = []
    for question in survey.questions:
        found = technique(question)
        if not found:
            continue
        if schema.FLAG_TRADE_OFF_UNSUPPORTED not in question.flags:
            question.flags.append(schema.FLAG_TRADE_OFF_UNSUPPORTED)
        question.extraction_evidence["trade_off"] = found
        flagged.append(question.qid)
    return flagged

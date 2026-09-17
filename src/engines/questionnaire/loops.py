"""A question asked once per brand is not one question.

    Other details: Loop through in randomized order and ask among aware /
    Current or Lapsed Users for each of these brands: Google Ads, Meta Ads,
    LinkedIn, Amazon Ads, and TikTok

That is eight rated statements asked five times: forty data columns, five bases,
five sets of tabs, and a banner cut by brand. Held as one question it is eight
columns and no way to tell one brand's answers from another's — the loop is not
a presentation detail, it is the shape of the data.

So a loop is EXPANDED here, into one real question per iteration, because every
stage downstream — the compiler, the data map, the materializer, the tab plan —
reads a flat list of questions and would each need its own idea of a loop
otherwise. Expanding is also what a scripter does by hand, which means the
result is the thing they would have written.

What is NOT guessed: the per-iteration base. The document defines who sees each
brand in prose ("Provider aware based on HID_AWARE, Provider Current or Lapsed
User based on DA_PLATFORM_USAGE [...]"), naming ten user states across two
questions. Inventing a condition there would send a brand's questions to the
wrong people and nothing downstream could tell. Each expansion is flagged for a
human to set its base, which is a smaller job than writing five questions.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from src.core.survey_definition import Question, SurveyDefinition
from src.engines.questionnaire import schema

# "Loop through ... for each of these brands: A, B, C, and D". The verb is
# required: "ask for each of the following" without it is usually a grid's row
# list, not a repeated question.
_LOOP_RE = re.compile(
    r"\bloop(?:ing|ed)?\b[^:\n]*?\bfor\s+each\s+(?:of\s+)?(?:these|the)?\s*"
    r"[\w\s/-]*?:\s*(?P<items>[^.\n]+)",
    re.I,
)
# "[INSERT PROVIDER]", "[INSERT BRAND]" — where the iteration's name belongs.
_PLACEHOLDER_RE = re.compile(r"\[\s*(?:INSERT|PIPE)[^\]]*\]", re.I)
_RANDOM_ORDER_RE = re.compile(r"\brandomi[sz]ed?\s+order\b|\bin\s+random\s+order\b", re.I)

#: A loop with one iteration is not a loop; a loop with fifty is a misread list.
_MIN_ITERATIONS = 2
_MAX_ITERATIONS = 20
#: An iteration is a NAME. Anything this long is a sentence we misread.
_MAX_LABEL_WORDS = 6


@dataclass
class Loop:
    """What a loop instruction states."""

    labels: list[str]
    randomised: bool
    verbatim: str


def parse_loop(text: str) -> Loop | None:
    """The loop a scripter note states, or None if it does not state one exactly."""
    match = _LOOP_RE.search(text or "")
    if not match:
        return None
    labels = _labels(match.group("items"))
    if not _MIN_ITERATIONS <= len(labels) <= _MAX_ITERATIONS:
        return None
    if any(len(label.split()) > _MAX_LABEL_WORDS for label in labels):
        return None  # a sentence, not a list of names
    return Loop(
        labels=labels,
        randomised=bool(_RANDOM_ORDER_RE.search(text or "")),
        verbatim=match.group(0).strip(),
    )


def _labels(raw: str) -> list[str]:
    parts = re.split(r",|\band\b|\bor\b", raw or "", flags=re.I)
    out: list[str] = []
    for part in parts:
        label = part.strip(" \t.;:")
        if label and label.lower() not in {"etc", "others"}:
            out.append(label)
    return list(dict.fromkeys(out))


def expand_loops(survey: SurveyDefinition) -> list[dict]:
    """Replace each looped question with one question per iteration."""
    applied: list[dict] = []

    for template in list(survey.questions):
        loop = parse_loop(_loop_text(template))
        if loop is None:
            continue
        expansions = [
            _iteration(template, loop, index, label)
            for index, label in enumerate(loop.labels, start=1)
        ]
        _replace(survey, template, expansions)
        applied.append(
            {
                "qid": template.qid,
                "iterations": [q.qid for q in expansions],
                "labels": list(loop.labels),
                "randomised_order": loop.randomised,
                "source": loop.verbatim,
            }
        )

    if applied:
        survey.metadata["loops"] = applied
        survey._rebuild_index()
    return applied


def _loop_text(question: Question) -> str:
    """Where a loop instruction is written: the scripter's notes, not the wording."""
    validation = question.validation or {}
    parts = [
        str(validation.get("scripter_directives") or ""),
        str(validation.get("notes") or ""),
        str(question.extraction_evidence.get("loop") or ""),
        question.source_text or "",
    ]
    return "\n".join(parts)


def _iteration(template: Question, loop: Loop, index: int, label: str) -> Question:
    """One turn of the loop, as its own question."""
    text = template.text or ""
    if _PLACEHOLDER_RE.search(text):
        # "...about [INSERT PROVIDER]" — the placeholder IS where the name goes.
        text = _PLACEHOLDER_RE.sub(label, text)
    else:
        text = f"{text} ({label})".strip()

    validation = dict(template.validation or {})
    validation["loop"] = {
        "of": template.qid,
        "index": index,
        "label": label,
        "randomised_order": loop.randomised,
    }
    question = Question(
        qid=f"{template.qid}_{index}",
        text=text,
        type=template.type,
        options=list(template.options or []),
        rows=list(template.rows) if template.rows else None,
        cols=list(template.cols) if template.cols else None,
        required=template.required,
        randomize_options=template.randomize_options,
        # Deliberately NOT carried: the base. The document states who sees each
        # iteration in prose naming several user states across two questions,
        # and copying the template's base would ask every brand of everybody.
        terminate_if=template.terminate_if,
        piping_refs=list(template.piping_refs or []),
        validation=validation,
        source_text=template.source_text,
        source_location=template.source_location,
        extraction_method="loop_expansion",
        extraction_evidence={**template.extraction_evidence, "loop": loop.verbatim},
        flags=[*template.flags, schema.FLAG_AMBIGUOUS_ROUTING],
    )
    return question


def _replace(survey: SurveyDefinition, template: Question, expansions: list[Question]) -> None:
    """Put the iterations where the template was, and take the template out.

    The template must go: left in place it is a question nobody is asked that
    still emits its own columns, and every one of its answers would be
    double-counted against the iterations that replaced it.
    """
    index = survey.questions.index(template)
    survey.questions[index : index + 1] = expansions
    for block in survey.blocks:
        if template.qid in block.question_ids:
            position = block.question_ids.index(template.qid)
            block.question_ids[position : position + 1] = [q.qid for q in expansions]

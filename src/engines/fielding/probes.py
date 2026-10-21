"""One follow-up when an open answer is thin, the way a moderator would ask.

"Lower the rates", "Nothing comes to mind", "efficacy and safety": a
quarter of the open answers on a real B2B study say too little to code. In an
interview a moderator would ask once more. A question the researcher marks
for probing (`validation.probe`) gets a follow-up box right after it, shown
only when the answer is thin; its wording is written for that answer by an
open model, neutral and non-leading, and falls back to a fixed probe when no
model answers or the model writes something unusable. What was asked is
recorded beside the answer (`<qid>_PROBEQ`, `<qid>_PROBE`).

One follow-up per question per respondent: a second model call for the same
person is answered from the record, so a public link cannot be used to run
the model at will.
"""

from __future__ import annotations

import logging
import re

from src.core.survey_definition import Question, QuestionType

logger = logging.getLogger(__name__)

PROBE, ASKED = "_PROBE", "_PROBEQ"
MIN_WORDS = 6
_VAGUE = re.compile(
    r"^\W*(good|bad|ok(ay)?|fine|nice|great|nothing( (else|comes to mind))?|none|no|yes|nope|"
    r"na|n/?a|nil|idk|i don'?t know|don'?t know|dont know|not sure|no idea|same|"
    r"no comment|-+|\.+)\W*$", re.I)
_NOTHING = re.compile(r"\b(nothing|none|no idea|not sure|don'?t know|idk|n/?a)\b", re.I)
# What a moderator never says: a question that hands over the answer.
_LEADING = re.compile(r"\b(don'?t you|wouldn'?t you|isn'?t it|surely|agree that)\b", re.I)
FALLBACK = "Could you tell us a little more about that? What makes you say so?"
FALLBACK_NOTHING = "Is there anything at all that comes to mind, even something small?"

SCHEMA = {
    "type": "object",
    "properties": {"probe": {"type": "string", "description": "One follow-up question"}},
    "required": ["probe"],
}
PROMPT = (
    "You are a neutral market research interviewer. A respondent gave a short answer "
    "to a survey question. Write ONE follow-up question, at most 25 words, in the same "
    "language as the answer, that invites them to say more. Do not suggest an answer, "
    "do not agree or disagree, do not mention brands they did not mention.\n"
    "Question: {question}\nAnswer: {answer}\nReturn only JSON."
)


def probed(q: Question) -> bool:
    return q.type is QuestionType.OPEN_TEXT and (q.validation or {}).get("probe") is True


def is_thin(answer: str | None) -> bool:
    """Too little to code: a stock reply, or fewer than six words."""
    text = (answer or "").strip()
    if not text:
        return False  # unanswered is not thin; the question's own rules apply
    return bool(_VAGUE.match(text)) or len(re.findall(r"[^\W\d_]{2,}", text)) < MIN_WORDS


def fallback(answer: str) -> str:
    return FALLBACK_NOTHING if _NOTHING.search(answer or "") else FALLBACK


def _usable(text: str) -> bool:
    return (10 <= len(text) <= 200 and text.endswith("?") and "http" not in text
            and "<" not in text and not _LEADING.search(text))


def write_probe(question_text: str, answer: str, timeout: int = 8) -> tuple[str, str]:
    """(follow-up, "model" | "fixed") for one thin answer."""
    from src.ai.router import run_task

    prompt = PROMPT.format(question=question_text[:500], answer=answer[:500])
    try:
        text = run_task("fielding.probe", prompt, SCHEMA, timeout=timeout).content["probe"]
        text = " ".join(str(text).split()).strip("\"'“” ")
        if _usable(text):
            return text, "model"
        logger.info("probe from the model refused: %r", text[:120])
    except Exception as exc:  # no model reachable: the fixed probe still asks
        logger.info("probe model unavailable: %s", exc)
    return fallback(answer), "fixed"

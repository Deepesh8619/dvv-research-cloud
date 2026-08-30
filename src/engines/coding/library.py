"""Find a codeframe you've already built for a question like this one.

Trackers ask the same questions wave after wave, and different studies for the
same client ask near-identical ones. Today every one of those regenerates a
codeframe from scratch: an LLM call, and — worse — a DIFFERENT set of codes each
time, so wave 2 cannot be compared to wave 1 without someone reconciling by
hand.

Reuse fixes the expensive half (a call saved) and the commercially important
half (the same question is coded the same way, so waves stack).

Matching is deliberately plain: normalise, then compare word sets. No
embeddings, no vector store, no new dependency. Survey questions are short and
formulaic — "Which brand do you use most often?" recurs almost verbatim — so
exact-after-normalisation catches most real reuse, and token overlap catches
the rest. If this proves insufficient in practice, Neon supports pgvector and
this module is the seam to swap; measuring first is cheaper than assuming.

Nothing here auto-applies. A match is a SUGGESTION with a score and a reason,
because silently reusing the wrong codeframe would corrupt coded data in a way
nobody would notice until the tables looked strange.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Words that carry no distinguishing signal between survey questions. Kept
# small and MR-specific on purpose: strip too much and "brand" questions start
# matching "store" questions.
_STOPWORDS = frozenset(
    """
    a an the and or of to in on for with at by from is are was were be been
    do does did you your yours we our us they them their it its this that
    these those which what how why when where who whom please select choose
    all any some other others none if then than as so
    """.split()
)

# Survey text is full of scripter noise that is not part of the question:
# "(SINGLE)", "[ASK IF Q3=1]", "Q12.", trailing punctuation.
_SCRIPTER_NOISE = re.compile(r"\[[^\]]*\]|\([^)]*\)|^\s*[A-Za-z]{0,3}\d+[a-z]?[.):]\s*")
_NON_WORD = re.compile(r"[^a-z0-9\s]")


def normalize_question(text: str) -> str:
    """Comparable form of a question: no ids, no scripter notes, no punctuation."""
    low = (text or "").strip().lower()
    low = _SCRIPTER_NOISE.sub(" ", low)
    low = _NON_WORD.sub(" ", low)
    return " ".join(low.split())


def content_words(text: str) -> frozenset[str]:
    """The words that actually distinguish one question from another."""
    return frozenset(w for w in normalize_question(text).split() if w not in _STOPWORDS)


def similarity(a: str, b: str) -> float:
    """0..1 overlap between two questions' content words (Jaccard).

    Jaccard rather than raw overlap so that a SHORT question does not match a
    long one just by being a subset of it: "Which brand?" should not score 1.0
    against "Which brand do you buy for your children on weekdays?".
    """
    wa, wb = content_words(a), content_words(b)
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / len(wa | wb)


# An identical question after normalisation — the common tracker case.
EXACT = 1.0
# Below this, questions are too different for a codeframe to transfer. Chosen
# to be conservative: a wrong codeframe silently miscodes real data, whereas a
# missed match only costs one LLM call.
SUGGEST_FLOOR = 0.6


@dataclass
class CodeframeMatch:
    """One candidate from the library, with why it was offered."""

    entry_id: str
    question_text: str
    score: float
    reason: str
    study_id: str | None = None
    times_reused: int = 0

    @property
    def exact(self) -> bool:
        return self.score >= EXACT

    def to_dict(self) -> dict:
        return {
            "entry_id": self.entry_id,
            "question_text": self.question_text,
            "score": round(self.score, 3),
            "reason": self.reason,
            "study_id": self.study_id,
            "times_reused": self.times_reused,
            "exact": self.exact,
        }


def find_matches(
    question_text: str,
    library: list[dict],
    *,
    floor: float = SUGGEST_FLOOR,
    limit: int = 5,
) -> list[CodeframeMatch]:
    """Rank library entries against `question_text`, best first.

    `library` entries are plain dicts (id, question_text, study_id,
    times_reused) so this stays free of the ORM and testable without a
    database.

    Ties break on `times_reused`: a codeframe the team has already reused is
    more likely to be the house standard than one used once.
    """
    if not normalize_question(question_text):
        return []

    scored: list[CodeframeMatch] = []
    for entry in library:
        candidate = entry.get("question_text") or ""
        score = similarity(question_text, candidate)
        if score < floor:
            continue
        if score >= EXACT:
            reason = "Identical question — reuse keeps waves comparable"
        elif score >= 0.8:
            reason = "Nearly the same question, minor rewording"
        else:
            reason = "Similar question — check the codes still fit"
        scored.append(
            CodeframeMatch(
                entry_id=str(entry.get("id")),
                question_text=candidate,
                score=score,
                reason=reason,
                study_id=entry.get("study_id"),
                times_reused=int(entry.get("times_reused") or 0),
            )
        )

    scored.sort(key=lambda m: (m.score, m.times_reused), reverse=True)
    return scored[:limit]

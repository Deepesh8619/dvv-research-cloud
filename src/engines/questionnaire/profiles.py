"""Questionnaire format profiles: recognize the authoring convention first.

Different clients author questionnaires differently — GBK/Google ships each
question as a labelled `Q Name` spec-table, a classic MR screener uses `Q5.` /
`S3.` prose labels, a lightweight doc uses bare `1.` / `2.` numbering. Running
ONE identical pipeline on all of them is exactly the gap the owner named
("don't take every qnre as the same"). This module adds the missing first step:
classify the document's format from purely STRUCTURAL signals (no LLM), then let
the extractor adapt the prompt to it.

Extensibility contract — "everyone has a different qnre":
    Adding a new client format = append ONE `QuestionnaireProfile` to `REGISTRY`
    with its own `scorer`. `detect_profile` iterates the registry and picks the
    best score; it never needs editing for a new format. A profile's `scorer`
    reads the shared `BlockStats` (computed once) and returns
    (confidence, signals) — the signals ride into the review UI for transparency.

Detection is DETERMINISTIC: it counts `Q Name` table rows, prose-label hits, and
bare-number hits over the block list, then scores each profile by how much of the
document's question-ish content it explains. On no confident match the result is
`UNKNOWN_PROFILE`, whose prompt hint keeps extraction EXACTLY as it is today — the
profile step is never allowed to make an unrecognized document worse.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field

from src.intake.questionnaire_loader import _BASE_TAG_RE, _QID_RE, DocumentBlock, _table_qid

# Bare `1.` / `2.` / `3)` numbering with NO letter prefix — the lightweight
# format. A trailing separator + space keeps it from matching a `1: Yes` option
# code (colon) or a bare year/number in prose.
_PLAIN_NUM_RE = re.compile(r"^\s*\d+[.)]\s+\S")

# Below this best score, no profile is trusted and we fall back to UNKNOWN_PROFILE
# (which routes to today's generic pipeline). Keeps "never worse than today".
_CONFIDENCE_FLOOR = 0.5

# Prompt-selection hints a profile hands to build_extraction_prompt. Kept as a
# tiny closed set (not one-per-slug) so a NEW client profile can reuse an
# existing guidance block — e.g. another spec-table client just uses FIELD_SPEC.
HINT_FIELD_SPEC = "field_spec"  # GBK-style labelled spec table -> _FIELD_SPEC_GUIDANCE
HINT_PROSE = "prose"  # classic prose / bare-number labels -> no extra guidance
HINT_CONSERVATIVE = "conservative"  # unknown format -> "extract conservatively" note


@dataclass(frozen=True)
class BlockStats:
    """Structural counts over one document's blocks, computed once for scoring."""

    n_blocks: int
    qname_tables: int  # table blocks carrying a `Q Name | <token>` row (GBK)
    prose_labels: int  # paragraphs starting with a Q5./S3./P1.-style label
    plain_numbers: int  # paragraphs starting with bare 1./2. numbering
    #: paragraphs OPENING with "[BASE: …]" — the unnumbered healthcare format,
    #: where the base tag is what starts each question. Counted only when no
    #: question label exists at all; in a labelled document it is a directive.
    base_tags: int = 0

    @property
    def question_ish(self) -> int:
        """Total blocks that look like a question start (any convention)."""
        return self.qname_tables + self.prose_labels + self.plain_numbers + self.base_tags


def compute_stats(blocks: list[DocumentBlock]) -> BlockStats:
    """Count the format signals in one pass. Deterministic, no LLM."""
    qname = prose = plain = 0
    for b in blocks:
        if _table_qid(b) is not None:
            qname += 1
        elif b.block_type != "table":
            if _QID_RE.match(b.text):
                prose += 1
            elif _PLAIN_NUM_RE.match(b.text):
                plain += 1
    tags = 0
    if not qname and not prose:
        tags = sum(1 for b in blocks if b.block_type != "table" and _BASE_TAG_RE.match(b.text))
    return BlockStats(
        n_blocks=len(blocks), qname_tables=qname, prose_labels=prose, plain_numbers=plain,
        base_tags=tags,
    )


@dataclass(frozen=True)
class QuestionnaireProfile:
    """One recognized authoring convention + how to score and extract it.

    `scorer(stats) -> (confidence, signals)` is the ONLY per-profile logic the
    detector calls, so a new client format is a new entry here, never a detector
    edit. `prompt_hint` tells build_extraction_prompt which guidance to append.
    """

    slug: str
    label: str
    prompt_hint: str
    scorer: Callable[[BlockStats], tuple[float, dict]]


def _ratio_scorer(count_attr: str) -> Callable[[BlockStats], tuple[float, dict]]:
    """A scorer = (this convention's share of all question-ish blocks).

    Confidence is the fraction of detected question starts that match THIS
    convention, so the dominant format in a document wins and a document with no
    matching signal scores 0. The raw counts travel in `signals` for the UI.
    """

    def score(stats: BlockStats) -> tuple[float, dict]:
        count = getattr(stats, count_attr)
        total = stats.question_ish
        confidence = (count / total) if (count and total) else 0.0
        return confidence, {
            count_attr: count,
            "question_ish": total,
        }

    return score


# The registry. Append here to teach the detector a new client format — nothing
# else changes. `numbered_plain` deliberately shares the PROSE prompt hint: a
# bare-`1.` question extracts identically to a `Q5.` one (same prose chunk, no
# spec-table guidance), so it needs recognition for transparency, not a separate
# extraction path.
REGISTRY: list[QuestionnaireProfile] = [
    QuestionnaireProfile(
        slug="gbk_spec_table",
        label="GBK scripter spec-table",
        prompt_hint=HINT_FIELD_SPEC,
        scorer=_ratio_scorer("qname_tables"),
    ),
    QuestionnaireProfile(
        slug="prose_labelled",
        label="Prose Q-labels (Q5./S3.)",
        prompt_hint=HINT_PROSE,
        scorer=_ratio_scorer("prose_labels"),
    ),
    QuestionnaireProfile(
        slug="base_tagged",
        label="Unnumbered, one [BASE: …] tag per question",
        prompt_hint=HINT_PROSE,
        scorer=_ratio_scorer("base_tags"),
    ),
    QuestionnaireProfile(
        slug="numbered_plain",
        label="Plain numbered (1./2.)",
        prompt_hint=HINT_PROSE,
        scorer=_ratio_scorer("plain_numbers"),
    ),
]

# Returned when nothing scores above the floor: keeps extraction identical to
# today (conservative hint appends only a light note, and the per-chunk
# _has_field_spec fallback still fires for a stray spec-table chunk).
UNKNOWN_PROFILE = QuestionnaireProfile(
    slug="unknown",
    label="Unrecognized format",
    prompt_hint=HINT_CONSERVATIVE,
    scorer=lambda stats: (0.0, {}),
)


@dataclass
class ProfileDetection:
    """The detector's verdict: which profile, how sure, and why."""

    profile: QuestionnaireProfile
    confidence: float
    signals: dict = field(default_factory=dict)

    def to_metadata(self) -> dict:
        """Compact, reviewer-facing form stashed on survey.metadata['profile']."""
        return {
            "slug": self.profile.slug,
            "label": self.profile.label,
            "confidence": round(self.confidence, 4),
            "signals": self.signals,
        }


def detect_profile(blocks: list[DocumentBlock]) -> ProfileDetection:
    """Classify a document's format by iterating the registry (deterministic).

    Scores every profile against the shared stats and returns the highest; if the
    best score is below `_CONFIDENCE_FLOOR` (or there are no blocks) returns
    `UNKNOWN_PROFILE` so the pipeline behaves exactly as it does today. Adding a
    profile to REGISTRY is picked up here with no code change.
    """
    stats = compute_stats(blocks)
    best: ProfileDetection | None = None
    for profile in REGISTRY:
        confidence, signals = profile.scorer(stats)
        if best is None or confidence > best.confidence:
            best = ProfileDetection(profile=profile, confidence=confidence, signals=signals)

    if best is None or best.confidence < _CONFIDENCE_FLOOR:
        return ProfileDetection(
            profile=UNKNOWN_PROFILE,
            confidence=best.confidence if best else 0.0,
            signals={
                "qname_tables": stats.qname_tables,
                "prose_labels": stats.prose_labels,
                "plain_numbers": stats.plain_numbers,
            },
        )
    return best


def _demo() -> None:
    """Self-check: each convention classifies as itself; noise -> unknown."""
    def para(text: str) -> DocumentBlock:
        return DocumentBlock("paragraph", text, 0)

    def qname_table(token: str) -> DocumentBlock:
        return DocumentBlock("table", f"Q Name: {token}", 0, rows=[["Q Name", token]])

    gbk = detect_profile([qname_table("RESP_AGE"), qname_table("SS_EMP")])
    assert gbk.profile.slug == "gbk_spec_table" and gbk.confidence >= 0.9, gbk

    prose = detect_profile([para("Q5. Are you aware?"), para("S3. How old?")])
    assert prose.profile.slug == "prose_labelled", prose

    plain = detect_profile([para("1. Are you aware?"), para("2. How old?")])
    assert plain.profile.slug == "numbered_plain", plain

    noise = detect_profile([para("Thank you for taking part."), para("Please continue.")])
    assert noise.profile.slug == "unknown", noise

    print("profiles demo OK")


if __name__ == "__main__":  # pragma: no cover - manual self-check
    _demo()

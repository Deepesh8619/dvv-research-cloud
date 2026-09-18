"""Founder-taught patterns that the deterministic parsers consult.

The correction loop already teaches the *model*: an approved rule is prose that
gets appended to the extraction prompt. That makes the system more accurate and
never cheaper — the same convention on the next client document still costs a
model call, every time, forever.

This module closes that gap. A correction can now write a **pattern** the
parsers read directly, so the second document that uses a convention is parsed
for free. The measurable claim is cost per document falling as founders work,
not just accuracy rising.

Six kinds, one per decision the parsers actually make:

    type_signal      "[SHOWCARD]"        -> single          (both parsers)
    field_alias      "Asked of:"         -> base            (spec table rows)
    option_tag       "[SO]"              -> terminate       (option suffixes)
    randomize_signal "[SCRAMBLE]"        -> (no value)      (directive lines)
    screen_out_range "CONTINUE IF AGES"  -> (no value)      (numeric range screen-out)
    hidden_variable  "SAMPLE SOURCE"     -> (no value)      (computed, never asked)

**Patterns are literal fragments, not regexes.** A founder types what they see
in the document — `[SHOWCARD]` — and it is matched case-insensitively as a
substring. Accepting regexes would hand an unprivileged text box the ability to
hang extraction (a catastrophic backtrack) and would fail confusingly on the
brackets that appear in nearly every real directive. Literal matching is both
safer and closer to what a founder means.

Nothing here imports the database. Patterns arrive as plain dataclasses through
the same provider seam that already carries `learned_rules`, so the engines stay
free of `api.db`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from src.core.survey_definition import QuestionType

# What a pattern of each kind is allowed to mean. Validated at the boundary so
# a malformed rule is rejected when it is taught, not silently ignored for
# months while a founder believes the system learned something.
TYPE_VALUES = frozenset(t.value for t in QuestionType)
FIELD_VALUES = frozenset(
    {"qid", "type", "base", "show", "randomize", "notes", "text"}
)
OPTION_TAG_VALUES = frozenset({"terminate", "exclusive", "anchor"})

PATTERN_KINDS = frozenset(
    {
        "type_signal",
        "field_alias",
        "option_tag",
        "randomize_signal",
        "screen_out_range",
        "hidden_variable",
    }
)

_ALLOWED_VALUES: dict[str, frozenset[str]] = {
    "type_signal": TYPE_VALUES,
    "field_alias": FIELD_VALUES,
    "option_tag": OPTION_TAG_VALUES,
}


@dataclass(frozen=True)
class LearnedPattern:
    """One convention a founder taught, for one format profile.

    `pattern` is a literal fragment as it appears in the document.
    `value` is what it means — see `_ALLOWED_VALUES`; unused by
    `randomize_signal`, `screen_out_range` and `hidden_variable`, whose
    presence is the whole meaning.
    """

    kind: str
    pattern: str
    value: str = ""

    @property
    def needle(self) -> str:
        """The fragment to look for, lowercased.

        Square brackets are stripped. A founder types the directive exactly as
        the document shows it — `[SO]` — but the parsers hand us the text
        INSIDE the brackets when matching an option tag, so a literal `[SO]`
        would never match. Stripping here means "write what you see" works for
        every kind, instead of silently failing for one of them.
        """
        return self.pattern.strip().strip("[]").strip().lower()


def validate_pattern(kind: str, pattern: str, value: str) -> str | None:
    """Return a human-readable reason this pattern is unusable, or None.

    Called before storing, so the founder finds out immediately. The empty and
    whitespace checks matter more than they look: an empty needle is a
    substring of every string, so it would match every question in the
    document and silently retype the whole questionnaire.
    """
    if kind not in PATTERN_KINDS:
        kinds = ", ".join(sorted(PATTERN_KINDS))
        return f"Unknown pattern kind '{kind}'. Expected one of: {kinds}."
    if not (pattern or "").strip():
        return "The text to look for cannot be empty."
    if len((pattern or "").strip()) < 2:
        return "The text to look for is too short to identify a convention reliably."
    allowed = _ALLOWED_VALUES.get(kind)
    if allowed is None:
        return None  # randomize_signal carries no value
    if value not in allowed:
        return f"'{value}' is not valid for {kind}. Expected one of: {', '.join(sorted(allowed))}."
    return None


def _matches(pattern: LearnedPattern, haystack: str) -> bool:
    needle = pattern.needle
    return bool(needle) and needle in (haystack or "").lower()


def of_kind(
    patterns: list[LearnedPattern] | tuple[LearnedPattern, ...] | None, kind: str
) -> list[LearnedPattern]:
    return [p for p in (patterns or ()) if p.kind == kind]


def learned_type(
    patterns: list[LearnedPattern] | tuple[LearnedPattern, ...] | None,
    *haystacks: str,
) -> tuple[QuestionType, str] | None:
    """A taught type signal found in any of `haystacks`, with its evidence.

    Checked BEFORE the built-in table: a founder teaching their client's
    convention is stating something the built-ins cannot know, so the taught
    pattern wins. Returns the matched pattern as evidence so the review UI can
    still cite where the answer came from.
    """
    for pattern in of_kind(patterns, "type_signal"):
        for haystack in haystacks:
            if _matches(pattern, haystack):
                try:
                    return QuestionType(pattern.value), f"learned: {pattern.pattern}"
                except ValueError:
                    # Stored value no longer maps to a type (an enum rename).
                    # Skip rather than crash a whole extraction on one bad row.
                    break
    return None


def learned_field(
    patterns: list[LearnedPattern] | tuple[LearnedPattern, ...] | None, label: str
) -> str | None:
    """Which spec-table field a taught row label maps to."""
    for pattern in of_kind(patterns, "field_alias"):
        if _matches(pattern, label):
            if pattern.value in FIELD_VALUES:
                return pattern.value
    return None


def learned_option_tags(
    patterns: list[LearnedPattern] | tuple[LearnedPattern, ...] | None, tag_text: str
) -> set[str]:
    """Meanings a taught option tag carries ({"terminate"}, {"exclusive"}...)."""
    return {
        p.value
        for p in of_kind(patterns, "option_tag")
        if _matches(p, tag_text) and p.value in OPTION_TAG_VALUES
    }


def learned_randomize(
    patterns: list[LearnedPattern] | tuple[LearnedPattern, ...] | None, body: str
) -> str | None:
    """The taught randomisation directive matched here, if any."""
    for pattern in of_kind(patterns, "randomize_signal"):
        if _matches(pattern, body):
            return pattern.pattern
    return None


# The range written after a taught screen_out_range phrase: "CONTINUE IF AGES
# 18-60" -> 18, 60. Fixed and internal, never built from what a founder types —
# the taught phrase only says WHERE to look; this regex says HOW to read what
# follows it, same security boundary as every other kind here.
_RANGE_AFTER_RE = re.compile(r"(\d{1,3})\s*-\s*(\d{1,3})")


def learned_screen_out_range(
    patterns: list[LearnedPattern] | tuple[LearnedPattern, ...] | None, text: str
) -> tuple[int, int, str] | None:
    """A taught screen_out_range phrase in `text`, with the range after it.

    Returns (low, high, matched pattern) for the first taught phrase whose
    following text holds a parseable N-M range; None if no taught phrase
    matches or none is followed by one.
    """
    haystack = text or ""
    low_haystack = haystack.lower()
    for pattern in of_kind(patterns, "screen_out_range"):
        needle = pattern.needle
        if not needle:
            continue
        idx = low_haystack.find(needle)
        if idx == -1:
            continue
        match = _RANGE_AFTER_RE.search(haystack[idx + len(needle) :])
        if not match:
            continue
        low, high = int(match.group(1)), int(match.group(2))
        if low <= high:
            return low, high, pattern.pattern
    return None


def learned_hidden_variable(
    patterns: list[LearnedPattern] | tuple[LearnedPattern, ...] | None, text: str
) -> str | None:
    """The taught hidden-variable phrase found in `text`, if any."""
    for pattern in of_kind(patterns, "hidden_variable"):
        if _matches(pattern, text):
            return pattern.pattern
    return None


def _demo() -> None:
    """Self-check: taught patterns win, bad ones are refused, empties can't match."""
    patterns = [
        LearnedPattern("type_signal", "[SHOWCARD]", "single"),
        LearnedPattern("field_alias", "Asked of", "base"),
        LearnedPattern("option_tag", "[SO]", "terminate"),
        LearnedPattern("randomize_signal", "[SCRAMBLE]"),
        LearnedPattern("screen_out_range", "CONTINUE IF AGES"),
        LearnedPattern("hidden_variable", "SAMPLE SOURCE"),
    ]
    assert learned_type(patterns, "Q1 [showcard]") == (
        QuestionType.SINGLE,
        "learned: [SHOWCARD]",
    )
    assert learned_type(patterns, "nothing here") is None
    assert learned_field(patterns, "Asked of:") == "base"
    assert learned_option_tags(patterns, "SO") == {"terminate"}
    assert learned_randomize(patterns, "[PN: SCRAMBLE]") == "[SCRAMBLE]"
    assert learned_screen_out_range(
        patterns, "continue if ages 18-60. terminate all others"
    ) == (18, 60, "CONTINUE IF AGES")
    assert learned_screen_out_range(patterns, "nothing here") is None
    assert learned_hidden_variable(patterns, "S9. Sample Source") == "SAMPLE SOURCE"
    # A regex-looking taught phrase is matched literally, never compiled: "."
    # would match any character as a regex, so "SAMPLE" would wrongly match a
    # "S.MPLE" pattern if it were compiled. Literal matching refuses that.
    regex_pattern = [LearnedPattern("hidden_variable", "S.MPLE")]
    assert learned_hidden_variable(regex_pattern, "the wording says SAMPLE here") is None
    assert learned_hidden_variable(regex_pattern, "the wording says S.MPLE here") == "S.MPLE"

    # An empty needle is a substring of everything — it must never be stored.
    assert validate_pattern("type_signal", "  ", "single") is not None
    assert validate_pattern("type_signal", "[X]", "not_a_type") is not None
    assert validate_pattern("nonsense", "[X]", "single") is not None
    assert validate_pattern("type_signal", "[SHOWCARD]", "single") is None
    print("learned.py self-check OK")


if __name__ == "__main__":
    _demo()

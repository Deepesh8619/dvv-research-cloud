"""One authenticity score per respondent, every reason shown, built from evidence.

The industry's top problem is data quality, and AI is now part of the fraud: a
physician study export held a 1,168-character answer typed with ONE keystroke,
the copied text recorded beside it. Decipher already writes, per open end, the
keystroke and character counts, the pasted text, keyboard suggestions,
dictation and a paste flag — and all of it was being classified as "system
columns" and thrown away.

Evidence, strongest first:

- **Typing** — a recorded paste; few keystrokes for a long answer that no
  autocomplete, suggestion or dictation explains. Dictation is never a fault.
- **AI-written text** — the phrases a language model leaves behind.
- **Reading time** — finished faster than the words they were asked could be
  skimmed at 600 words a minute (three times normal reading speed).
- **Person-fit** — answers that contradict the item order everyone else gives
  (normalised Guttman errors, `person_fit.py`).
- **The existing checks** — speeders, straight-liners, duplicates, gibberish,
  copied open ends, failed traps.

The score is the sum of the reasons' weights, capped at 100; removal stays a
person's decision on a ranked, explained list. Weights are a first setting,
to be retuned on what a fielded study's reviewers find (the "cries wolf" rule).
"""

from __future__ import annotations

import re
from typing import Any

import polars as pl

from src.core.models import DataMap, VarType
from src.engines.qc.respondents import _find_duration_column

#: Points per reason; a respondent's score is the sum, capped at 100.
WEIGHTS = {
    "pasted_answer": 35,
    "ai_text": 30,
    "too_fast_to_read": 30,
    "duplicate": 30,
    "trap_failed": 40,
    "speeder": 20,
    "fraud_signal": 20,
    "few_keystrokes": 15,
    "gibberish": 15,
    "straightliner": 10,
    "inconsistent": 20,
    "person_fit": 15,
}
#: Review first at or above this; worth a look from REVIEW_AT.
HIGH_AT, REVIEW_AT = 60, 30

_SUFFIXES = {
    "keys": "KeyStrokeCount", "chars": "CharacterCount", "copy": "CopyResponse",
    "pasted": "Copy_Paste_Flagged", "speech": "speechToTextText",
    "suggest": "keyboardSuggestText", "manual": "Manualtyping",
}
# "As an AI language model", "I don't have personal experience" — what a
# model leaves in an answer that a person would not write.
_AI_PHRASES = re.compile(
    r"\bas an ai\b|\bas a language model\b|\bas an artificial intelligence\b|"
    r"\bi (?:do not|don't) have personal (?:experience|opinions)\b|"
    r"\bi cannot (?:provide|answer) personal\b",
    re.I,
)
#: Words a minute no one reads faster than: three times a normal reading speed.
SKIM_WPM = 600


def _typing_groups(columns: list[str]) -> dict[str, dict[str, str]]:
    """Per open end, the typing-trace columns an export recorded for it."""
    groups: dict[str, dict[str, str]] = {}
    for col in columns:
        for kind, suffix in _SUFFIXES.items():
            if col.lower().endswith(suffix.lower()):
                # hQ15OE_ / hQ15_ / hGA_X_OEOE_ / hGA_X_OE all name one open end.
                stem = re.sub(r"(?:_|OE)+$", "", col[: -len(suffix)])
                stem = re.sub(r"^h", "", stem) or stem
                groups.setdefault(stem, {})[kind] = col
    return {k: v for k, v in groups.items() if "keys" in v or "copy" in v or "pasted" in v}


def _filled(value: Any) -> bool:
    return value not in (None, "", 0, "0", 0.0) and str(value).strip() != ""


def _num(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def typing_flags(df: pl.DataFrame) -> dict[int, list[tuple[str, str]]]:
    """Pasted and implausibly-typed open ends, from the export's typing traces."""
    out: dict[int, list[tuple[str, str]]] = {}
    groups = _typing_groups(df.columns)
    if not groups:
        return out
    for i, row in enumerate(df.iter_rows(named=True)):
        found: list[tuple[str, str]] = []
        for name, cols in groups.items():
            cell = lambda kind, c=cols, r=row: r.get(c.get(kind, ""))  # noqa: E731
            chars, keys = _num(cell("chars")), _num(cell("keys"))
            # A paste (the export's flag, or copied text) counts only where the
            # keystrokes fall well short of the answer: 111 keys for 65
            # characters is typing, whatever word was pasted along the way.
            typed_short = keys is None or (chars is not None and keys < 0.5 * chars)
            pasted = typed_short and (_num(cell("pasted")) == 1 or _filled(cell("copy")))
            explained = any(_filled(cell(k)) for k in ("speech", "suggest", "manual"))
            if pasted:
                size = f" {int(chars)} characters" if chars else ""
                typed = "" if keys is None else f" with {int(keys)} keystroke" + (
                    "" if keys == 1 else "s")
                found.append(("pasted_answer", f"{name}: pasted{size}{typed}"))
            elif (chars and chars >= 40 and keys is not None and keys < 0.3 * chars
                  and not explained):
                found.append(("few_keystrokes",
                              f"{name}: {int(keys)} keystrokes for {int(chars)} characters"))
        if found:
            out[i] = found
    return out


def ai_text_flags(df: pl.DataFrame, datamap: DataMap) -> dict[int, list[tuple[str, str]]]:
    """Open ends carrying what a language model leaves in its answers."""
    out: dict[int, list[tuple[str, str]]] = {}
    texts = [v.name for v in datamap.variables
             if v.type == VarType.TEXT and v.name in df.columns
             and v.provenance not in ("fielding_paradata",)]
    for name in texts:
        for i, value in enumerate(df[name].to_list()):
            if isinstance(value, str) and (m := _AI_PHRASES.search(value)):
                out.setdefault(i, []).append(("ai_text", f'{name}: "{m.group(0)}"'))
    return out


def _words(text: str | None) -> int:
    return len(re.findall(r"[A-Za-z]{2,}", text or ""))


def reading_speed_flags(df: pl.DataFrame, datamap: DataMap) -> dict[int, list[tuple[str, str]]]:
    """Finished faster than the words they were asked could be skimmed."""
    out: dict[int, list[tuple[str, str]]] = {}
    duration = _find_duration_column(df, datamap)
    if duration is None:
        return out
    # What a respondent sees: each question's text once, plus its answers.
    # Hidden variables, MaxDiff design columns and script columns are never
    # shown, and counting them put 8,000 "words" on a 12-minute survey.
    from src.engines.qc.variable_roles import classify_variable

    columns: dict[str, list[str]] = {}
    words: dict[str, int] = {}
    for v in datamap.variables:
        if v.name not in df.columns or v.provenance == "fielding_paradata" or v.name == duration:
            continue
        role = classify_variable(v.name, v.label or "", v.question_ref)
        if role not in ("question", "punch"):
            continue
        key = v.question_ref or v.question_text or v.name
        columns.setdefault(key, []).append(v.name)
        if role == "punch":
            # A punch's own words: its label minus the question it belongs to.
            own = (v.label or "").replace(v.question_text or "", "")[:120]
            words[key] = max(words.get(key, 0), _words(v.question_text)) + _words(own)
        else:
            words[key] = max(words.get(key, 0), _words(v.question_text or v.label)
                             + sum(_words(label) for label in (v.values or {}).values()))
    if not columns:
        return out
    for i, row in enumerate(df.iter_rows(named=True)):
        seconds = _num(row.get(duration))
        if seconds is None or seconds <= 0:
            continue
        seen = sum(words[k] for k, cols in columns.items()
                   if any(row.get(c) is not None for c in cols))
        floor = seen / SKIM_WPM * 60
        if seen >= 150 and seconds < floor:
            out[i] = [(
                "too_fast_to_read",
                f"finished in {int(seconds)}s; skimming the {seen} words they were shown "
                f"takes at least {int(floor)}s",
            )]
    return out


def score(flags: list[dict]) -> dict[str, Any]:
    """The score, its band and the reasons, from one respondent's flags."""
    reasons = [f for f in flags if f.get("check") in WEIGHTS]
    seen: dict[str, int] = {}
    total = 0
    for f in reasons:
        # A second instance of the same check adds half: two pasted answers are
        # stronger evidence than one, not twice as strong.
        n = seen.get(f["check"], 0)
        total += WEIGHTS[f["check"]] if n == 0 else WEIGHTS[f["check"]] // 2
        seen[f["check"]] = n + 1
    value = min(100, total)
    band = "review first" if value >= HIGH_AT else "worth a look" if value >= REVIEW_AT else "fine"
    return {"score": value, "band": band,
            "reasons": [f"{f['check'].replace('_', ' ')}: {f['detail']}" for f in reasons]}


def scores(flags_by_key: dict[str, list[dict]]) -> dict[str, dict[str, Any]]:
    return {key: score(found) for key, found in flags_by_key.items()}


_LABELS = {
    "pasted_answer": "Pasted answer", "ai_text": "Reads like AI text",
    "too_fast_to_read": "Faster than reading", "duplicate": "Duplicate answers",
    "trap_failed": "Failed a trap", "speeder": "Speeder", "fraud_signal": "Fraud signal",
    "few_keystrokes": "Few keystrokes", "gibberish": "Gibberish", "straightliner": "Straight-lined",
    "inconsistent": "Inconsistent answers",
    "person_fit": "Answers don't fit the pattern",
}


def signals(flags: list[dict], near_duplicate_of: str | None = None) -> dict[str, Any]:
    """The evidence panel's view of `score`: the same number the list ranks by,
    with each reason named and weighted. A near-identical open end found by the
    vector index is one more `duplicate` reason, unless one is already there."""
    found = list(flags)
    if near_duplicate_of and not any(f.get("check") == "duplicate" for f in found):
        found.append({"check": "duplicate",
                      "detail": f"an open answer is almost identical to {near_duplicate_of}'s"})
    result = score(found)
    seen: dict[str, int] = {}
    named = []
    for f in found:
        if f.get("check") not in WEIGHTS:
            continue
        n = seen.get(f["check"], 0)
        seen[f["check"]] = n + 1
        named.append({"key": f["check"], "label": _LABELS.get(f["check"], f["check"]),
                      "weight": WEIGHTS[f["check"]] if n == 0 else WEIGHTS[f["check"]] // 2,
                      "detail": f.get("detail", "")})
    return {"score": result["score"], "band": result["band"], "signals": named}


def near_duplicate_partners(db, study_id: str) -> dict[str, str]:
    """{session id: another session id with a near-identical open end}; {} with no index."""
    from src.platform.vectors import near_duplicates

    out: dict[str, str] = {}
    for a, b, _s in near_duplicates(db, study_id):
        sa, sb = a.split(":", 1)[-1], b.split(":", 1)[-1]
        if sa != sb:
            out.setdefault(sa, sb)
            out.setdefault(sb, sa)
    return out

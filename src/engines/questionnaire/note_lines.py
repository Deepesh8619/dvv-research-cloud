"""Every note on a question, whatever form the document writes it in.

One reader for `[PN: …]`, `[…]`, `PROGRAMMER NOTE: …`, a "Scripter instructions:"
table row, `Filter: …`, `Base: …`, `→ Asked if …` and a plain "Ask if …" line, so a
rule is applied the same way however it was marked.
"""

from __future__ import annotations

import re
from itertools import pairwise

_WRAPPED = re.compile(r"^\s*\[\s*(?:(?:PN|PROG(?:RAMMER)?\s+NOTE|SCRIPTER\s+NOTE)\s*:\s*)?(.*?)\s*\]\s*$", re.I)  # noqa: E501
_PREFIXED = re.compile(
    r"^\s*(?:programm(?:er|ing)\s+(?:notes?|instructions?)|scripter\s+(?:notes?|instructions?)|"
    r"scripting\s+(?:notes?|instructions?)|notes?\s+to\s+programmer|prog\s+notes?|pn|notes?|"
    r"other\s+details|instructions?)\s*:\s*(.+)$", re.I)
_PLAIN = re.compile(
    r"^\s*(?:→\s*)?(?:ask(?:ed)?\s+(?:only\s+)?(?:if|for|those|everyone)\b|show\s+(?:module\s+)?(?:only\s+)?if\b|"
    r"base\s*(?::|selects?\b)|filter\b|anchor\b|randomi[sz]e\b|must\s+select\b|acceptable\s+range\b|"
    r"allow\s+|accept\s+|do\s+not\s+allow\b|cannot\s+combine\b|create\s+)", re.I)
_NOT_A_NOTE = re.compile(r"^\s*(?:SECTION\s*:|INSERT\b|PIPE\b)", re.I)
_PAREN = re.compile(r"^\s*\((.+)\)\s*$")


def note_lines(question) -> list[str]:
    """The note lines on a question, in document order, markers removed."""
    out = []
    for line in (question.source_text or "").splitlines():
        if m := _WRAPPED.match(line):
            if m.group(1) and not _NOT_A_NOTE.match(m.group(1)):
                out.append(m.group(1).strip())
        elif m := _PREFIXED.match(line):
            out.append(m.group(1).strip())
        elif _PLAIN.match(line):
            out.append(line.strip().lstrip("→").strip())
        elif (m := _PAREN.match(line)) and _PLAIN.match(m.group(1)):
            out.append(m.group(1).strip())
    return out


_STOP = re.compile(r"(?<=[.!])\s+(?=[A-Z])|(?<=[a-z]{2}[.!])(?=[A-Z])")  # no space: "terminate.If"
_CREATE = re.compile(r"\bCreate\b(?=[^.]*\bvariable\b)")


def _create_parts(line: str) -> list[str]:
    """A second "Create … variable" on one line starts a new note."""
    cuts = [m.start() for m in _CREATE.finditer(line)][1:]
    if not cuts:
        return [line]
    bounds = [0, *cuts, len(line)]
    return [line[a:b] for a, b in pairwise(bounds)]


def wording_lines(question) -> list[str]:
    """The lines on a question that are not notes: what a respondent would read."""
    out = []
    for line in (question.source_text or "").splitlines():
        if not line.strip() or _WRAPPED.match(line) or _PREFIXED.match(line) or _PLAIN.match(line):
            continue
        if (m := _PAREN.match(line)) and _PLAIN.match(m.group(1)):
            continue
        out.append(line.strip())
    return out


def sentences(lines: list[str]) -> list[str]:
    return [s.strip() for line in lines for part in _create_parts(line)
            for s in _STOP.split(part) if s.strip()]


def _tokens_at(text: str, pos: int):
    """Every reading of one band starting at pos: (letter, low, high or None, end)."""
    letter = text[pos] if pos < len(text) and text[pos].isalpha() else ""
    start = pos + len(letter)
    for lo_len in (1, 2, 3):
        low_txt = text[start:start + lo_len]
        if len(low_txt) != lo_len or not low_txt.isdigit():
            continue
        after = start + lo_len
        if after < len(text) and text[after] == "+":
            yield letter, int(low_txt), None, after + 1
        elif after < len(text) and text[after] == "-":
            for hi_len in (1, 2, 3):
                hi_txt = text[after + 1:after + 1 + hi_len]
                if len(hi_txt) == hi_len and hi_txt.isdigit():
                    yield letter, int(low_txt), int(hi_txt), after + 1 + hi_len


def _parse_groups(text: str, pos: int = 0, prev_high: int | None = None):
    """Every band from pos to the end, or None. Ranges rise; a letter-led band restarts."""
    if pos == len(text):
        return []
    for letter, low, hi, end in _tokens_at(text, pos):
        if not (10 <= low <= 120) or (hi is not None and not (low < hi and 10 <= hi <= 120)):
            continue
        if not letter and prev_high is not None and low < prev_high:
            continue
        rest = _parse_groups(text, end, None if letter or hi is None else hi)
        if rest is not None:
            return [(letter, low, hi), *rest]
    return None


def split_groups(text: str) -> list[str]:
    """Run-together age or gender bands, split: "18-3435-4950-6465+" -> four bands.

    Only for the text after ':' in a "create … variable" note. Anything that
    does not parse cleanly comes back whole, as a one-item list.
    """
    stripped = re.sub(r"\s+", "", text)
    groups = _parse_groups(stripped) if stripped else None
    if not groups or len(groups) < 2:
        return [text.strip()]
    out = []
    for letter, low, hi in groups:
        out.append(f"{letter}{low}+" if hi is None else f"{letter}{low}-{hi}")
    return out

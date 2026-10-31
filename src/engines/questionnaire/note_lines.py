"""Every note on a question, whatever form the document writes it in.

One reader for `[PN: …]`, `[…]`, `PROGRAMMER NOTE: …`, a "Scripter instructions:"
table row, `Filter: …`, `Base: …`, `→ Asked if …` and a plain "Ask if …" line, so a
rule is applied the same way however it was marked.
"""

from __future__ import annotations

import re

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


def sentences(lines: list[str]) -> list[str]:
    return [s.strip() for line in lines
            for s in re.split(r"(?<=[.!])\s+(?=[A-Z])", line) if s.strip()]

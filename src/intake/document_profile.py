"""Learn how THIS document is written, before any question is read.

An analyst skims a questionnaire first: how are questions labelled, how are
notes to the scripter marked, do routing lines sit above or below the question
they govern, what does the brief require? Every reader step used to assume its
own answer. One document writes `<ROUTE: ASK IF MQ2=1-6>` above each question,
another `[BASE: Q6=1-7]`, another `Filter: Q3 in (1,3)`, another `Ask if S2=1` in
a notes row underneath.

Everything here is counted from the document. A convention is adopted only when
the document uses it repeatedly, so one stray line changes nothing.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import replace
from typing import Any

MIN_USES = 3

# A line that states who is asked, in any of the forms documents use.
BASE_LINE = re.compile(
    r"^\s*(?:[\[<(→]\s*)?(?:PN\s*:\s*|ROUTE\s*:\s*)?"
    r"(?:base\s*(?::|selects?\b)|filter\b\s*[:–—-]?|universe\s*:|"
    r"ask(?:ed)?\s+(?:only\s+)?(?:if|those|everyone|for)\b|show\s+(?:only\s+)?if\b|if\s+[A-Za-z.]+\s*\d*\s*(?:=|in\b))",
    re.I)
# Tags closing a line ("<MC>", "<THANK AND CLOSE>", a whole "<ROUTE: …>" line).
# Inside the wording, "<BRAND>" is a pipe, not a note.
_ANGLE_LINE = re.compile(r"<[A-Za-z][^<>]{0,160}>(?=(?:\s*<[^<>]*>)*\s*$)")
_SOFT_NOTE = re.compile(r"^\s*(?:→\s*(.+?)|\(\s*((?:only\s+show|show\s+only|ask\s|rotate|randomi[sz]e)"  # noqa: E501
                       r"[^)]*)\))\s*$", re.I)
_INLINE_ANSWER = re.compile(r"([^,()]+?(?:\[[^\]]*\])?)\s*\((\d{1,3})\)\s*,?\s*")
_LABEL_START = re.compile(r"^\s*(?:Q\.?\s?)?[A-Za-z]{0,6}\.?\d{1,3}[A-Za-z]?\s*[.):\t–—-]")
_SPACE_LABEL = re.compile(r"^\s*([A-Z]{1,4}\d{1,3}[a-z]?)\s+(\S.*)$", re.S)
_ANSWER_LINE = re.compile(r"^\s*(?:\d{1,3}[.):\t]|[a-z][.)]\s)")
_ENFORCE = re.compile(r"enforce\s+(?:a\s+)?response\s+to\s+each\s+question|"
                      r"all\s+questions\s+(?:are\s+)?(?:mandatory|required)", re.I)
_MARKERS = {
    "[PN: …]": re.compile(r"^\s*\[\s*PN\s*:", re.I),
    "[…]": re.compile(r"^\s*\[(?!\s*PN\s*:)[^\]]+\]\s*$"),
    "<…>": re.compile(r"^\s*<[A-Za-z][^<>]*>\s*$"),
    "PROGRAMMER NOTE:": re.compile(r"^\s*(?:programm(?:er|ing)|scripter)\s+notes?\s*:", re.I),
    "Base:": re.compile(r"^\s*base\s*:", re.I),
    "Filter": re.compile(r"^\s*filter\b", re.I),
    "Ask if (plain line)": re.compile(r"^\s*ask(?:ed)?\s+(?:only\s+)?(?:if|those|for)\b", re.I),
}


def _text_blocks(blocks) -> list:
    return [b for b in blocks if b.block_type != "table" and (b.text or "").strip()]


def uses_angle_notes(blocks) -> bool:
    return sum(1 for b in _text_blocks(blocks) if _ANGLE_LINE.search(b.text)) >= MIN_USES


def normalise(blocks: list) -> list:
    """Rewrite the document's own ways of writing notes and answers into the one
    form the readers know. Each only when the document uses it repeatedly."""
    angle = uses_angle_notes(blocks)
    inline = sum(1 for b in _text_blocks(blocks) if len(_INLINE_ANSWER.findall(b.text)) >= 2)
    out: list = []
    for b in blocks:
        text = b.text or ""
        if b.block_type == "table" or not text:
            out.append(b)
            continue
        if angle:
            text = _ANGLE_LINE.sub(lambda m: f"[{m.group(0)[1:-1]}]", text)
        if m := _SOFT_NOTE.match(text):
            # "→ Asked if Q.8 includes 1-6", "(Only show the answers chosen at Q.8)"
            text = f"[{(m.group(1) or m.group(2)).strip()}]"
        items = _INLINE_ANSWER.findall(text) if inline >= MIN_USES else []
        if len(items) >= 2 and _INLINE_ANSWER.sub("", text).strip(" ,") == "":
            # "Male (1), Female (2), Prefer not to say (4)": one answer each.
            out += [replace(b, text=f"{code}. {label.strip()}") for label, code in items]
            continue
        out.append(replace(b, text=text) if text != b.text else b)
    return out


def space_labels(blocks: list, known_prefixes: Counter) -> dict[int, tuple[str, str]]:
    """"S5 Which brands do you use?": a label with only a space after it.

    Too loose to accept alone ("Q3 2026 results"), so only for a prefix the
    document uses repeatedly, in non-decreasing number order, on lines that ask.
    """
    found: dict[str, list[tuple[int, str, str, int]]] = {}
    for i, b in enumerate(blocks):
        if b.block_type == "table" or b.label or not (m := _SPACE_LABEL.match(b.text or "")):
            continue
        prefix = re.match(r"[A-Z]+", m.group(1)).group(0)
        number = int(re.search(r"\d+", m.group(1)).group(0))
        found.setdefault(prefix, []).append((i, m.group(1), m.group(2).strip(), number))
    out: dict[int, tuple[str, str]] = {}
    for prefix, hits in found.items():
        numbers = [h[3] for h in hits]
        if ((len(hits) >= 5 or (known_prefixes[prefix] >= MIN_USES and len(hits) >= 1))
                and numbers == sorted(numbers)):
            out.update({i: (qid, rest) for i, qid, rest, _ in hits})
    return out


def routing_position(blocks: list) -> str:
    """Do routing lines sit "above" the question they govern, or "below" it?

    A base line straight before a label governs what follows; one with no label
    after it, under a question's answers, governs what came before.
    """
    lines = _text_blocks(blocks)
    above = below = 0
    for i, b in enumerate(lines[:-1]):
        if not BASE_LINE.match(b.text):
            continue
        nxt = lines[i + 1]
        if nxt.label or _LABEL_START.match(nxt.text) or (nxt.list_label or "")[:1].isalpha():
            above += 1
        else:
            below += 1
    return "above" if above >= MIN_USES and above > below else "below"


def build_profile(blocks: list, cards: int = 0) -> dict[str, Any]:
    """What the document's own habits are, for the reader and for the reviewer."""
    lines = _text_blocks(blocks)
    tables = sum(1 for b in blocks if b.block_type == "table")
    markers = Counter()
    for b in lines:
        for name, pattern in _MARKERS.items():
            if pattern.match(b.text):
                markers[name] += 1
                break
    labelled = sum(1 for b in lines if b.label or _LABEL_START.match(b.text))
    numbered = sum(1 for b in lines if (b.list_label or "").strip()[:1].isalpha())
    layout = ("question cards" if cards >= MIN_USES
              else "Word auto-numbering" if numbered >= MIN_USES
              else "one table per question" if tables >= max(MIN_USES, labelled)
              else "labelled paragraphs" if labelled >= MIN_USES
              else "no visible labels")
    text = " ".join(b.text for b in lines) + " " + " ".join(
        b.text or "" for b in blocks if b.block_type == "table")
    return {
        "layout": layout,
        "note_markers": dict(markers.most_common()),
        "routing_position": routing_position(blocks),
        "answer_required_by_brief": bool(_ENFORCE.search(text)),
    }

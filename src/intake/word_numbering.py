"""The number Word shows in front of an auto-numbered paragraph.

Many agencies never type a question number: Word's list numbering draws it.
The number lives in numbering.xml, not in the text, so a reader of the text
sees no number at all — and every question gets a name the document never
gave it. In the referrer study the first list item was a scripter note, so
every question came out one lower than the document says ("[INSERT Q2
RESPONSE]" then pointed at the wrong question).

This renders the label the way Word does in the final view: counters per
list, deeper levels reset when a shallower one advances, `start` values and
per-list `startOverride` restarts honoured, and the level's `lvlText`
("%1.", "Q%1", "%1.%2") filled in with each level's own format. Bullets have
no number and return None.
"""

from __future__ import annotations

from docx.oxml.ns import qn

_ROMAN = [(1000, "m"), (900, "cm"), (500, "d"), (400, "cd"), (100, "c"), (90, "xc"),
          (50, "l"), (40, "xl"), (10, "x"), (9, "ix"), (5, "v"), (4, "iv"), (1, "i")]


def _roman(n: int) -> str:
    out = ""
    for value, letters in _ROMAN:
        while n >= value:
            out += letters
            n -= value
    return out


def _letters(n: int) -> str:
    # Word repeats the letter past z: a..z, aa..zz.
    return chr(ord("a") + (n - 1) % 26) * ((n - 1) // 26 + 1)


def _format(n: int, fmt: str) -> str | None:
    if fmt in ("decimal", "decimalZero"):
        return f"{n:02d}" if fmt == "decimalZero" and n < 10 else str(n)
    if fmt == "lowerLetter":
        return _letters(n)
    if fmt == "upperLetter":
        return _letters(n).upper()
    if fmt == "lowerRoman":
        return _roman(n)
    if fmt == "upperRoman":
        return _roman(n).upper()
    return None  # bullet, none, and formats no questionnaire uses


def _val(element, tag: str) -> str | None:
    found = element.find(qn(tag)) if element is not None else None
    return found.get(qn("w:val")) if found is not None else None


class ListNumbering:
    """Visible list labels for the paragraphs of one document, in order."""

    def __init__(self, document) -> None:
        self._levels: dict[str, dict[int, dict]] = {}
        self._num_to_abstract: dict[str, str] = {}
        self._overrides: dict[str, dict[int, int]] = {}
        self._counters: dict[str, dict[int, int]] = {}
        self._restarted: set[tuple[str, int]] = set()
        try:
            numbering = document.part.numbering_part.element
        except (KeyError, NotImplementedError, AttributeError):
            return  # a document with no lists at all
        for abstract in numbering.findall(qn("w:abstractNum")):
            levels = {}
            for lvl in abstract.findall(qn("w:lvl")):
                levels[int(lvl.get(qn("w:ilvl")))] = {
                    "fmt": _val(lvl, "w:numFmt") or "decimal",
                    "text": _val(lvl, "w:lvlText") or "",
                    "start": int(_val(lvl, "w:start") or 1),
                }
            self._levels[abstract.get(qn("w:abstractNumId"))] = levels
        for num in numbering.findall(qn("w:num")):
            num_id = num.get(qn("w:numId"))
            self._num_to_abstract[num_id] = _val(num, "w:abstractNumId")
            for override in num.findall(qn("w:lvlOverride")):
                start = _val(override, "w:startOverride")
                if start is not None:
                    self._overrides.setdefault(num_id, {})[
                        int(override.get(qn("w:ilvl")))] = int(start)

    @staticmethod
    def _num_pr(paragraph):
        p_pr = paragraph._p.pPr
        if p_pr is not None and p_pr.numPr is not None and p_pr.numPr.numId is not None:
            return p_pr.numPr
        style = paragraph.style
        while style is not None:
            s_pr = style.element.pPr
            if s_pr is not None and s_pr.numPr is not None and s_pr.numPr.numId is not None:
                return s_pr.numPr
            style = style.base_style
        return None

    def label(self, paragraph) -> str | None:
        """Advance the paragraph's list and return its visible label."""
        num_pr = self._num_pr(paragraph)
        if num_pr is None:
            return None
        num_id = str(num_pr.numId.val)
        if num_id == "0":
            return None  # numbering explicitly switched off
        level = int(num_pr.ilvl.val) if num_pr.ilvl is not None else 0
        abstract = self._num_to_abstract.get(num_id)
        levels = self._levels.get(abstract or "", {})
        if level not in levels:
            return None
        counters = self._counters.setdefault(abstract, {})
        start = self._overrides.get(num_id, {}).get(level)
        if start is not None and (num_id, level) not in self._restarted:
            # A list that restarts: its first paragraph at this level begins
            # again at the override, whatever the shared list had reached.
            self._restarted.add((num_id, level))
            counters[level] = start
        else:
            counters[level] = counters.get(level, levels[level]["start"] - 1) + 1
        for deeper in [k for k in counters if k > level]:
            del counters[deeper]
        text = levels[level]["text"]
        if _format(1, levels[level]["fmt"]) is None:
            return None
        for depth in range(level + 1):
            spec = levels.get(depth)
            if spec is None:
                continue
            value = counters.get(depth, spec["start"])
            text = text.replace(f"%{depth + 1}", _format(value, spec["fmt"]) or "")
        return text.strip() or None

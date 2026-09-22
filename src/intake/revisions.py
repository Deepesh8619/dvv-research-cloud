"""What a questionnaire says was TAKEN OUT, and what was added.

A tracking study arrives as last wave's document with this wave's edits on
top. In the Northwell Vivo wave-2 questionnaire that is 151 runs of red
strikethrough: brands dropped from lists, wording cut, and a whole question
(Q13b, with its ASK IF directive) struck out. None of it is part of the survey
being fielded — and all of it was being read as live content, so removed
answers would have been offered to respondents and a deleted question asked.

Two conventions, both handled here because both mean the same thing:

* **Word's own tracked changes.** `w:del` is deleted text and `w:ins` is
  inserted text. Deleted text is not in the document any more; Word only shows
  it until somebody accepts the change.
* **Struck through by hand**, usually coloured. This is what market-research
  documents actually use, because the edit has to be visible to a human
  reading the Word file rather than hidden behind a review setting.

Deletions are dropped and recorded — never silently, because "the brand is
missing from Q3" and "the brand was struck out in the document" are different
problems and a reviewer must be able to tell them apart. Insertions are kept:
new wording IS the questionnaire now.
"""

from __future__ import annotations

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _is_struck(run) -> bool:
    """A run struck through by hand, single or double."""
    properties = run.find(f"{W}rPr")
    if properties is None:
        return False
    for kind in (f"{W}strike", f"{W}dstrike"):
        element = properties.find(kind)
        # <w:strike w:val="false"/> turns it OFF again — a real thing in
        # documents edited more than once.
        if element is not None and element.get(f"{W}val") not in ("false", "0"):
            return True
    return False


def _is_deleted(run) -> bool:
    """A run inside a Word tracked deletion."""
    parent = run.getparent()
    while parent is not None:
        if parent.tag == f"{W}del":
            return True
        if parent.tag in (f"{W}p", f"{W}tc"):
            return False
        parent = parent.getparent()
    return False


def live_text(paragraph) -> tuple[str, list[str]]:
    """(what this paragraph still says, what was taken out of it).

    Word keeps deleted text in `w:delText` rather than `w:t`, so a tracked
    deletion contributes nothing to the visible text on its own; a hand
    strikethrough sits in ordinary `w:t` and has to be dropped here.
    """
    kept: list[str] = []
    removed: list[str] = []
    for run in paragraph._p.iter(f"{W}r"):
        text = "".join(node.text or "" for node in run.findall(f"{W}t"))
        gone = "".join(node.text or "" for node in run.findall(f"{W}delText"))
        if gone:
            removed.append(gone)
        if not text:
            continue
        if _is_struck(run) or _is_deleted(run):
            removed.append(text)
        else:
            kept.append(text)
    return "".join(kept).strip(), [r.strip() for r in removed if r.strip()]


def live_cell_text(cell) -> tuple[str, list[str]]:
    """The same reading for one table cell: a struck row of a brand list."""
    kept: list[str] = []
    removed: list[str] = []
    for paragraph in cell.paragraphs:
        text, gone = live_text(paragraph)
        if text:
            kept.append(text)
        removed.extend(gone)
    return "\n".join(kept).strip(), removed

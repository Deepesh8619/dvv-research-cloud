"""Question labels an owner taught by example, applied to any document using them.

Every format the reader has never seen fails the same way: it cannot tell where
a question starts. Rather than a code change per format, an owner shows ONE
label exactly as the document writes it — "QGENDER.", "Q.5", "Question 5:",
"S5c)" — and its shape is learned:

    "S5c."         -> letters, digits, optional letter, "."
    "QGENDER."     -> an all-caps word, "."
    "Question 5:"  -> the word "Question", a number, ":"

The owner never writes a regex. The shape is built here from character classes,
so it cannot hang extraction, and it is anchored to the start of a line.

A shape is applied to a document only when it matches at least MIN_MATCHES of
its lines: one taught label must not split a document written another way, and
a shape the document does not use costs nothing. Because of that gate, taught
labels are shared across formats — the second client who writes "QGENDER." is
read correctly with nobody teaching anything.
"""

from __future__ import annotations

import re

MIN_MATCHES = 3
MAX_EXAMPLE = 30


def shape_from_example(example: str) -> re.Pattern | None:
    """The label shape an example stands for, or None if it is not a label."""
    text = (example or "").strip()
    if not (2 <= len(text) <= MAX_EXAMPLE) or not re.search(r"[A-Za-z]", text):
        return None
    parts: list[str] = []
    for m in re.finditer(r"[A-Za-z]+|\d+|\s+|.", text):
        token, after = m.group(0), text[m.end():m.end() + 1]
        if token.isdigit():
            parts.append(r"\d{1,4}[A-Za-z]{0,2}")  # "5", "5c", "12ab"
        elif token.isspace():
            parts.append(r"\s+")
        elif token.isalpha():
            if after == " " and len(token) >= 3:
                parts.append(re.escape(token))  # a keyword: "Question 5:"
            elif token.isupper() and not after.isdigit() and len(token) > 3:
                # A named label: "QGENDER.", also "QS5c." — never "Thanks."
                parts.append(r"[A-Z][A-Z0-9_]{1,24}[a-z]{0,2}")
            elif parts and parts[-1].startswith(r"\d"):
                continue  # the sub-question letter, already allowed after the number
            else:
                parts.append(r"[A-Za-z]{1,6}")  # a prefix: "S", "KP", "CST"
        else:
            parts.append(re.escape(token))
    # A label ends the line or is followed by space: "S5c. How long…".
    return re.compile(r"^\s*(" + "".join(parts) + r")(?=\s|$)")


def label_in(text: str, shapes: list[re.Pattern]) -> tuple[str, str] | None:
    """(label, the rest of the line) when a taught shape opens this line.

    The label is the id a reviewer and the data use: "S5c." -> S5c,
    "Question 5:" -> Q5, "Q.5" -> Q5.
    """
    for shape in shapes:
        if m := shape.match(text or ""):
            words = m.group(1).split()
            raw = words[0][0].upper() + "".join(words[1:]) if len(words) > 1 else words[0]
            label = re.sub(r"[^A-Za-z0-9_]", "", raw)
            if label:
                return label, text[m.end():].strip()
    return None


def shapes_in_use(blocks, examples: list[str]) -> list[re.Pattern]:
    """The taught shapes this document actually writes its labels in."""
    used = []
    for example in examples:
        shape = shape_from_example(example)
        if shape and sum(1 for b in blocks
                         if b.block_type != "table" and shape.match(b.text or "")) >= MIN_MATCHES:
            used.append(shape)
    return used


def apply(blocks: list, examples: list[str]) -> tuple[list, int]:
    """The blocks with each taught label marked on the block it opens
    (DocumentBlock.label), and how many questions that is.

    A label alone on its line ("QGENDER.") names the line after it, and the
    label line itself is dropped. Blocks are not modified in place.
    """
    from dataclasses import replace

    shapes = shapes_in_use(blocks, examples)
    if not shapes:
        return blocks, 0
    out: list = []
    marked = 0
    pending: str | None = None
    for block in blocks:
        if block.block_type == "table" or block.label:
            pending = None
            out.append(block)
            continue
        if pending and (block.text or "").strip():
            out.append(replace(block, label=pending))
            pending, marked = None, marked + 1
            continue
        found = label_in(block.text, shapes)
        if not found:
            out.append(block)
            continue
        label, rest = found
        if rest:
            out.append(replace(block, label=label, text=rest))
            marked += 1
        else:
            pending = label
    return out, marked

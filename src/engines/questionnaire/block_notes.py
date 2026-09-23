"""Blocks a scripter note names, and the rotation it asks for.

    SCRIPTER NOTE: Please group the Google Ads questions and Meta Ads
    questions into two separate blocks.
    Block order: Rotate the presentation of the two blocks evenly (50% see
    Google Ads first, 50% see Meta Ads first).
    Internal order: Do NOT randomize the questions within the blocks. The
    internal question order must remain static/fixed.

    Google Ads Block of questions: GO_AI_GOOGLE, GA_GOOGLE_TOOL, ...
    Meta Ads Block of questions: GA_AI_META, GA_META_ADV, ...

This is not commentary. It is the STRUCTURE of the study, written where a
human would look for it, and every part of it changes what a respondent meets:

* The two blocks are the comparison the study exists to make. Read as one flat
  run of questions they are still asked, but the instrument has no notion that
  Google's eight and Meta's seven are parallel arms.
* The ROTATION is the part that decides whether the comparison is valid. Half
  the sample must see Google first and half Meta first, because whichever
  brand is asked about first is rated differently — order effects on brand
  batteries are the reason the instruction is there. Field it in fixed order
  and every respondent sees Google first, so the Google-versus-Meta difference
  that the study is FOR carries an order effect nobody can separate out
  afterwards. The data looks complete and the finding is unsound.
* "Do NOT randomize within the blocks" is the opposite instruction, and it is
  satisfiable: it means exactly what the default already does, and saying so
  is what stops someone "helpfully" switching randomisation on.

So the blocks are built, the internal order is pinned, and the rotation is
recorded and FLAGGED rather than quietly dropped: this platform has no
block-order rotation, and a study whose design needs one must know that before
a link opens, not after the fieldwork.
"""

from __future__ import annotations

import difflib
import re
from typing import Any

from src.core.survey_definition import Block, SurveyDefinition

#: "Google Ads Block of questions: GO_AI_GOOGLE, GA_GOOGLE_TOOL, ..."
#: The label is whatever precedes "Block of questions", trimmed to something a
#: reviewer would recognise on a page tab.
_BLOCK_LIST = re.compile(
    r"(?P<label>[A-Za-z0-9][A-Za-z0-9 &/'-]{0,40}?)\s*Block\s+of\s+questions\s*:\s*"
    r"(?P<ids>[A-Za-z0-9_]{2,40}(?:\s*,\s*[A-Za-z0-9_]{2,40})+)",
    re.I,
)
#: "Rotate the presentation of the two blocks evenly (50% ... 50% ...)"
_ROTATE = re.compile(
    r"\brotate\b[^.]{0,120}?\bblocks?\b[^.]{0,160}\.?|"
    r"\bblock\s+order\s*:\s*[^.]{0,200}\.",
    re.I,
)
#: "Do NOT randomize the questions within the blocks."
_NO_INTERNAL_RANDOM = re.compile(
    r"\bdo\s*not\s+randomi[sz]e\b[^.]{0,80}\bwithin\b[^.]{0,40}\bblocks?\b|"
    r"\binternal\s+question\s+order\s+must\s+remain\b",
    re.I,
)


def _documents_text(survey: SurveyDefinition) -> str:
    """Every fragment the document gave us, once."""
    seen: list[str] = []
    for question in survey.questions:
        source = question.source_text or ""
        if source:
            seen.append(source)
    return "\n".join(seen)


def read(text: str) -> dict[str, Any]:
    """What a scripter note states about blocks. Empty when it states none."""
    flat = " ".join((text or "").split())
    blocks: list[tuple[str, list[str]]] = []
    for match in _BLOCK_LIST.finditer(flat):
        label = match.group("label").strip(" -–,")
        ids = [q.strip().upper() for q in match.group("ids").split(",") if q.strip()]
        if len(ids) >= 2:
            blocks.append((label, ids))
    found: dict[str, Any] = {}
    if blocks:
        found["blocks"] = blocks
    rotate = _ROTATE.search(flat)
    if rotate and blocks:
        found["rotate_verbatim"] = rotate.group(0).strip()
    if _NO_INTERNAL_RANDOM.search(flat):
        found["fix_internal_order"] = True
    return found


def apply(survey: SurveyDefinition) -> dict[str, Any]:
    """Build the blocks a note names. Returns what was applied."""
    found = read(_documents_text(survey))
    named = found.get("blocks") or []
    if not named:
        return {}

    known = {q.qid.upper(): q.qid for q in survey.questions}
    built: list[Block] = []
    missing: list[str] = []
    claimed: set[str] = set()
    for label, ids in named:
        present = [known[i] for i in ids if i in known]
        missing.extend(i for i in ids if i not in known)
        if len(present) < 2:
            # A block naming questions this survey does not have is a note
            # about a different wave, or a typo. Either way, not a block.
            continue
        claimed.update(present)
        slug = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_") or str(len(built))
        built.append(
            Block(
                block_id=f"blk_{slug}",
                label=label,
                question_ids=present,
                # The note says fixed, and fixed is also the default — saying
                # so is what stops it being switched on later by accident.
                randomize_questions=False,
            )
        )
    if not built:
        return {}

    # Everything the note did NOT claim keeps its place, in document order,
    # around the named blocks. A question silently dropped from every block is
    # a question nobody is asked.
    order = [q.qid for q in survey.questions]
    first_claimed = order.index(built[0].question_ids[0])
    before = [q for i, q in enumerate(order) if q not in claimed and i < first_claimed]
    after = [q for q in order if q not in claimed and q not in before]

    blocks: list[Block] = []
    if before:
        blocks.append(Block(block_id="blk_main", label="", question_ids=before))
    blocks.extend(built)
    if after:
        blocks.append(Block(block_id="blk_rest", label="", question_ids=after))
    survey.blocks = blocks

    applied: dict[str, Any] = {
        "blocks": [{"label": b.label, "qids": b.question_ids} for b in built],
    }
    if missing:
        # The note said GO_AI_GOOGLE and the document's own Q Name is
        # GA_AI_GOOGLE — one character, in the instruction rather than in the
        # question. The block is then short by one and nothing says why.
        #
        # The near miss is SUGGESTED, never applied: a qid decides a data
        # column, and quietly moving a question into a block because the name
        # was nearly right is how a study ends up with an arm the analyst
        # cannot reproduce from the document.
        absent = sorted(set(missing))
        applied["named_but_absent"] = absent
        unclaimed = [qid for qid in known.values() if qid not in claimed]
        suggestions = {}
        for name in absent:
            close = difflib.get_close_matches(name, unclaimed, n=1, cutoff=0.8)
            if close:
                suggestions[name] = close[0]
        if suggestions:
            applied["did_you_mean"] = suggestions
    if found.get("fix_internal_order"):
        applied["internal_order_fixed"] = True
    if found.get("rotate_verbatim"):
        # Recorded, never pretended. Preflight refuses the link over it.
        applied["rotation_unsupported"] = found["rotate_verbatim"]
    survey.metadata["blocks_from_note"] = applied
    return applied

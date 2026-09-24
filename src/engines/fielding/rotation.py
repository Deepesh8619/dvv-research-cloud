"""Which block a respondent meets first, and the record that it happened.

    Block order: Rotate the presentation of the two blocks evenly
    (50% see Google Ads first, 50% see Meta Ads first).

The instruction exists because whichever brand is asked about first is rated
differently. Field the blocks in a fixed order and every respondent meets
Google first, so the Google-versus-Meta difference the study exists to measure
carries an order effect nobody can separate out afterwards — the data looks
complete and the finding is unsound.

Three things have to be true, and only the first is obvious:

* **Assigned, not shuffled.** A coin flip drifts: on 500 respondents a fair
  coin lands 54/46 often enough to matter. Each respondent takes the arm that
  is behind, so the split is balanced by construction rather than in
  expectation.
* **Rotated, not permuted.** "Rotate" means each block gets its turn at the
  front with the others following in document order — not every possible
  ordering. Two blocks give two arms; three give three, not six. That is what
  the instruction asks for and what an analyst will expect to find.
* **RECORDED.** The arm goes into the data as a variable. A rotation the
  analyst cannot see is nearly as bad as no rotation: they cannot control for
  order, and the thing the rotation was for is still unavailable to them.
"""

from __future__ import annotations

from typing import Any

#: The variable the arm is delivered as. Named like the hidden variables a
#: scripter would have written by hand, because that is what it is.
ROTATION_VARIABLE = "HID_BLOCK_ORDER"


def rotating_blocks(survey: Any) -> list[str]:
    """The block ids a scripter note asked to rotate, in document order.

    Empty when the document asked for no rotation — which is most studies, and
    they must compile exactly as they did before.
    """
    note = (getattr(survey, "metadata", None) or {}).get("blocks_from_note") or {}
    # `rotation` is what the note pass writes now. `rotation_unsupported` is
    # what it wrote while rotation was refused rather than applied, and studies
    # extracted then are still in the database — they rotate too, rather than
    # needing a re-upload to gain it.
    if not any(note.get(key) for key in ("rotation", "rotation_unsupported", "rotate_verbatim")):
        return []
    named = [b for b in (note.get("block_ids") or []) if b]
    return named if len(named) >= 2 else []


def arms(survey: Any) -> list[str]:
    """One arm per rotating block: the block that goes first."""
    return list(rotating_blocks(survey))


def assign(arm_counts: dict[str, int], available: list[str]) -> str:
    """The arm that is behind. Ties go to document order, so arm 1 leads.

    Least-fill rather than random: a fair coin on 500 respondents lands 54/46
    often enough to bias a brand comparison.
    """
    if not available:
        raise ValueError("no arms to assign")
    return min(available, key=lambda arm: (arm_counts.get(arm, 0), available.index(arm)))


def order_pages(compiled: dict[str, Any], first_block: str) -> dict[str, Any]:
    """The compiled model with `first_block`'s page moved to the front of its group.

    Only the rotating pages move, and only among themselves: everything before
    them and after them keeps its place, because a screener that runs after the
    brand blocks is not part of the rotation.
    """
    pages = list(compiled.get("pages") or [])
    note_ids = [p.get("name") for p in pages]
    if first_block not in note_ids:
        return compiled

    rotating = [p for p in pages if p.get("name") in _rotating_names(compiled)]
    if len(rotating) < 2:
        return compiled

    start = next(i for i, p in enumerate(rotating) if p.get("name") == first_block)
    reordered = rotating[start:] + rotating[:start]

    out, seen = [], iter(reordered)
    for page in pages:
        out.append(next(seen) if page.get("name") in _rotating_names(compiled) else page)
    return {**compiled, "pages": out}


def _rotating_names(compiled: dict[str, Any]) -> set[str]:
    """The page names that take part, carried on the compiled model."""
    return {str(name) for name in (compiled.get("rotatingPages") or [])}

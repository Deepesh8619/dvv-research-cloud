"""Which items each respondent sees on each MaxDiff screen.

Twelve screens of four items drawn from a list of twenty is twelve different
questions per person, and no two people see the same twelve. That subsetting is
not presentation — it IS the measurement. Utilities are recovered from which
items beat which, so an item shown more often than another, or a pair that
never meets, biases the result in a way no amount of sample fixes.

What has to hold:

* **Frequency balance** — every item appears the same number of times. An item
  seen twice as often as another accumulates more wins for that reason alone.
* **Pairwise balance** — every PAIR of items co-occurs about equally often. A
  pair that never shares a screen is a comparison the model has to infer
  entirely from other items.
* **No repeats** — an item cannot appear twice on one screen. It is a
  best/worst choice between distinct things.
* **Versions** — respondents are spread across several designs, assigned by
  least fill (see rotation.assign), so the balance holds across the sample and
  not only within one person's twelve screens.

A textbook balanced incomplete block design exists only for particular
(items, screens, per-screen) combinations, and a study picks its parameters for
research reasons rather than for combinatorial ones. So this builds a NEAR
balanced design by construction and then improves it by swapping — and reports
the balance it actually achieved rather than the balance it was aiming at.
`Balance.is_exact` says whether it hit textbook balance; nothing here claims it
did. A design that quietly fell short is the failure this whole area is prone
to: the output looks like data and the bias is invisible until someone reruns
the study.

Deterministic: same parameters and seed, same design, every time. A design that
differed between the preflight check and the live link would make the check
meaningless.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from itertools import combinations

#: Below this, a respondent sees an item too seldom for their own utilities to
#: mean much. Sawtooth's long-standing guidance, and the reason a design with
#: too few screens is worth warning about rather than silently generating.
MIN_APPEARANCES_PER_ITEM = 3
_IMPROVEMENT_PASSES = 400


@dataclass(frozen=True)
class Balance:
    """What the generated design actually achieved."""

    item_min: int
    item_max: int
    pair_min: int
    pair_max: int
    unpaired: int  # pairs that never share a screen — the worst failure
    # How often ONE respondent meets an item. Reported separately because the
    # two can disagree: totals even out across the sample while an individual
    # sees an item once and another four times, and individual-level utilities
    # are estimated from what that person saw.
    seen_min: int
    seen_max: int

    @property
    def is_exact(self) -> bool:
        """True only for textbook balance: every item and every pair equal."""
        return (
            self.item_min == self.item_max
            and self.pair_min == self.pair_max
            and self.unpaired == 0
        )

    def to_dict(self) -> dict[str, int | bool]:
        return {
            "item_min": self.item_min, "item_max": self.item_max,
            "pair_min": self.pair_min, "pair_max": self.pair_max,
            "unpaired": self.unpaired, "seen_min": self.seen_min,
            "seen_max": self.seen_max, "is_exact": self.is_exact,
        }


@dataclass(frozen=True)
class MaxDiffDesign:
    """Every version's screens, as item indices into the study's item list."""

    n_items: int
    items_per_screen: int
    versions: tuple[tuple[tuple[int, ...], ...], ...]
    balance: Balance

    @property
    def n_versions(self) -> int:
        return len(self.versions)

    @property
    def n_screens(self) -> int:
        return len(self.versions[0]) if self.versions else 0

    def screens_for(self, version: int) -> tuple[tuple[int, ...], ...]:
        """One respondent's screens. Raises rather than wrapping a bad index —
        a silently wrapped version would quietly overfill one arm."""
        if not 0 <= version < self.n_versions:
            raise IndexError(f"version {version} outside 0..{self.n_versions - 1}")
        return self.versions[version]

    def to_dict(self) -> dict:
        return {
            "n_items": self.n_items,
            "items_per_screen": self.items_per_screen,
            "versions": [[list(s) for s in v] for v in self.versions],
            "balance": self.balance.to_dict(),
        }


#: A problem that makes the design impossible, versus one that makes it weak.
FATAL, ADVISORY = "fatal", "advisory"


def check_parameters(
    n_items: int, n_screens: int, items_per_screen: int
) -> list[tuple[str, str]]:
    """(severity, why) for each problem with these parameters. Empty is fine.

    Two severities, because the two are genuinely different and conflating them
    refused a perfectly fieldable exercise: a screen that cannot be built is
    impossible, while a design too thin to read is a research judgement whose
    answer might legitimately be "we know, field it anyway".

    Returned rather than raised so preflight can show every problem at once,
    the way every other check in this codebase does.
    """
    problems: list[tuple[str, str]] = []
    if items_per_screen < 2:
        problems.append((FATAL, "a screen needs at least 2 items for a best/worst choice"))
    if n_items < items_per_screen:
        problems.append((
            FATAL,
            f"only {n_items} items for a screen of {items_per_screen} — a screen "
            "cannot show an item twice",
        ))
    if n_screens < 1:
        problems.append((FATAL, "a MaxDiff exercise needs at least one screen"))
    if problems:
        return problems

    per_item = n_screens * items_per_screen / n_items
    if per_item < MIN_APPEARANCES_PER_ITEM:
        problems.append((
            ADVISORY,
            f"each item is seen about {per_item:.1f} times per respondent; below "
            f"{MIN_APPEARANCES_PER_ITEM} the utilities are too thin to read. "
            f"{_screens_needed(n_items, items_per_screen)} screens would fix it",
        ))
    return problems


def _screens_needed(n_items: int, items_per_screen: int) -> int:
    return -(-MIN_APPEARANCES_PER_ITEM * n_items // items_per_screen)


def _pair_counts(screens: list[list[int]]) -> dict[tuple[int, int], int]:
    counts: dict[tuple[int, int], int] = {}
    for screen in screens:
        for pair in combinations(sorted(screen), 2):
            counts[pair] = counts.get(pair, 0) + 1
    return counts


def _deal(n_items: int, n_screens: int, items_per_screen: int, n_versions: int,
          rng: random.Random) -> list[list[int]]:
    """Every version's screens, dealt so balance holds at BOTH levels.

    Within a version, each item appears floor or ceil of the ideal — never once
    while another appears four times, which is what dealing one long pool gave:
    a respondent who meets an item a single time carries almost no information
    about it, however balanced the totals look across the sample.

    Across versions, the leftover slots go to whichever item has had fewest of
    them (the least-fill rule rotation.assign uses, for the same reason: a coin
    flip drifts). So the global count is even too, by construction rather than
    by the swapping that follows.
    """
    slots = n_screens * items_per_screen
    full_rounds, remainder = divmod(slots, n_items)
    extras_given = [0] * n_items
    screens: list[list[int]] = []

    for _ in range(n_versions):
        bag = [item for item in range(n_items) for _ in range(full_rounds)]
        # Fewest extras first; ties broken by item order so it stays deterministic.
        for item in sorted(range(n_items), key=lambda i: (extras_given[i], i))[:remainder]:
            bag.append(item)
            extras_given[item] += 1
        rng.shuffle(bag)
        screens.extend(
            bag[i * items_per_screen : (i + 1) * items_per_screen]
            for i in range(n_screens)
        )
    return screens


def _swap_is_legal(screens: list[list[int]], a: int, i: int, b: int, j: int) -> bool:
    """A swap may not put the same item on a screen twice."""
    return screens[a][i] not in screens[b] and screens[b][j] not in screens[a]


def _pair_delta(
    screens: list[list[int]], counts: dict[tuple[int, int], int],
    mean: float, a: int, i: int, b: int, j: int,
) -> float:
    """Change in squared pair-imbalance from one swap, without rescoring all of it.

    Only the pairs touching the two moved items change, so a full rebuild per
    candidate swap is wasted work — and it is the difference between a few
    hundred improvement attempts and enough of them to matter.
    """
    x, y = screens[a][i], screens[b][j]
    delta = 0.0
    for screen_idx, moving_out, moving_in, slot in ((a, x, y, i), (b, y, x, j)):
        for slot_idx, other in enumerate(screens[screen_idx]):
            if slot_idx == slot:
                continue
            lost = tuple(sorted((moving_out, other)))
            gained = tuple(sorted((moving_in, other)))
            c_lost = counts.get(lost, 0)
            delta += -2 * (c_lost - mean) + 1
            counts[lost] = c_lost - 1
            c_gained = counts.get(gained, 0)
            delta += 2 * (c_gained - mean) + 1
            counts[gained] = c_gained + 1
    return delta


def _undo(
    screens: list[list[int]], counts: dict[tuple[int, int], int],
    a: int, i: int, b: int, j: int,
) -> None:
    """Put the pair counts back. Mirrors _pair_delta exactly."""
    x, y = screens[a][i], screens[b][j]
    for screen_idx, moving_out, moving_in, slot in ((a, x, y, i), (b, y, x, j)):
        for slot_idx, other in enumerate(screens[screen_idx]):
            if slot_idx == slot:
                continue
            counts[tuple(sorted((moving_out, other)))] += 1
            counts[tuple(sorted((moving_in, other)))] -= 1


def _repair_duplicates(
    screens: list[list[int]], version_of: list[int], rng: random.Random
) -> None:
    """Trade a repeated item away, always WITHIN its own version.

    Confining the trade to one version keeps both that version's item counts and
    the global ones exactly as the deal left them.
    """
    for i, screen in enumerate(screens):
        while len(set(screen)) != len(screen):
            seen: set[int] = set()
            slot = next(s for s, it in enumerate(screen)
                        if it in seen or (seen.add(it) or False))
            item = screen[slot]
            partners = [
                k for k, other in enumerate(screens)
                if k != i and version_of[k] == version_of[i] and item not in other
            ]
            rng.shuffle(partners)
            for k in partners:
                take = next(
                    (t for t, cand in enumerate(screens[k]) if cand not in screen),
                    None,
                )
                if take is not None:
                    screens[i][slot], screens[k][take] = screens[k][take], item
                    break
            else:
                break  # nothing legal to trade with; Balance will report it


def _improve(
    screens: list[list[int]], version_of: list[int], n_items: int,
    rng: random.Random, attempts: int,
) -> None:
    """Swap items between screens of the SAME version to even out the pairs.

    Same version, so every version's item counts — and therefore the global
    ones — survive untouched, and only the pairing changes.
    """
    counts = _pair_counts(screens)
    total_pairs = n_items * (n_items - 1) // 2
    mean = sum(counts.values()) / total_pairs if total_pairs else 0.0
    by_version: dict[int, list[int]] = {}
    for idx, version in enumerate(version_of):
        by_version.setdefault(version, []).append(idx)
    per_screen = len(screens[0])

    for _ in range(attempts):
        pool = by_version[rng.choice(version_of)]
        if len(pool) < 2:
            continue
        a, b = rng.sample(pool, 2)
        i, j = rng.randrange(per_screen), rng.randrange(per_screen)
        if not _swap_is_legal(screens, a, i, b, j):
            continue
        delta = _pair_delta(screens, counts, mean, a, i, b, j)
        if delta < 0:
            screens[a][i], screens[b][j] = screens[b][j], screens[a][i]
        else:
            _undo(screens, counts, a, i, b, j)


def _measure(
    all_screens: list[list[int]], n_items: int, version_of: list[int]
) -> Balance:
    freq = [0] * n_items
    for screen in all_screens:
        for item in screen:
            freq[item] += 1
    counts = _pair_counts(all_screens)
    pair_values = [counts.get(p, 0) for p in combinations(range(n_items), 2)]

    per_version: dict[int, list[int]] = {}
    for idx, screen in enumerate(all_screens):
        seen = per_version.setdefault(version_of[idx], [0] * n_items)
        for item in screen:
            seen[item] += 1
    return Balance(
        item_min=min(freq), item_max=max(freq),
        pair_min=min(pair_values, default=0), pair_max=max(pair_values, default=0),
        unpaired=sum(1 for v in pair_values if v == 0),
        seen_min=min(min(v) for v in per_version.values()),
        seen_max=max(max(v) for v in per_version.values()),
    )


def build(
    n_items: int,
    n_screens: int,
    items_per_screen: int,
    n_versions: int = 10,
    seed: int = 20260927,
) -> MaxDiffDesign:
    """Generate the design. Raises on parameters that cannot produce screens."""
    fatal = [
        why for severity, why in check_parameters(n_items, n_screens, items_per_screen)
        if severity == FATAL
    ]
    if fatal:
        raise ValueError("; ".join(fatal))
    if n_versions < 1:
        raise ValueError("a design needs at least one version")

    rng = random.Random(seed)
    total = n_screens * n_versions
    screens = _deal(n_items, n_screens, items_per_screen, n_versions, rng)
    version_of = [i // n_screens for i in range(total)]
    _repair_duplicates(screens, version_of, rng)
    _improve(screens, version_of, n_items, rng, attempts=_IMPROVEMENT_PASSES * total)

    versions = tuple(
        tuple(tuple(s) for s in screens[v * n_screens : (v + 1) * n_screens])
        for v in range(n_versions)
    )
    return MaxDiffDesign(
        n_items=n_items,
        items_per_screen=items_per_screen,
        versions=versions,
        balance=_measure(screens, n_items, version_of),
    )

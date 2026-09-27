"""The design IS the measurement, so its properties are what must be asserted.

MaxDiff utilities are recovered from which items beat which. An item shown more
often than another accumulates wins for that reason alone, and a pair that never
shares a screen is a comparison the model has to infer entirely. Neither is
visible in the output: the file looks complete and the ranking is wrong.
"""

from collections import Counter
from itertools import combinations

import pytest

from src.engines.fielding.maxdiff_design import (
    MIN_APPEARANCES_PER_ITEM,
    build,
    check_parameters,
)

# 20 items, 12 screens of 4 — the shape a real MaxDiff exercise takes, and one
# where no textbook balanced design exists (48 slots do not divide 20 evenly).
_REAL = dict(n_items=20, n_screens=12, items_per_screen=4, n_versions=10)


def _screens(design):
    return [s for version in design.versions for s in version]


def test_no_item_appears_twice_on_one_screen():
    """It is a best/worst choice between distinct things."""
    for screen in _screens(build(**_REAL)):
        assert len(set(screen)) == len(screen)


def test_every_item_is_shown_the_same_number_of_times():
    design = build(**_REAL)
    counts = Counter(item for screen in _screens(design) for item in screen)
    assert set(counts) == set(range(20))
    # 12 x 4 x 10 = 480 slots over 20 items divides exactly.
    assert min(counts.values()) == max(counts.values()) == 24
    assert design.balance.item_min == design.balance.item_max


def test_every_pair_of_items_meets_at_least_once():
    """The worst failure this design can have, and the one to assert hardest.

    A pair that never shares a screen is not a weak estimate, it is no direct
    evidence at all about how those two compare.
    """
    design = build(**_REAL)
    counts = Counter(
        pair for screen in _screens(design) for pair in combinations(sorted(screen), 2)
    )
    missing = [p for p in combinations(range(20), 2) if counts[p] == 0]
    assert missing == []
    assert design.balance.unpaired == 0


def test_pairs_meet_about_equally_often():
    """Not exactly equal — no balanced design exists for these parameters — so
    the assertion is on the spread, which is what actually biases a utility."""
    design = build(**_REAL)
    assert design.balance.pair_max - design.balance.pair_min <= 3


def test_one_respondent_sees_each_item_about_equally_often():
    """Totals can even out across the sample while an individual sees an item
    once and another four times. Individual utilities come from what THAT
    person saw, so the per-respondent spread is its own requirement."""
    design = build(**_REAL)
    for version in design.versions:
        seen = Counter(item for screen in version for item in screen)
        counts = [seen.get(item, 0) for item in range(20)]
        # 48 slots over 20 items is 2.4 each: floor and ceil, nothing wider.
        assert min(counts) == 2
        assert max(counts) == 3
    assert (design.balance.seen_min, design.balance.seen_max) == (2, 3)


def test_the_same_parameters_always_give_the_same_design():
    """A design that differed between the preflight check and the live link
    would make the check meaningless."""
    assert build(**_REAL).to_dict() == build(**_REAL).to_dict()
    assert build(**_REAL).versions != build(**{**_REAL, "seed": 7}).versions


def test_balance_never_claims_to_be_exact_when_it_is_not():
    """The honest half. A design that quietly fell short of balance is the
    failure this whole area is prone to — invisible until someone reruns it."""
    assert build(**_REAL).balance.is_exact is False
    # A case where textbook balance IS reachable: 4 items, 3 per screen.
    tidy = build(n_items=4, n_screens=4, items_per_screen=3, n_versions=1)
    assert tidy.balance.is_exact is True


def test_versions_are_separate_designs():
    design = build(**_REAL)
    assert design.n_versions == 10
    assert design.n_screens == 12
    assert len({v for v in design.versions}) > 1  # not ten copies of one design
    with pytest.raises(IndexError):
        design.screens_for(10)


def test_parameters_that_cannot_make_a_screen_are_refused():
    assert [s for s, _ in check_parameters(3, 10, 4)] == ["fatal"]
    assert [s for s, _ in check_parameters(20, 10, 1)] == ["fatal"]
    with pytest.raises(ValueError, match="cannot show an item twice"):
        build(n_items=3, n_screens=10, items_per_screen=4)


def test_a_design_too_thin_to_read_is_warned_about_but_still_built():
    """Too few screens is a research judgement, not an impossibility — so it is
    reported with the number that would fix it, and still generated."""
    problems = check_parameters(n_items=20, n_screens=4, items_per_screen=4)
    # ADVISORY, not fatal: how many screens a study can ask of a respondent is
    # a research judgement, and refusing the link over it would overrule the
    # person who wrote the questionnaire.
    assert [s for s, _ in problems] == ["advisory"]
    assert any(str(MIN_APPEARANCES_PER_ITEM) in why for _, why in problems)
    assert any("15 screens would fix it" in why for _, why in problems)
    assert build(n_items=20, n_screens=4, items_per_screen=4).n_screens == 4


def test_a_healthy_design_reports_no_problems():
    assert check_parameters(n_items=20, n_screens=15, items_per_screen=4) == []

"""Sig-test math validated against hand-computed textbook values.

Statistical correctness is the reputation (architecture §10.2) — these numbers
were computed independently by hand, not copied from the implementation.
"""

import numpy as np
import pytest

from src.engines.tabs.stats import col_prop_ztest, effective_base, z_critical


def test_ztest_matches_hand_computed_value():
    # p1=0.50 (n=100) vs p2=0.30 (n=100): pooled p=0.40,
    # se = sqrt(0.4*0.6*(1/100+1/100)) = 0.069282, z = 0.20/0.069282 = 2.8868
    z = col_prop_ztest(0.50, 0.30, 100, 100)
    assert z == pytest.approx(2.8868, abs=1e-4)


def test_ztest_symmetry_and_null():
    assert col_prop_ztest(0.3, 0.5, 100, 100) == pytest.approx(-2.8868, abs=1e-4)
    assert col_prop_ztest(0.4, 0.4, 200, 150) == pytest.approx(0.0)


def test_ztest_unequal_bases():
    # p1=0.60 (n=150) vs p2=0.50 (n=250): pooled p = (90+125)/400 = 0.5375
    # se = sqrt(0.5375*0.4625*(1/150+1/250)) = 0.0514947, z = 0.10/0.0514947 = 1.9419
    z = col_prop_ztest(0.60, 0.50, 150, 250)
    assert z == pytest.approx(1.9419, abs=1e-4)


def test_effective_base_unweighted_equals_n():
    assert effective_base(np.ones(500)) == pytest.approx(500.0)


def test_effective_base_shrinks_with_weight_variance():
    # w = [2,2,1,1]: (6^2)/(4+4+1+1) = 36/10 = 3.6 < 4
    assert effective_base(np.array([2.0, 2.0, 1.0, 1.0])) == pytest.approx(3.6)


def test_z_critical_95():
    assert z_critical(0.95) == pytest.approx(1.959964, abs=1e-5)

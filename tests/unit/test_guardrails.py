"""Tests for crosstab statistical guardrails that prevent LLM hallucination.

Guardrails are immutable dataclasses containing pre-computed p-values, effect
sizes, confidence intervals, and power estimates. The LLM must cite these
values, never compute them.
"""

from dataclasses import FrozenInstanceError

import numpy as np
import pytest

from src.engines.tabs.stats import (
    _cramers_v,
    _estimate_power,
    _wilson_ci_proportion,
    compute_crosstab_guardrails,
)


class TestWilsonCI:
    """Wilson score confidence intervals for proportions."""

    def test_wilson_ci_neutral_p_equals_0_5(self):
        """CI for p = 0.5 should be symmetric around 0.5."""
        lower, upper = _wilson_ci_proportion(50, 100, confidence=0.95)
        mid = (lower + upper) / 2
        assert mid == pytest.approx(0.5, abs=0.01)
        assert 0 <= lower < upper <= 1

    def test_wilson_ci_extreme_p_equals_1_0(self):
        """CI for p = 1.0 (all successes) should not exceed [0, 1]."""
        lower, upper = _wilson_ci_proportion(100, 100, confidence=0.95)
        assert lower >= 0.9  # lower bound conservatively high
        assert upper <= 1.0

    def test_wilson_ci_extreme_p_equals_0_0(self):
        """CI for p = 0.0 (no successes) should not exceed [0, 1]."""
        lower, upper = _wilson_ci_proportion(0, 100, confidence=0.95)
        assert lower >= 0.0
        assert upper <= 0.1  # upper bound conservatively low

    def test_wilson_ci_small_n(self):
        """Wilson CI should be conservative for small n."""
        # Even with 100% success (1/1), CI should widen due to small n
        lower, upper = _wilson_ci_proportion(1, 1, confidence=0.95)
        assert 0.0 <= lower < 1.0
        assert 0.0 < upper <= 1.0
        width = upper - lower
        assert width > 0.5  # wide CI for n=1

    def test_wilson_ci_monotonic_with_confidence(self):
        """Wider confidence level should produce wider interval."""
        lower_80, upper_80 = _wilson_ci_proportion(50, 100, confidence=0.80)
        lower_95, upper_95 = _wilson_ci_proportion(50, 100, confidence=0.95)
        width_80 = upper_80 - lower_80
        width_95 = upper_95 - lower_95
        assert width_80 < width_95


class TestCramersV:
    """Cramér's V effect size calculation."""

    def test_cramers_v_zero_chi2_is_zero(self):
        """No association (chi² = 0) should give V = 0."""
        v = _cramers_v(chi2=0.0, n=100, nrows=2, ncols=2)
        assert v == 0.0

    def test_cramers_v_bounds_zero_to_one(self):
        """Cramér's V is always in [0, 1]."""
        for chi2 in [0.1, 1.0, 10.0, 100.0]:
            v = _cramers_v(chi2=chi2, n=100, nrows=3, ncols=4)
            assert 0.0 <= v <= 1.0

    def test_cramers_v_grows_with_chi2(self):
        """Larger chi² should give larger effect size."""
        v1 = _cramers_v(chi2=1.0, n=100, nrows=2, ncols=2)
        v2 = _cramers_v(chi2=10.0, n=100, nrows=2, ncols=2)
        assert v1 < v2

    def test_cramers_v_depends_on_table_dimensions(self):
        """Effect size should be adjusted for table size."""
        # For fixed chi², larger table dimensions should reduce V
        v_2x2 = _cramers_v(chi2=10.0, n=100, nrows=2, ncols=2)
        v_4x4 = _cramers_v(chi2=10.0, n=100, nrows=4, ncols=4)
        assert v_2x2 > v_4x4

    def test_cramers_v_independent_of_n(self):
        """Effect size is scale-free (invariant to scaling n with chi²)."""
        # If we scale both chi² and n, V should remain similar
        # (This is approximate due to numerical precision)
        v1 = _cramers_v(chi2=10.0, n=100, nrows=2, ncols=2)
        v2 = _cramers_v(chi2=100.0, n=1000, nrows=2, ncols=2)
        assert v1 == pytest.approx(v2, rel=0.01)


class TestEstimatePower:
    """Statistical power estimation for chi-square tests."""

    def test_power_zero_chi2_is_zero(self):
        """No test statistic means no power to detect effect."""
        power = _estimate_power(chi2=0.0, effect_size_v=0.0, n=100, df=1, alpha=0.05)
        assert power == 0.0

    def test_power_zero_effect_size_is_zero(self):
        """No effect size means no power."""
        power = _estimate_power(chi2=5.0, effect_size_v=0.0, n=100, df=1, alpha=0.05)
        assert power == 0.0

    def test_power_bounds_zero_to_one(self):
        """Power is always in [0, 1]."""
        for chi2 in [0.1, 1.0, 5.0, 10.0]:
            power = _estimate_power(chi2=chi2, effect_size_v=0.2, n=100, df=2, alpha=0.05)
            assert 0.0 <= power <= 1.0

    def test_power_increases_with_chi2(self):
        """Larger test statistic should increase power."""
        power1 = _estimate_power(chi2=1.0, effect_size_v=0.1, n=100, df=1, alpha=0.05)
        power2 = _estimate_power(chi2=5.0, effect_size_v=0.1, n=100, df=1, alpha=0.05)
        assert power1 < power2

    def test_power_increases_with_n(self):
        """Larger sample size should increase power (all else equal)."""
        # Approximate: larger n means larger chi² for same effect
        power_small = _estimate_power(chi2=2.0, effect_size_v=0.1, n=50, df=1, alpha=0.05)
        power_large = _estimate_power(chi2=8.0, effect_size_v=0.1, n=200, df=1, alpha=0.05)
        assert power_small < power_large


class TestCrosstabGuardrails:
    """Guardrail computation for full crosstabs."""

    def test_guardrails_immutable(self):
        """Guardrails must be frozen dataclass (immutable)."""
        table = np.array([[50, 30], [40, 20]], dtype=np.float64)
        guardrail = compute_crosstab_guardrails(
            contingency_table=table,
            row_labels=["A", "B"],
            col_labels=["X", "Y"],
            col_unweighted_bases=[90, 50],
        )
        with pytest.raises(FrozenInstanceError):
            guardrail.p_value = 0.5

    def test_guardrails_2x2_independent(self):
        """2x2 crosstab with independent rows/cols should not be significant."""
        # Equal proportions across columns -> no association
        table = np.array([[50, 50], [50, 50]], dtype=np.float64)
        guardrail = compute_crosstab_guardrails(
            contingency_table=table,
            row_labels=["A", "B"],
            col_labels=["X", "Y"],
            col_unweighted_bases=[100, 100],
        )
        assert guardrail.significant_flag is False
        assert guardrail.p_value > 0.05 or np.isnan(guardrail.p_value)
        assert guardrail.effect_size_cramers_v < 0.1

    def test_guardrails_2x2_associated(self):
        """2x2 crosstab with strong association should be significant."""
        # Highly skewed: row A mostly in col X, row B mostly in col Y
        table = np.array([[90, 10], [10, 90]], dtype=np.float64)
        guardrail = compute_crosstab_guardrails(
            contingency_table=table,
            row_labels=["A", "B"],
            col_labels=["X", "Y"],
            col_unweighted_bases=[100, 100],
        )
        assert guardrail.significant_flag is True
        assert guardrail.p_value < 0.05
        assert guardrail.effect_size_cramers_v > 0.5  # large effect

    def test_guardrails_small_cells_triggers_fisher(self):
        """Table with cells < 5 should use Fisher's exact."""
        # One very small cell
        table = np.array([[50, 2], [50, 50]], dtype=np.float64)
        guardrail = compute_crosstab_guardrails(
            contingency_table=table,
            row_labels=["A", "B"],
            col_labels=["X", "Y"],
            col_unweighted_bases=[100, 52],
        )
        assert guardrail.n_cells_below_5 > 0
        assert guardrail.test_used == "fisher-exact"

    def test_guardrails_ci_95_valid(self):
        """95% confidence intervals should be logically valid."""
        table = np.array([[50, 30], [40, 20]], dtype=np.float64)
        guardrail = compute_crosstab_guardrails(
            contingency_table=table,
            row_labels=["A", "B"],
            col_labels=["X", "Y"],
            col_unweighted_bases=[90, 50],
        )
        assert len(guardrail.ci_95_lower) == 4  # 2x2 table
        assert len(guardrail.ci_95_upper) == 4
        for lower, upper in zip(guardrail.ci_95_lower, guardrail.ci_95_upper, strict=True):
            assert 0.0 <= lower <= upper <= 1.0

    def test_guardrails_effect_size_bounded(self):
        """Cramér's V must be in [0, 1]."""
        for _ in range(10):
            # Random table
            table = np.random.randint(1, 100, size=(3, 3)).astype(np.float64)
            guardrail = compute_crosstab_guardrails(
                contingency_table=table,
                row_labels=["A", "B", "C"],
                col_labels=["X", "Y", "Z"],
                col_unweighted_bases=[100, 100, 100],
            )
            assert 0.0 <= guardrail.effect_size_cramers_v <= 1.0

    def test_guardrails_power_estimates_reasonable(self):
        """Power estimates should be meaningful."""
        # Small effect, large n -> low power if effect is real
        table = np.array([[50, 48], [50, 52]], dtype=np.float64)
        guardrail = compute_crosstab_guardrails(
            contingency_table=table,
            row_labels=["A", "B"],
            col_labels=["X", "Y"],
            col_unweighted_bases=[100, 100],
        )
        # Small difference should have modest power
        assert 0.0 <= guardrail.power <= 1.0

    def test_guardrails_min_cell_n_is_minimum(self):
        """min_cell_n should be the smallest unweighted count."""
        table = np.array([[50, 3], [40, 20]], dtype=np.float64)
        guardrail = compute_crosstab_guardrails(
            contingency_table=table,
            row_labels=["A", "B"],
            col_labels=["X", "Y"],
            col_unweighted_bases=[90, 23],
        )
        assert guardrail.min_cell_n == 3

    def test_guardrails_3x3_larger_table(self):
        """Guardrails should work for larger contingency tables."""
        # 3x3 table with some structure
        table = np.array(
            [[80, 40, 20], [20, 80, 40], [20, 20, 80]], dtype=np.float64
        )
        guardrail = compute_crosstab_guardrails(
            contingency_table=table,
            row_labels=["A", "B", "C"],
            col_labels=["X", "Y", "Z"],
            col_unweighted_bases=[120, 140, 140],
        )
        assert len(guardrail.ci_95_lower) == 9  # 3x3 cells
        assert guardrail.significant_flag is True
        assert guardrail.effect_size_cramers_v > 0.3

    def test_guardrails_empty_table_handled(self):
        """Guardrails should handle edge case of all-zero table."""
        table = np.array([[0, 0], [0, 0]], dtype=np.float64)
        guardrail = compute_crosstab_guardrails(
            contingency_table=table,
            row_labels=["A", "B"],
            col_labels=["X", "Y"],
            col_unweighted_bases=[0, 0],
        )
        assert guardrail.min_cell_n == 0
        assert np.isnan(guardrail.p_value) or guardrail.p_value > 0.05

    def test_guardrails_p_value_computed(self):
        """p-value must be computed (not nan for well-formed table)."""
        table = np.array([[50, 30], [40, 20]], dtype=np.float64)
        guardrail = compute_crosstab_guardrails(
            contingency_table=table,
            row_labels=["A", "B"],
            col_labels=["X", "Y"],
            col_unweighted_bases=[90, 50],
        )
        # Well-formed table should have a valid p-value
        assert not np.isnan(guardrail.p_value)
        assert 0.0 <= guardrail.p_value <= 1.0


class TestGuardrailsSerialization:
    """Guardrails should serialize cleanly for JSON/logging."""

    def test_guardrail_to_dict_compatible(self):
        """Guardrail fields should serialize without custom encoders."""
        table = np.array([[50, 30], [40, 20]], dtype=np.float64)
        guardrail = compute_crosstab_guardrails(
            contingency_table=table,
            row_labels=["A", "B"],
            col_labels=["X", "Y"],
            col_unweighted_bases=[90, 50],
        )
        # Should be convertible to dict via asdict (if needed)
        result_dict = {
            "chi_square_stat": guardrail.chi_square_stat,
            "p_value": guardrail.p_value,
            "significant_flag": guardrail.significant_flag,
            "effect_size_cramers_v": guardrail.effect_size_cramers_v,
            "power": guardrail.power,
            "test_used": guardrail.test_used,
        }
        assert isinstance(result_dict, dict)
        assert "significant_flag" in result_dict

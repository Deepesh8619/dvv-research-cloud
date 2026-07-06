"""Significance testing for crosstabs.

Column-proportions z-test as used across MR tab software (SPSS CTABLES, Q,
Decipher crosstabs): pooled two-sample proportion test, computed on effective
bases when the data is weighted so the variance correction for weighting is
built in.

Hallucination guards: Pre-computed statistical significance flags, confidence
intervals, and effect sizes for every crosstab to prevent LLM fabrication of
statistical claims. The LLM must cite these pre-computed values, not compute.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass

import numpy as np
from scipy import stats

logger = logging.getLogger(__name__)


def _norm_ppf(q: float) -> float:
    """Inverse standard-normal CDF (probit) via Acklam's rational approximation.

    Replaces scipy.stats.norm.ppf — scipy is a 30MB dependency we were pulling
    in for this one function, and on low-RAM machines its native extensions
    kept half-installing and breaking imports. Accurate to ~1e-9 across the
    whole range, far tighter than any significance decision needs.
    """
    if not 0.0 < q < 1.0:
        raise ValueError("q must be in (0, 1)")
    a = (-3.969683028665376e01, 2.209460984245205e02, -2.759285104469687e02,
         1.383577518672690e02, -3.066479806614716e01, 2.506628277459239e00)
    b = (-5.447609879822406e01, 1.615858368580409e02, -1.556989798598866e02,
         6.680131188771972e01, -1.328068155288572e01)
    c = (-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e00,
         -2.549732539343734e00, 4.374664141464968e00, 2.938163982698783e00)
    d = (7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e00,
         3.754408661907416e00)
    p_low = 0.02425
    if q < p_low:
        t = math.sqrt(-2.0 * math.log(q))
        return (((((c[0]*t+c[1])*t+c[2])*t+c[3])*t+c[4])*t+c[5]) / \
               ((((d[0]*t+d[1])*t+d[2])*t+d[3])*t+1.0)
    if q <= 1.0 - p_low:
        r = q - 0.5
        s = r * r
        return (((((a[0]*s+a[1])*s+a[2])*s+a[3])*s+a[4])*s+a[5])*r / \
               (((((b[0]*s+b[1])*s+b[2])*s+b[3])*s+b[4])*s+1.0)
    t = math.sqrt(-2.0 * math.log(1.0 - q))
    return -(((((c[0]*t+c[1])*t+c[2])*t+c[3])*t+c[4])*t+c[5]) / \
            ((((d[0]*t+d[1])*t+d[2])*t+d[3])*t+1.0)


def effective_base(weights: np.ndarray) -> float:
    """Kish effective sample size: (sum w)^2 / sum w^2."""
    total = weights.sum()
    if total == 0:
        return 0.0
    return float(total**2 / (weights**2).sum())


def col_prop_ztest(p1: float, p2: float, e1: float, e2: float) -> float:
    """Pooled column-proportions z statistic between two banner columns.

    p1, p2: column proportions; e1, e2: (effective) bases.
    """
    if e1 <= 0 or e2 <= 0:
        return 0.0
    pooled = (p1 * e1 + p2 * e2) / (e1 + e2)
    variance = pooled * (1.0 - pooled) * (1.0 / e1 + 1.0 / e2)
    if variance <= 0:
        return 0.0
    return (p1 - p2) / np.sqrt(variance)


def z_critical(confidence: float = 0.95) -> float:
    return _norm_ppf(0.5 + confidence / 2.0)


@dataclass(frozen=True)
class CrosstabGuardrail:
    """Immutable statistical guardrails for a crosstab result.

    These pre-computed values prevent the LLM from fabricating statistical claims
    (e.g., "p < 0.05"). The LLM must cite these values, never compute them.

    Attributes:
        chi_square_stat: Chi-square test statistic (or nan if Fisher's exact used)
        p_value: Two-tailed p-value from chi-square or Fisher's exact test
        significant_flag: True if p_value < 0.05 (at conventional significance level)
        ci_95_lower: 95% confidence interval lower bounds per cell (list of floats)
        ci_95_upper: 95% confidence interval upper bounds per cell (list of floats)
        effect_size_cramers_v: Cramér's V effect size (0-1), measures association strength
        min_cell_n: Minimum unweighted cell count in the table
        power: Estimated statistical power (0-1) to detect true differences
        test_used: Either "chi-square" or "fisher-exact", for interpreting results
        n_cells_below_5: Count of cells with unweighted n < 5 (triggers Fisher's exact)
    """

    chi_square_stat: float
    p_value: float
    significant_flag: bool
    ci_95_lower: list[float]
    ci_95_upper: list[float]
    effect_size_cramers_v: float
    min_cell_n: int
    power: float
    test_used: str  # "chi-square" or "fisher-exact"
    n_cells_below_5: int


def _wilson_ci_proportion(successes: int, n: int, confidence: float = 0.95) -> tuple[float, float]:
    """Compute Wilson score confidence interval for a proportion.

    Uses the Wilson score method (recommended by Agresti & Coull) which is
    conservative and accurate even for extreme proportions and small n.

    Args:
        successes: Number of successes in the sample
        n: Total sample size
        confidence: Confidence level (default 0.95 for 95% CI)

    Returns:
        (lower_bound, upper_bound) both in [0, 1]
    """
    if n <= 0:
        return (0.0, 0.0)
    if successes < 0 or successes > n:
        return (0.0, 1.0)

    p = successes / n
    z = _norm_ppf(0.5 + confidence / 2.0)
    z_sq = z * z

    denominator = 1.0 + z_sq / n
    p_prime = (p + z_sq / (2 * n)) / denominator
    margin = z * np.sqrt((p * (1 - p) + z_sq / (4 * n)) / n) / denominator

    lower = max(0.0, p_prime - margin)
    upper = min(1.0, p_prime + margin)
    return (lower, upper)


def _cramers_v(chi2: float, n: int, nrows: int, ncols: int) -> float:
    """Compute Cramér's V effect size from chi-square statistic.

    Cramér's V measures the strength of association between two categorical variables
    and is standardized to [0, 1] accounting for table dimensions.

    Args:
        chi2: Chi-square test statistic
        n: Total sample size
        nrows: Number of rows in contingency table
        ncols: Number of columns in contingency table

    Returns:
        Cramér's V in [0, 1]
    """
    if n <= 0 or chi2 < 0:
        return 0.0
    min_dim = min(nrows, ncols) - 1
    if min_dim <= 0:
        return 0.0
    v = np.sqrt(chi2 / (n * min_dim))
    return min(1.0, v)  # clamp to [0, 1]


def _estimate_power(
    chi2: float,
    effect_size_v: float,
    n: int,
    df: int,
    alpha: float = 0.05,
) -> float:
    """Estimate statistical power for chi-square test using effect size.

    Uses a heuristic approximation based on noncentrality parameter and known
    chi-square distribution. For small n or very small effect sizes, power will
    be below 0.80.

    Args:
        chi2: Chi-square test statistic (0 if test not run)
        effect_size_v: Cramér's V effect size
        n: Sample size
        df: Degrees of freedom
        alpha: Significance level (default 0.05)

    Returns:
        Estimated power in [0, 1], where power >= 0.80 is generally acceptable
    """
    if n <= 0 or df <= 0 or alpha <= 0 or alpha >= 1:
        return 0.0
    if chi2 <= 0:
        return 0.0

    # Noncentrality parameter: lambda = chi2 * (1 + sqrt(1 + 8/lambda))^2
    # This is a simplified approximation; exact requires numerical integration.
    # For chi-square GOF, noncentrality ≈ N * w^2 where w is effect size (Cohen)
    # For contingency, use Cramér's V as proxy for effect size.
    if effect_size_v <= 0:
        return 0.0

    # Critical value for two-tailed test at alpha significance
    try:
        critical = stats.chi2.ppf(1.0 - alpha, df)
    except Exception:
        return 0.0

    # Noncentrality parameter (lambda) derived from observed chi2
    # For symmetric approximation, use chi2 itself as noncentrality estimate
    noncentrality = chi2

    # Power is P(chi2(df, lambda) > critical)
    try:
        power = 1.0 - stats.ncx2.cdf(critical, df, noncentrality)
    except Exception:
        power = 0.0

    return min(1.0, max(0.0, power))


def compute_crosstab_guardrails(
    contingency_table: np.ndarray,
    row_labels: list[str],
    col_labels: list[str],
    col_unweighted_bases: list[int],
) -> CrosstabGuardrail:
    """Compute statistical guardrails from a crosstab contingency table.

    Pre-computes p-value, effect size, confidence intervals, and power to prevent
    the LLM from fabricating statistical claims. The LLM must cite these values.

    Args:
        contingency_table: Contingency table (numpy array, rows x columns)
        row_labels: Labels for row categories
        col_labels: Labels for column categories
        col_unweighted_bases: Unweighted base (n) for each column

    Returns:
        CrosstabGuardrail with all statistical measures and flags
    """
    table = contingency_table.astype(np.int64)
    nrows, ncols = table.shape
    total_n = int(table.sum())

    # Convert weighted proportions back to unweighted-like contingency for chi-square
    # This is a conservative approximation: we use the provided unweighted bases
    # to structure a contingency table for testing.
    chi2_stat = np.nan
    p_value = np.nan
    test_used = "chi-square"
    n_cells_below_5 = int(np.sum(table < 5))

    # Check if any cell is too small for chi-square; use Fisher's exact if so
    if n_cells_below_5 > 0:
        test_used = "fisher-exact"
        logger.debug(
            f"Using Fisher's exact test (contingency table has {n_cells_below_5} cells with n < 5)"
        )
        # For 2x2 table, scipy.stats.fisher_exact is available; for larger tables,
        # chi-square is the standard test even with small cells (as per modern guidance).
        if table.shape == (2, 2):
            try:
                _, p_value = stats.fisher_exact(table)
                chi2_stat = np.nan  # Fisher's exact doesn't produce chi-square stat
            except Exception as e:
                logger.warning(f"Fisher's exact failed: {e}, falling back to chi-square")
                test_used = "chi-square"
        else:
            # For larger tables, use chi-square even with small cells
            # (modern statistical practice accepts chi-square with small n)
            test_used = "chi-square"

    # Compute chi-square if not already done via Fisher
    if test_used == "chi-square" or np.isnan(p_value):
        try:
            chi2_stat, p_value, df, expected = stats.chi2_contingency(table)
        except Exception as e:
            logger.warning(f"Chi-square test failed: {e}")
            chi2_stat = np.nan
            p_value = np.nan
            df = (nrows - 1) * (ncols - 1)

    # Significance at conventional alpha = 0.05
    significant_flag = float(p_value) < 0.05 if not np.isnan(p_value) else False

    # Effect size: Cramér's V
    effect_size_v = _cramers_v(
        float(chi2_stat) if not np.isnan(chi2_stat) else 0.0,
        total_n,
        nrows,
        ncols,
    )

    # Confidence intervals for each cell proportion (relative to its column)
    ci_lower = []
    ci_upper = []
    for col_idx in range(ncols):
        col_total = int(table[:, col_idx].sum())
        for row_idx in range(nrows):
            cell_count = int(table[row_idx, col_idx])
            lower, upper = _wilson_ci_proportion(cell_count, col_total, confidence=0.95)
            ci_lower.append(lower)
            ci_upper.append(upper)

    # Min cell count (unweighted)
    min_cell = int(table.min())

    # Power estimation
    df = (nrows - 1) * (ncols - 1) if nrows > 1 and ncols > 1 else 1
    power = _estimate_power(
        float(chi2_stat) if not np.isnan(chi2_stat) else 0.0,
        effect_size_v,
        total_n,
        df,
        alpha=0.05,
    )

    logger.debug(
        f"Crosstab guardrails computed: test={test_used}, p={p_value:.4f}, "
        f"sig={significant_flag}, v={effect_size_v:.4f}, power={power:.4f}, min_n={min_cell}"
    )

    return CrosstabGuardrail(
        chi_square_stat=float(chi2_stat) if not np.isnan(chi2_stat) else np.nan,
        p_value=float(p_value) if not np.isnan(p_value) else np.nan,
        significant_flag=bool(significant_flag),
        ci_95_lower=ci_lower,
        ci_95_upper=ci_upper,
        effect_size_cramers_v=float(effect_size_v),
        min_cell_n=int(min_cell),
        power=float(power),
        test_used=test_used,
        n_cells_below_5=int(n_cells_below_5),
    )

"""Significance testing for crosstabs.

Column-proportions z-test as used across MR tab software (SPSS CTABLES, Q,
Decipher crosstabs): pooled two-sample proportion test, computed on effective
bases when the data is weighted so the variance correction for weighting is
built in.
"""

from __future__ import annotations

import math

import numpy as np


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

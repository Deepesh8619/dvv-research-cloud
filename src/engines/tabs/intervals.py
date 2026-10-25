"""Wilson score interval for a percentage, on the effective base."""

from math import sqrt
from statistics import NormalDist


def interval(pct: float, n_eff: float, level: float = 0.95) -> tuple[float, float] | None:
    """(lo, hi) in percent, or None when the effective base is under 1."""
    if not n_eff or n_eff < 1:
        return None
    z = NormalDist().inv_cdf(0.5 + level / 2)
    p = min(max(pct / 100, 0.0), 1.0)
    d = 1 + z * z / n_eff
    centre = (p + z * z / (2 * n_eff)) / d
    half = z * sqrt(p * (1 - p) / n_eff + z * z / (4 * n_eff * n_eff)) / d
    return (max(0.0, centre - half) * 100, min(1.0, centre + half) * 100)


def interval_text(pct: float, n_eff: float) -> str | None:
    """"28%–40%", whole percents; None if there is no interval."""
    iv = interval(pct, n_eff)
    return None if iv is None else f"{iv[0]:.0f}%–{iv[1]:.0f}%"


def differs(p1: float, p2: float, n1: float, n2: float | None = None,
            level: float = 0.95) -> bool | None:
    """Whether two percentages differ at `level`, on effective bases.

    Overlapping intervals do NOT mean no difference (59% and 26% of 34 people
    overlap at 42% yet differ clearly), so this tests the difference itself.
    `n2` None: two answers from the SAME respondents (rows of one column), whose
    shares are negatively correlated: var = (p1 + p2 - (p1 - p2)^2) / n.
    Otherwise two independent groups (two columns). None without a usable base.
    """
    a, b = p1 / 100, p2 / 100
    if not n1 or n1 < 1 or (n2 is not None and (not n2 or n2 < 1)):
        return None
    var = (a + b - (a - b) ** 2) / n1 if n2 is None else a * (1 - a) / n1 + b * (1 - b) / n2
    if var <= 0:
        return a != b
    return abs(a - b) / sqrt(var) >= NormalDist().inv_cdf(0.5 + level / 2)

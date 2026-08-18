"""
lab/analysis/cointegration.py
=============================
Is this pair's relationship real, or an artefact of both series drifting upward
for three years?

Two stocks that trend together will produce a high R² and a confident
t-statistic whether or not anything connects them — run OLS on two independent
random walks and you get exactly that. This is the spurious-regression problem,
and cointegration is the test that separates a genuine long-run equilibrium
from the illusion.

Ported from the cointegration explorer. The two things worth carrying forward:

**Step two is not optional.** Engle-Granger has two steps. Step one regresses
y on x and asks whether the residuals are stationary — that tells you a pair is
*out* of equilibrium. Step two fits an error-correction model and estimates α,
the speed at which it comes *back*. Without α you cannot tell whether a spread
reverts this month or this decade, and a cointegrated pair with α ≈ 0 is
statistically significant and completely untradeable. A generated implementation
of this procedure silently omitted step two; checking it against Engle & Granger
(1987) is how that was caught.

**Residuals need MacKinnon's table.** Step one's residuals come from a
regression fitted to minimise their own variance, so they look more stationary
than observed data does, and the standard Dickey-Fuller table over-rejects.
`statsmodels.coint()` implements the residual-based critical values;
`adfuller()` does not. Measured on 561 real pairs, changing nothing but the
table: 46.0% "cointegrated" became 27.1%. Same regression, same residuals,
wrong table.

`use_residual_critical_values=False` restores the wrong behaviour, so the
difference stays measurable rather than described.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass
from typing import Callable

import numpy as np

__all__ = ["CointegrationResult", "PairScan", "engle_granger", "prefilter",
           "hurst", "variance_ratio", "scan_pairs"]


@dataclass(frozen=True)
class CointegrationResult:
    """Everything the two-step procedure produces about one pair."""

    beta: float             # δ₁ — the long-run cointegrating coefficient
    intercept: float        # δ₀
    p_value: float          # ADF on the residuals, residual-based table
    adf_stat: float
    r_squared: float

    alpha: float            # error-correction loading; < 0 means it corrects
    alpha_p_value: float
    half_life: float        # bars; inf when the pair does not correct

    observations: int
    used_residual_table: bool

    def is_cointegrated(self, level: float = 0.05) -> bool:
        return self.p_value < level

    def is_tradeable(self, level: float = 0.05, max_half_life: float = 60.0
                     ) -> bool:
        """Cointegrated *and* actually correcting, within a usable horizon.

        The second half is the part that gets dropped. A pair can clear the
        test at p = 0.01 with α ≈ 0 — a real long-run relationship that never
        pulls back on any timescale you can hold.
        """
        return (self.is_cointegrated(level)
                and self.alpha < 0
                and math.isfinite(self.half_life)
                and 0 < self.half_life <= max_half_life)

    def __str__(self) -> str:
        hl = f"{self.half_life:.1f}" if math.isfinite(self.half_life) else "inf"
        return (f"EG p={self.p_value:.4f} beta={self.beta:.3f} "
                f"alpha={self.alpha:+.4f} half-life={hl} bars")


NOT_COINTEGRATED = CointegrationResult(
    beta=float("nan"), intercept=float("nan"), p_value=1.0,
    adf_stat=float("nan"), r_squared=0.0, alpha=0.0, alpha_p_value=1.0,
    half_life=float("inf"), observations=0, used_residual_table=True)


# ═══════════════════════════════════════════════════════════════════════════
# The two-step procedure
# ═══════════════════════════════════════════════════════════════════════════

def engle_granger(y: np.ndarray, x: np.ndarray, *, lags: int = 1,
                  use_residual_critical_values: bool = True
                  ) -> CointegrationResult:
    """Full Engle-Granger (1987) on two aligned series.

    Step 1:  y_t = δ₀ + δ₁·x_t + u_t        →  is û stationary?
    Step 2:  Δy_t = φ₀ + Σφⱼ·Δy_{t-j}
                       + Σθₕ·Δx_{t-h} + α·û_{t-1} + ε_t   →  how fast?

    `y` and `x` must be the same length and free of NaN (`ctx.window()`
    already guarantees both).
    """
    y = np.asarray(y, dtype=float)
    x = np.asarray(x, dtype=float)
    if y.size != x.size or y.size < 30:
        return NOT_COINTEGRATED

    # ── Step 1 — long-run equilibrium ─────────────────────────────────
    x_mean, y_mean = x.mean(), y.mean()
    variance = float(((x - x_mean) ** 2).sum())
    if variance <= 0:
        return NOT_COINTEGRATED

    beta = float(((x - x_mean) * (y - y_mean)).sum() / variance)
    intercept = float(y_mean - beta * x_mean)
    residuals = y - intercept - beta * x

    total_ss = float(((y - y_mean) ** 2).sum())
    r_squared = 1.0 - float((residuals ** 2).sum()) / total_ss if total_ss else 0.0

    try:
        if use_residual_critical_values:
            # coint() re-runs the regression internally and applies
            # MacKinnon's residual-based table — the correct one here.
            from statsmodels.tsa.stattools import coint
            adf_stat, p_value, _ = coint(y, x, trend="c", autolag="AIC")
        else:
            # The wrong table, kept so the over-rejection is measurable.
            from statsmodels.tsa.stattools import adfuller
            adf_stat, p_value = adfuller(residuals, autolag="AIC")[:2]
    except Exception:                                        # noqa: BLE001
        return NOT_COINTEGRATED

    # ── Step 2 — error correction ─────────────────────────────────────
    alpha, alpha_p, half_life = _error_correction(y, x, residuals, lags)

    return CointegrationResult(
        beta=beta, intercept=intercept, p_value=float(p_value),
        adf_stat=float(adf_stat), r_squared=r_squared,
        alpha=alpha, alpha_p_value=alpha_p, half_life=half_life,
        observations=int(y.size),
        used_residual_table=use_residual_critical_values)


def _error_correction(y: np.ndarray, x: np.ndarray, residuals: np.ndarray,
                      lags: int) -> tuple[float, float, float]:
    """Fit the ECM and return (α, p-value, half-life in bars).

    α is the loading on û_{t-1}:
        α  < 0   y corrects back toward equilibrium after a shock
        α ≈ 0    no correction — consistent with no cointegration
        α ≈ −1   full correction within one period
        α  > 0   diverging; pathological

    half-life = −ln2 / ln(1 + α), valid on −2 < α < 0.
    """
    dy, dx = np.diff(y), np.diff(x)
    ec_lag = residuals[:-1]                    # û_{t-1}, aligned with Δy_t
    n, start = len(dy), lags
    if n - start < 20:
        return 0.0, 1.0, float("inf")

    columns = [np.ones(n - start)]                                # φ₀
    for j in range(1, lags + 1):
        columns.append(dy[start - j:n - j])                       # Δy_{t-j}
    for h in range(0, lags + 1):
        columns.append(dx[start - h:n - h])                       # Δx_{t-h}
    columns.append(ec_lag[start:])                                # û_{t-1}

    design = np.column_stack(columns)
    target = dy[start:]

    try:
        from statsmodels.regression.linear_model import OLS
        fit = OLS(target, design).fit()
        alpha = float(fit.params[-1])
        alpha_p = float(fit.pvalues[-1])
    except Exception:                                        # noqa: BLE001
        return 0.0, 1.0, float("inf")

    if -2.0 < alpha < 0.0:
        half_life = float(-math.log(2.0) / math.log(1.0 + alpha))
    else:
        half_life = float("inf")
    return alpha, alpha_p, half_life


# ═══════════════════════════════════════════════════════════════════════════
# Cheap screens — run these before the expensive test
# ═══════════════════════════════════════════════════════════════════════════

def hurst(series: np.ndarray, max_lag: int = 40) -> float:
    """Hurst exponent by rescaled-variance.

        H < 0.5  mean-reverting
        H ≈ 0.5  random walk
        H > 0.5  trending

    Cheap, noisy, and useful only as a screen — it is not a test.
    """
    series = np.asarray(series, dtype=float)
    max_lag = min(max_lag, len(series) // 2)
    if max_lag < 4:
        return 0.5
    lags = np.arange(2, max_lag)
    deviations = [np.std(series[lag:] - series[:-lag]) for lag in lags]
    deviations = np.array(deviations)
    good = deviations > 0
    if good.sum() < 3:
        return 0.5
    slope = np.polyfit(np.log(lags[good]), np.log(deviations[good]), 1)[0]
    return float(slope)


def variance_ratio(series: np.ndarray, period: int = 5) -> float:
    """Lo–MacKinlay variance ratio.

    Under a random walk the variance of k-period returns is k times the
    variance of one-period returns, so VR ≈ 1. Below 1 is mean reversion.
    """
    series = np.asarray(series, dtype=float)
    returns = np.diff(series)
    if len(returns) < period * 4:
        return 1.0
    var_one = float(np.var(returns, ddof=1))
    if var_one <= 0:
        return 1.0
    aggregated = np.add.reduceat(
        returns, np.arange(0, len(returns) - len(returns) % period, period))
    var_k = float(np.var(aggregated, ddof=1))
    return var_k / (period * var_one)


@dataclass(frozen=True)
class PairScan:
    """One pair's place in a scan across a universe."""

    a: str
    b: str
    result: CointegrationResult
    tradeable: bool          # cointegrated at the *corrected* level, and correcting


def scan_pairs(prices: dict[str, np.ndarray], *, level: float = 0.05,
               max_half_life: float = 60.0, correction: str = "bonferroni",
               progress: Callable[[float, str], None] | None = None,
               ) -> list[PairScan]:
    """Engle-Granger over every pair in a universe, prefiltered for cost.

    `prices` maps symbol to an aligned, NaN-free close series — same length
    and same calendar for every entry, which is the caller's job (a `Dataset`
    slice already guarantees it for a fixed date range).

    Two things a naive "test every pair, keep p < 0.05" scan gets wrong at
    this scale, both handled here:

    **The search itself is not free.** `prefilter()` (correlation, Hurst,
    variance ratio — all O(n), no regression) runs first and only pairs that
    clear it pay for the real test, because two names that never even look
    related are not worth a `statsmodels.coint()` call. This is a cost
    optimisation, not a statistical one — it does not change which pairs are
    reported as tradeable, only how long finding them takes. On a bull-market
    sample the prefilter alone still passes a large fraction of pairs (the
    market co-movement problem this whole module exists to catch), so the
    saving is real but bounded.

    **Testing N pairs at p < 0.05 finds ~0.05N false positives by chance
    alone**, even if nothing in the universe is truly related. At a few
    hundred names this is dozens of pairs, not one or two. `correction="bonferroni"`
    (the default) divides `level` by the number of pairs that actually reached
    the expensive test, so `is_tradeable` is evaluated against a threshold
    that accounts for how many chances the scan had to be wrong. Pass
    `correction=None` to see the uncorrected — and inflated — count instead.
    """
    symbols = sorted(prices)
    pairs = list(itertools.combinations(symbols, 2))
    total = len(pairs)

    candidates: list[tuple[str, str, CointegrationResult]] = []
    for i, (a, b) in enumerate(pairs):
        y, x = prices[a], prices[b]
        ok, _ = prefilter(y, x)
        if ok:
            candidates.append((a, b, engle_granger(y, x)))
        if progress and (i % 200 == 0 or i == total - 1):
            progress((i + 1) / total, f"{a}/{b}")

    tested = len(candidates)
    effective_level = level / tested if (correction == "bonferroni" and tested) \
        else level

    scans = [PairScan(a, b, result,
                      tradeable=result.is_tradeable(effective_level, max_half_life))
             for a, b, result in candidates]
    scans.sort(key=lambda s: s.result.p_value)
    return scans


def prefilter(y: np.ndarray, x: np.ndarray, *, min_correlation: float = 0.5,
              max_hurst: float = 0.6, max_variance_ratio: float = 1.2
              ) -> tuple[bool, str]:
    """Three cheap screens, before paying for the full test.

    Returns `(passed, reason_if_not)`. This is a triage step, not evidence:
    passing it says nothing except that the expensive test is worth running.
    """
    y = np.asarray(y, dtype=float)
    x = np.asarray(x, dtype=float)
    if y.size < 30 or y.size != x.size:
        return False, "not enough aligned observations"

    correlation = float(np.corrcoef(y, x)[0, 1])
    if not np.isfinite(correlation) or abs(correlation) < min_correlation:
        return False, f"correlation {correlation:.2f} below {min_correlation:.2f}"

    x_mean, y_mean = x.mean(), y.mean()
    variance = float(((x - x_mean) ** 2).sum())
    if variance <= 0:
        return False, "x has no variance"
    beta = float(((x - x_mean) * (y - y_mean)).sum() / variance)
    spread = y - beta * x

    h = hurst(spread)
    if h > max_hurst:
        return False, f"spread Hurst {h:.2f} above {max_hurst:.2f} (trending)"

    vr = variance_ratio(spread)
    if vr > max_variance_ratio:
        return False, f"variance ratio {vr:.2f} above {max_variance_ratio:.2f}"

    return True, ""

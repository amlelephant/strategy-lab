"""
tests/test_core.py
==================
The framework itself: cash accounting, cost application, metric arithmetic.

The portfolio tests carry over the ones written for the original paper broker,
because the behaviours they pin down are the ones that were wrong at some
point: short sales crediting cash, cost basis re-averaging on an add, and a
mark-to-market that treats a short as a liability.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from lab import Hub, RunConfig, build
from lab.core.contract import Intent, Order, Side
from lab.core.costs import CostModel, FillTiming, LatencyModel
from lab.core.metrics import summarise
from lab.core.portfolio import InsufficientCash, Portfolio
from lab.data.loaders import frame_to_dataset
from synthetic_prices import synthetic

TS = pd.Timestamp("2024-01-02")


# ── portfolio ─────────────────────────────────────────────────────────────

def test_long_costs_cash_and_short_credits_it():
    book = Portfolio(100_000)
    book.open("AAA", Side.LONG, 100, 50.0, timestamp=TS)
    assert book.cash == pytest.approx(95_000)

    book.open("BBB", Side.SHORT, 100, 30.0, timestamp=TS)
    assert book.cash == pytest.approx(98_000), \
        "selling borrowed shares must bring cash in"


def test_cost_basis_is_share_weighted_across_adds():
    book = Portfolio(100_000)
    book.open("AAA", Side.LONG, 100, 10.0, timestamp=TS)
    book.open("AAA", Side.LONG, 300, 20.0, timestamp=TS)
    position = book.get("AAA", Side.LONG)
    assert position.quantity == 400
    assert position.cost_basis == pytest.approx(17.5)   # not 10.0, not 15.0


def test_pnl_signs():
    book = Portfolio(100_000)
    book.open("AAA", Side.LONG, 100, 10.0, timestamp=TS)
    fill = book.close("AAA", Side.LONG, 12.0, timestamp=TS)
    assert fill.realised_pnl == pytest.approx(200)

    book.open("BBB", Side.SHORT, 100, 10.0, timestamp=TS)
    fill = book.close("BBB", Side.SHORT, 8.0, timestamp=TS)
    assert fill.realised_pnl == pytest.approx(200), \
        "a short that falls is a profit"


def test_mark_to_market_treats_a_short_as_a_liability():
    book = Portfolio(100_000)
    book.open("AAA", Side.SHORT, 100, 10.0, timestamp=TS)
    assert book.value({"AAA": 10.0}) == pytest.approx(100_000)
    assert book.value({"AAA": 12.0}) == pytest.approx(99_800)
    assert book.value({"AAA": 8.0}) == pytest.approx(100_200)


def test_can_afford_nets_the_legs_of_a_pair():
    """The reason `can_afford(trade1, trade2)` existed in the original broker.

    $9,000 of stock against $5,000 of cash is unaffordable on its own, and
    perfectly affordable when it is one leg of a hedged pair — because the
    short leg brings the money in.
    """
    book = Portfolio(5_000)
    long_leg = Order.open("AAA", Side.LONG, 100, reason="test")
    short_leg = Order.open("BBB", Side.SHORT, 100, reason="test")
    prices = {"AAA": 90.0, "BBB": 90.0}

    ok, net = book.can_afford([long_leg, short_leg], prices)
    assert net == pytest.approx(0.0), "a matched pair is roughly cash-neutral"
    assert ok

    ok, net = book.can_afford([long_leg], prices)
    assert net == pytest.approx(9_000.0)
    assert not ok, "the long leg alone is not affordable on $5,000"


def test_overdraft_is_refused():
    book = Portfolio(1_000)
    with pytest.raises(InsufficientCash):
        book.open("AAA", Side.LONG, 100, 50.0, timestamp=TS)


def test_partial_close_leaves_the_rest_open():
    book = Portfolio(100_000)
    book.open("AAA", Side.LONG, 100, 10.0, timestamp=TS)
    book.close("AAA", Side.LONG, 12.0, timestamp=TS, quantity=40)
    position = book.get("AAA", Side.LONG)
    assert position.quantity == pytest.approx(60)
    assert position.cost_basis == pytest.approx(10.0)


def test_flatten_closes_everything():
    book = Portfolio(100_000)
    book.open("AAA", Side.LONG, 100, 10.0, timestamp=TS)
    book.open("BBB", Side.SHORT, 50, 20.0, timestamp=TS)
    book.flatten({"AAA": 11.0, "BBB": 19.0}, TS)
    assert book.open_positions() == ()
    assert book.cash == pytest.approx(100_000 + 100 + 50)


# ── costs ─────────────────────────────────────────────────────────────────

def test_slippage_always_moves_against_you():
    costs = CostModel(commission_bps=0, slippage_bps=10, latency=None)
    assert costs.fill_price(100.0, Side.LONG, opening=True) > 100.0   # buying
    assert costs.fill_price(100.0, Side.LONG, opening=False) < 100.0  # selling
    assert costs.fill_price(100.0, Side.SHORT, opening=True) < 100.0  # selling
    assert costs.fill_price(100.0, Side.SHORT, opening=False) > 100.0  # buying


def test_frictionless_fills_at_the_reference_price():
    costs = CostModel(commission_bps=0, slippage_bps=0, latency=None)
    assert costs.fill_price(100.0, Side.LONG, opening=True) == pytest.approx(100.0)
    assert costs.commission(1_000_000) == 0.0


def test_latency_drift_is_seeded_and_reproducible():
    a = CostModel(slippage_bps=0, commission_bps=0,
                  latency=LatencyModel.conservative(), seed=42)
    b = CostModel(slippage_bps=0, commission_bps=0,
                  latency=LatencyModel.conservative(), seed=42)
    first = [a.fill_price(100.0, Side.LONG, True, 0.02) for _ in range(20)]
    second = [b.fill_price(100.0, Side.LONG, True, 0.02) for _ in range(20)]
    assert first == second
    assert len(set(first)) > 1, "latency drift should actually vary"


def test_round_trip_bps():
    assert CostModel(commission_bps=1, slippage_bps=2).round_trip_bps() == 6.0


# ── metrics ───────────────────────────────────────────────────────────────

def _curve(values, freq="B"):
    return pd.Series(values, index=pd.bdate_range("2021-01-04", periods=len(values)))


def test_flat_curve_has_no_return_and_no_sharpe():
    perf = summarise(_curve([100_000.0] * 300))
    assert perf.total_return == pytest.approx(0.0)
    assert perf.sharpe == pytest.approx(0.0)
    assert perf.max_drawdown == pytest.approx(0.0)


def test_max_drawdown_measures_from_the_peak():
    perf = summarise(_curve([100.0, 120.0, 60.0, 90.0]))
    assert perf.max_drawdown == pytest.approx(-0.5)   # 120 -> 60


def test_sharpe_significance_falls_with_fewer_observations():
    """The whole point of reporting t: the same Sharpe means less on less
    data."""
    rng = np.random.default_rng(3)
    returns = rng.normal(0.0006, 0.01, 2000)
    equity = 100_000 * np.cumprod(1 + returns)

    long_run = summarise(_curve(equity))
    short_run = summarise(_curve(equity[:60]))

    assert long_run.observations > short_run.observations
    assert abs(long_run.sharpe_t) > abs(short_run.sharpe_t)
    assert math.isfinite(long_run.sharpe_stderr)


def test_periods_per_year_is_inferred_from_the_index():
    daily = synthetic(("AAA", "BBB"), bars=300)
    assert daily.periods_per_year == pytest.approx(252.0)


# ── data loading ──────────────────────────────────────────────────────────

def test_tidy_and_wide_frames_load_to_the_same_dataset():
    index = pd.bdate_range("2021-01-04", periods=30)
    wide = pd.DataFrame({"AAA": np.arange(30.0) + 10,
                         "BBB": np.arange(30.0) + 20}, index=index)
    tidy = wide.stack().rename("close").reset_index()
    tidy.columns = ["timestamp", "symbol", "close"]

    from_wide = frame_to_dataset(wide.reset_index().rename(
        columns={"index": "timestamp"}))
    from_tidy = frame_to_dataset(tidy)

    assert from_wide.symbols == from_tidy.symbols
    np.testing.assert_allclose(from_wide.history("AAA", 29),
                               from_tidy.history("AAA", 29))


def test_fundamentals_are_not_knowable_before_the_filing_lag():
    from lab.data.dataset import Dataset, FundamentalRecord

    quarter = pd.Timestamp("2021-06-30")
    record = FundamentalRecord(quarter, quarter + pd.Timedelta(days=60),
                               {"ROIC": 0.2})
    base = synthetic(("AAA", "BBB"), bars=400)
    dataset = Dataset({f: base._fields[f] for f in base.fields},
                      symbols=base.symbols, fundamentals={"AAA": [record]})

    assert dataset.fundamentals_at("AAA", quarter) is None, \
        "a quarter-end date is not a knowledge date"
    assert dataset.fundamentals_at("AAA", quarter + pd.Timedelta(days=61)) \
        == {"ROIC": 0.2}


def test_universe_narrowing_records_what_it_dropped():
    dataset = synthetic(("AAA", "BBB"), bars=100)
    view = dataset.for_universe(["AAA", "NOPE"])
    assert view.symbols == ("AAA",)
    assert view.dropped == ("NOPE",)


# ═══════════════════════════════════════════════════════════════════════════
# The market benchmark
#
# Alpha is only meaningful if it was measured against the market rather than
# against the names the run happened to pick, so these check that the market
# series reaches the measurement, survives every narrowing on the way, and
# cannot be traded once it gets there.
# ═══════════════════════════════════════════════════════════════════════════

def _with_market(dataset, level=None):
    """`dataset` carrying a rising benchmark on its own clock."""
    from lab.data.dataset import Dataset

    if level is None:
        level = pd.Series(np.linspace(100.0, 200.0, len(dataset.index)),
                          index=dataset.index)
    return Dataset({f: dataset._fields[f] for f in dataset.fields},
                   symbols=dataset.symbols, fundamentals=dataset.fundamentals,
                   name=dataset.name, benchmark=level,
                   benchmark_label="the S&P 500")


def test_the_benchmark_is_the_market_not_the_universe():
    """The whole point of the change: measure against the market.

    An equal-weight hold of the universe and the market are different series,
    and `benchmark_equity` has to return the second one. If it returns the
    first, every strategy is credited for its universe.
    """
    from lab.core.metrics import (benchmark_equity, benchmark_label,
                                  has_market_benchmark)

    dataset = _with_market(synthetic(("AAA", "BBB"), bars=200))
    curve = benchmark_equity(dataset, 100_000.0)

    assert has_market_benchmark(dataset)
    assert benchmark_label(dataset) == "the S&P 500"
    # Normalised to the starting cash and doubling, because the series does.
    assert curve.iloc[0] == pytest.approx(100_000.0)
    assert curve.iloc[-1] == pytest.approx(200_000.0)


def test_without_a_market_series_the_fallback_says_so():
    """A clone with an empty `data/` still runs, but must not claim the market."""
    from lab.core.metrics import (FALLBACK_BENCHMARK_LABEL, benchmark_equity,
                                  benchmark_label, has_market_benchmark)

    dataset = synthetic(("AAA", "BBB"), bars=200)
    assert not has_market_benchmark(dataset)
    assert benchmark_label(dataset) == FALLBACK_BENCHMARK_LABEL
    assert len(benchmark_equity(dataset, 100_000.0)) == len(dataset.index)


def test_a_run_without_a_market_series_warns_rather_than_reporting_quietly():
    dataset = synthetic(("AAA", "BBB"), bars=200)
    result = Hub(dataset, build("hundred_day_mov_avg"), RunConfig()).run()
    assert any("market series" in w for w in result.warnings)
    assert result.benchmark_label == "the equal-weight universe"


def test_a_run_with_a_market_series_does_not_warn_and_labels_the_market():
    dataset = _with_market(synthetic(("AAA", "BBB"), bars=200))
    result = Hub(dataset, build("hundred_day_mov_avg"), RunConfig()).run()
    assert not any("market series" in w for w in result.warnings)
    assert result.benchmark_label == "the S&P 500"
    assert result.to_dict()["benchmark"]["label"] == "the S&P 500"
    # The label travels with the Performance too, so a verdict sentence names
    # what it was measured against instead of saying "the benchmark".
    assert "the S&P 500" in result.sleeves[0].performance.verdict()


def test_narrowing_a_dataset_keeps_its_benchmark():
    """`for_universe` and `between` must not silently drop the yardstick.

    A run that trims its universe or its dates and loses the benchmark would
    fall back to the equal-weight comparison without anything saying so.
    """
    dataset = _with_market(synthetic(("AAA", "BBB", "CCC"), bars=300))

    narrowed = dataset.for_universe(["AAA", "BBB"])
    assert narrowed.benchmark is not None
    assert narrowed.benchmark_label == "the S&P 500"
    assert len(narrowed.benchmark) == len(narrowed.index)

    cut = dataset.between(dataset.index[50], dataset.index[-50])
    assert cut.benchmark is not None
    assert len(cut.benchmark) == len(cut.index)
    assert cut.benchmark.iloc[0] == pytest.approx(dataset.benchmark.iloc[50])


def test_the_benchmark_is_never_a_tradeable_symbol():
    """It is not a column in the price frames, so no universe can select it.

    "Every symbol in the price file" is one of the GUI's universe options; a
    benchmark living in `close` would be swept into it, and a strategy would
    end up holding the thing it is measured against.
    """
    dataset = _with_market(synthetic(("AAA", "BBB"), bars=120))
    assert "SPY" not in dataset.symbols
    assert "SPY" not in dataset._fields["close"].columns
    assert dataset.benchmark is not None


def test_a_zero_risk_free_rate_manufactures_alpha_for_a_low_beta_strategy():
    """The bug this data file exists to prevent, stated as arithmetic.

    Build a strategy that is *exactly* 0.3 of the benchmark plus cash earning
    the bill rate. It has no skill: its true alpha is zero. Regressing raw
    returns hands it `rf · (1 − β)` anyway, and only subtracting the rate
    recovers the right answer.
    """
    from lab.core.metrics import Performance, alpha

    index = pd.bdate_range("2010-01-04", periods=2500)
    rng = np.random.default_rng(17)
    rf_annual = 0.04
    rf = rf_annual / 252.0
    beta = 0.30

    market_r = rng.normal(0.0003, 0.011, len(index))
    # CAPM with zero alpha: r_s − rf = β(r_m − rf)
    strat_r = rf + beta * (market_r - rf)

    bench = pd.Series(100_000 * np.cumprod(1 + market_r), index=index)
    strat = pd.Series(100_000 * np.cumprod(1 + strat_r), index=index)

    naive = alpha(Performance(), strat, bench, periods_per_year=252.0)
    correct = alpha(Performance(), strat, bench, periods_per_year=252.0,
                    risk_free=rf_annual)

    assert correct.beta == pytest.approx(beta, abs=1e-6)
    # The right answer is zero, and only the second regression finds it.
    assert correct.alpha == pytest.approx(0.0, abs=1e-6)
    # The first invents rf·(1 − β) = 4% × 0.7 = 2.8% a year out of nothing.
    assert naive.alpha == pytest.approx(rf_annual * (1 - beta), abs=1e-3)


def test_a_risk_free_series_is_used_bar_by_bar_not_averaged():
    """The rate moved from 5% to 0% to 5%; a strategy holding cash cares when."""
    from lab.core.metrics import Performance, alpha, summarise

    index = pd.bdate_range("2010-01-04", periods=1200)
    rates = pd.Series(np.where(np.arange(len(index)) < 600, 0.05, 0.0),
                      index=index)
    rng = np.random.default_rng(4)
    equity = pd.Series(100_000 * np.cumprod(
        1 + rng.normal(0.0004, 0.01, len(index))), index=index)

    varying = summarise(equity, periods_per_year=252.0, risk_free=rates)
    # The constant that has the same mean over the bars actually used —
    # pct_change drops the first, so it is 599 of 1199 days at 5%.
    equivalent = float(rates.iloc[1:].mean())
    flat = summarise(equity, periods_per_year=252.0, risk_free=equivalent)

    assert varying.risk_free_rate == pytest.approx(equivalent, abs=1e-9)
    assert flat.risk_free_rate == pytest.approx(equivalent, abs=1e-9)

    # Same mean, different Sharpe: subtracting 5% for half the sample and 0%
    # for the other half is not the same as subtracting 2.5% throughout, and
    # a flattened rate would make these identical.
    assert varying.sharpe != pytest.approx(flat.sharpe, abs=1e-6)


def test_alpha_and_outperformance_are_reported_as_different_questions():
    """A low-beta strategy can have positive alpha and still trail the index.

    This is the case that read as "beats the market" beside a chart showing it
    below the market, so the verdict has to name both numbers.
    """
    from lab.core.metrics import Performance, alpha

    index = pd.bdate_range("2010-01-04", periods=3000)
    rng = np.random.default_rng(23)
    market_r = rng.normal(0.0005, 0.011, len(index))
    # A third of the market's exposure, plus a small genuine edge. Less money
    # than the index, more money than its risk exposure justifies.
    strat_r = 0.3 * market_r + 0.00012 + rng.normal(0, 0.002, len(index))

    bench = pd.Series(100_000 * np.cumprod(1 + market_r), index=index)
    strat = pd.Series(100_000 * np.cumprod(1 + strat_r), index=index)

    perf = alpha(Performance(), strat, bench, periods_per_year=252.0,
                 label="the S&P 500")

    assert perf.alpha > 0, "should have positive risk-adjusted alpha"
    assert perf.active_return < 0, "but should still trail the index outright"
    assert not perf.outperformed
    # Positive intercept, significant t — and still not "has alpha", because it
    # ended with less money than the index.
    assert perf.alpha_t > 2
    assert not perf.has_alpha

    sentence = perf.verdict()
    assert sentence.startswith(f"{perf.active_return:+.2%}"), (
        "the verdict must lead with the plain comparison, not the alpha")
    assert "did not beat the market" in sentence
    assert "beta" in sentence, "and must still report the risk-adjusted view"


def test_a_market_neutral_book_that_lost_to_the_index_claims_nothing():
    """The case that prompted this: beta 0.00 turns any profit into "alpha".

    A book returning ~3% a year while the index returns ~16% has essentially no
    market exposure, so the regression intercept is its whole return and the
    old headline read "+3% alpha" on a result that trailed by thirteen points.
    """
    from lab.core.metrics import Performance, alpha

    index = pd.bdate_range("2021-01-04", periods=1125)
    rng = np.random.default_rng(31)
    # Market compounding at roughly 16% a year.
    market_r = rng.normal(0.16 / 252, 0.011, len(index))
    # Uncorrelated with it, compounding at roughly 3%. Low vol on purpose: at
    # a realistic daily sigma the sample mean is the same size as its own
    # standard error and the test would be measuring the seed.
    strat_r = rng.normal(0.03 / 252, 0.001, len(index))

    bench = pd.Series(100_000 * np.cumprod(1 + market_r), index=index)
    strat = pd.Series(100_000 * np.cumprod(1 + strat_r), index=index)

    perf = alpha(Performance(), strat, bench, periods_per_year=252.0,
                 label="the S&P 500")

    assert abs(perf.beta) < 0.1, "should be market-neutral"
    assert perf.alpha > 0, "the regression still reports a positive intercept"
    assert perf.active_return < -0.10, "while trailing the index badly"

    assert not perf.has_alpha, "must not claim alpha while losing to the index"
    assert not perf.outperformed

    sentence = perf.verdict()
    assert "did not beat the market" in sentence
    assert "real alpha" not in sentence


def test_a_benchmark_on_a_different_calendar_is_aligned_not_dropped():
    """A benchmark that skips one of the dataset's bars carries its last level."""
    dataset = synthetic(("AAA",), bars=100)
    sparse = pd.Series(
        np.linspace(100.0, 150.0, 20),
        index=dataset.index[::5][:20])

    aligned = _with_market(dataset, level=sparse)
    assert len(aligned.benchmark) == len(dataset.index)
    assert aligned.benchmark.notna().all()
    assert aligned.benchmark.iloc[0] == pytest.approx(100.0)


# ═══════════════════════════════════════════════════════════════════════════
# Alpha — the number the result page leads with
#
# These are calibration tests, not behaviour tests. `alpha()` decides whether
# a strategy is reported as working, so it has to be checked against cases
# where the right answer is known in advance rather than read off a chart.
# ═══════════════════════════════════════════════════════════════════════════

def test_a_strategy_that_is_the_benchmark_has_no_alpha_and_unit_beta():
    """The strongest available check: regress the benchmark on itself.

    Any bug in alignment, annualisation or the regression shows up here as a
    non-zero alpha or a beta away from 1, because the correct answer is
    exactly (0, 1) and nothing else.
    """
    from lab.core.metrics import Performance, alpha

    index = pd.bdate_range("2021-01-04", periods=500)
    rng = np.random.default_rng(11)
    equity = pd.Series(100_000 * np.exp(np.cumsum(
        rng.normal(0.0004, 0.011, len(index)))), index=index)

    perf = alpha(Performance(), equity, equity, periods_per_year=252.0)

    assert perf.beta == pytest.approx(1.0, abs=1e-9)
    assert perf.alpha == pytest.approx(0.0, abs=1e-9)
    assert perf.active_return == pytest.approx(0.0, abs=1e-9)
    assert perf.tracking_error == pytest.approx(0.0, abs=1e-9)


def test_a_constant_edge_over_the_benchmark_is_recovered_as_alpha():
    """Add a known 2 bp per day to the benchmark and ask for it back.

    2bp/day × 252 ≈ 5.04% a year, and a strategy that is the benchmark plus a
    constant has a beta of 1. A sign error, a missing annualisation or a
    per-period/annual mix-up all fail this.
    """
    from lab.core.metrics import Performance, alpha

    index = pd.bdate_range("2021-01-04", periods=1000)
    rng = np.random.default_rng(5)
    bench_returns = rng.normal(0.0003, 0.010, len(index))
    edge = 0.0002

    benchmark = pd.Series(100_000 * np.exp(np.cumsum(bench_returns)), index=index)
    strategy = pd.Series(100_000 * np.exp(np.cumsum(bench_returns + edge)),
                         index=index)

    perf = alpha(Performance(), strategy, benchmark, periods_per_year=252.0)

    assert perf.beta == pytest.approx(1.0, abs=0.02)
    assert perf.alpha == pytest.approx(edge * 252, rel=0.05)
    assert perf.alpha_t > 5, "a constant, noiseless edge must be significant"
    assert perf.information_ratio > 5


def test_a_leveraged_benchmark_is_beta_not_alpha():
    """Twice the benchmark's return is twice its risk, not skill.

    This is the failure the whole metric exists to catch: a strategy that
    simply holds more market looks excellent on return and on Sharpe, and
    must still report ~zero alpha.
    """
    from lab.core.metrics import Performance, alpha, summarise

    index = pd.bdate_range("2021-01-04", periods=1000)
    rng = np.random.default_rng(9)

    # Doubled *simple* returns, compounded — daily-rebalanced 2x leverage.
    # Doubling log returns instead would be a different animal: exp(2r) has a
    # convexity term that a simple-return regression correctly reports as
    # alpha, which is a real property of that payoff and not a bug here.
    bench_returns = rng.normal(0.0004, 0.010, len(index))
    benchmark = pd.Series(100_000 * np.cumprod(1 + bench_returns), index=index)
    levered = pd.Series(100_000 * np.cumprod(1 + 2 * bench_returns), index=index)

    perf = alpha(summarise(levered, periods_per_year=252.0), levered, benchmark,
                 periods_per_year=252.0)

    assert perf.total_return > summarise(benchmark).total_return
    assert perf.beta == pytest.approx(2.0, abs=1e-6)
    assert perf.alpha == pytest.approx(0.0, abs=1e-6)
    assert abs(perf.alpha_t) < 2.0, "leverage is not alpha"


def test_alpha_aligns_mismatched_calendars_instead_of_regressing_garbage():
    from lab.core.metrics import Performance, alpha

    index = pd.bdate_range("2021-01-04", periods=400)
    rng = np.random.default_rng(3)
    series = pd.Series(100_000 * np.exp(np.cumsum(
        rng.normal(0.0003, 0.010, len(index)))), index=index)

    # The sleeve starts 100 bars late, as a strategy with a long warmup does.
    perf = alpha(Performance(), series.iloc[100:], series, periods_per_year=252.0)
    assert perf.beta == pytest.approx(1.0, abs=1e-9)
    assert perf.alpha == pytest.approx(0.0, abs=1e-9)


def test_a_run_reports_the_benchmark_comparison_for_every_sleeve():
    """Wiring check: the hub must hand each sleeve the benchmark.

    Every field the result page leads with has to arrive filled in — a missing
    `active_return` renders as an empty hero rather than as an error.
    """
    dataset = synthetic(("AAA", "BBB", "CCC", "DDD"), bars=400)
    result = Hub(dataset, [build("hundred_day_mov_avg")], RunConfig()).run()

    perf = result.sleeves[0].performance
    assert perf.benchmark_label
    for field_name in ("alpha", "alpha_t", "beta", "active_return"):
        assert np.isfinite(getattr(perf, field_name)), field_name


def test_a_result_carries_the_underlying_universe_curve():
    """The equal-weight hold of the run's own names, for the chart's dropdown.

    It was a registered strategy taking up a sleeve in every run; it is a
    curve, so it rides on the result instead.
    """
    dataset = synthetic(("AAA", "BBB", "CCC"), bars=300)
    result = Hub(dataset, [build("hundred_day_mov_avg")], RunConfig()).run()

    assert len(result.universe) == len(dataset.index)
    assert result.universe.iloc[0] == pytest.approx(100_000.0)
    payload = result.to_dict()
    assert payload["universe"]["equity"], "the chart needs points to draw"
    assert payload["universe"]["label"]

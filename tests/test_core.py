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

from lab.core.contract import Intent, Order, Side
from lab.core.costs import CostModel, FillTiming, LatencyModel
from lab.core.metrics import summarise
from lab.core.portfolio import InsufficientCash, Portfolio
from lab.data.loaders import frame_to_dataset, synthetic

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

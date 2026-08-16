"""
tests/test_contract.py
======================
The contract tests. These run against **every registered strategy**, so a new
strategy is covered the moment it is added to `lab/strategies/__init__.py` —
nobody has to remember to write these for it.

What they assert is not "does this strategy make money". It is that the
strategy is a legal citizen of the platform: it returns valid orders, it does
not reach into the future, it does not crash, and two identical runs produce
identical numbers. Those four properties are what make a result mean anything,
and they are exactly the properties that are easy to lose silently.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from lab import Hub, RunConfig, all_strategies, build, synthetic
from lab.core.contract import Intent, MarketContext, Order, Side
from lab.data.dataset import Dataset

STRATEGY_KEYS = sorted(all_strategies())


# ── fixtures ──────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def prices() -> Dataset:
    # Eight symbols, not four: the cross-sectional strategies refuse to
    # rebalance a universe smaller than five, so a four-symbol fixture would
    # skip them out of the contract tests entirely.
    return synthetic(("AAA", "BBB", "CCC", "DDD", "EEE", "FFF", "GGG", "HHH"),
                     bars=420, seed=11)


@pytest.fixture(scope="module")
def with_fundamentals(prices: Dataset) -> Dataset:
    """Synthetic quarterly fundamentals, so the cross-sectional strategies
    have something to rank."""
    from lab.data.dataset import FundamentalRecord

    rng = np.random.default_rng(5)
    metrics = ("Operating Margin", "FCF Margin", "ROIC", "Revenue Growth",
               "Net Debt/EBITDA", "FCF Yield", "EV/EBITDA", "P/E")
    quarters = pd.date_range(prices.index[0], prices.index[-1], freq="QE")

    records = {}
    for symbol in prices.symbols:
        rows = []
        for quarter in quarters:
            values = {
                "Operating Margin": float(rng.uniform(-0.1, 0.45)),
                "FCF Margin": float(rng.uniform(-0.1, 0.35)),
                "ROIC": float(rng.uniform(-0.05, 0.5)),
                "Revenue Growth": float(rng.uniform(-0.2, 0.4)),
                "Net Debt/EBITDA": float(rng.uniform(-1.0, 6.0)),
                "FCF Yield": float(rng.uniform(-0.02, 0.15)),
                "EV/EBITDA": float(rng.uniform(3.0, 60.0)),
                "P/E": float(rng.uniform(4.0, 80.0)),
            }
            assert set(values) == set(metrics)
            rows.append(FundamentalRecord(quarter,
                                          quarter + pd.Timedelta(days=60),
                                          values))
        records[symbol] = rows

    return Dataset({f: prices._fields[f] for f in prices.fields},
                   symbols=prices.symbols, fundamentals=records,
                   name="synthetic+fundamentals")


def dataset_for(key: str, prices: Dataset, with_fundamentals: Dataset) -> Dataset:
    return with_fundamentals if key.startswith("bw_") else prices


# ── every strategy ────────────────────────────────────────────────────────

@pytest.mark.parametrize("key", STRATEGY_KEYS)
def test_declares_its_metadata(key: str):
    cls = all_strategies()[key]
    assert cls.key == key
    assert cls.title, f"{key} has no title"
    assert cls.summary, f"{key} has no summary"
    names = [p.name for p in cls.params]
    assert len(names) == len(set(names)), f"{key} has duplicate parameter names"
    for param in cls.params:
        assert param.help, f"{key}.{param.name} has no help text"
        # A default that fails its own validation would break every form.
        assert param.coerce(param.default) is not None or param.default is False


@pytest.mark.parametrize("key", STRATEGY_KEYS)
def test_runs_and_returns_legal_orders(key, prices, with_fundamentals):
    """Run it for real and check every order the hub was handed was valid."""
    dataset = dataset_for(key, prices, with_fundamentals)
    strategy = build(key)

    seen: list[Order] = []
    original = strategy.on_bar

    def spy(ctx):
        orders = original(ctx)
        if isinstance(orders, Order):
            orders = [orders]
        seen.extend(orders or [])
        return orders

    strategy.on_bar = spy
    result = Hub(dataset, strategy).run()
    sleeve = result.sleeves[0]

    assert not sleeve.rejections or all(
        "raised" not in message for _, message in sleeve.rejections), \
        f"{key} raised during the run: {sleeve.rejections[:3]}"

    for order in seen:
        assert isinstance(order, Order)
        assert order.symbol in dataset.symbols
        if order.intent is Intent.OPEN:
            assert order.side in (Side.LONG, Side.SHORT)
            assert order.quantity and order.quantity > 0
        if order.intent is Intent.TARGET:
            assert order.weight is not None
            assert -3.0 <= order.weight <= 3.0, \
                f"{key} asked for a {order.weight:.1%} position"
        assert order.reason, f"{key} returned an order with no reason: {order}"

    assert len(sleeve.equity) == len(dataset.index)
    assert np.isfinite(sleeve.equity).all(), f"{key} produced a non-finite equity"


@pytest.mark.parametrize("key", STRATEGY_KEYS)
def test_is_reproducible(key, prices, with_fundamentals):
    """Same data, same parameters, same seed → identical numbers.

    This is the test that catches an unseeded RNG, a `datetime.now()`, or a
    dict iteration order leaking into position sizing.
    """
    dataset = dataset_for(key, prices, with_fundamentals)
    first = Hub(dataset, build(key), RunConfig()).run().sleeves[0]
    second = Hub(dataset, build(key), RunConfig()).run().sleeves[0]

    pd.testing.assert_series_equal(first.equity, second.equity)
    assert first.performance.sharpe == pytest.approx(
        second.performance.sharpe, nan_ok=True)


@pytest.mark.parametrize("key", STRATEGY_KEYS)
def test_cannot_see_the_future(key, prices, with_fundamentals):
    """Truncating the data must not change the decisions made before the cut.

    A strategy that peeks would produce a different trade log on the truncated
    run, because the information it was illegally using no longer exists.
    """
    dataset = dataset_for(key, prices, with_fundamentals)
    cut = dataset.index[len(dataset.index) * 2 // 3]

    full = Hub(dataset, build(key), RunConfig(flatten_at_end=False)).run()
    short = Hub(dataset.between(end=cut), build(key),
                RunConfig(flatten_at_end=False)).run()

    full_log = full.sleeves[0].trade_log
    short_log = short.sleeves[0].trade_log
    if full_log.empty or short_log.empty:
        pytest.skip(f"{key} did not trade on the synthetic dataset")

    # Compare the fills that happened strictly before the cut. The final bar
    # of the truncated run is excluded: the hub never asks for orders on the
    # last bar, so that one bar legitimately differs.
    last_short_bar = short.sleeves[0].equity.index[-1]
    a = full_log[full_log["timestamp"] < last_short_bar].reset_index(drop=True)
    b = short_log[short_log["timestamp"] < last_short_bar].reset_index(drop=True)

    assert len(a) == len(b), (
        f"{key} produced {len(a)} fills before {last_short_bar.date()} with the "
        f"full dataset but {len(b)} with the truncated one — it is using data "
        f"from after the decision point")
    for column in ("timestamp", "symbol", "side", "action"):
        assert list(a[column]) == list(b[column]), \
            f"{key} changed its {column} decisions when the future was removed"


@pytest.mark.parametrize("key", STRATEGY_KEYS)
def test_rejects_unknown_parameters(key):
    with pytest.raises(TypeError):
        build(key, {"definitely_not_a_real_parameter": 1})


# ── the contract objects themselves ───────────────────────────────────────

def test_order_constructors_validate():
    with pytest.raises(ValueError):
        Order.open("AAA", Side.LONG, 0)
    with pytest.raises(ValueError):
        Order.open("AAA", Side.LONG, -5)
    assert Order.close("AAA").intent is Intent.CLOSE
    assert Order.target("AAA", -0.2).weight == -0.2


def test_context_history_cannot_reach_past_the_cursor(prices):
    """The structural guarantee, asserted directly."""
    from lab.core.portfolio import Portfolio

    i = 100
    ctx = MarketContext(prices, Portfolio(), i)
    history = ctx.history("AAA")
    assert len(history) == i + 1
    assert history[-1] == pytest.approx(ctx.price("AAA"))

    full = prices.history("AAA", len(prices.index) - 1)
    assert len(full) > len(history)
    np.testing.assert_allclose(history, full[:len(history)])

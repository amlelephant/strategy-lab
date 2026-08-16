"""
lab/core/costs.py
=================
What a trade costs, and what it fills at.

Three components, applied in this order:

  1. **Commission** — a flat basis-point charge on notional, per side.
  2. **Spread / market impact** — a basis-point slippage that always moves
     against you: you buy above the reference price and sell below it.
  3. **Latency drift** — the price moves between the moment a signal is
     computed and the moment the order reaches the exchange. Modelled as a
     random walk over that interval scaled by recent volatility.

The latency model is ported from `stat-arb-v1/stat_arb_strategy.py`'s
`LatencyConfig`. It is worth keeping even though its magnitude is tiny at
swing-trade horizons, for one reason: it is the term that tells you *when the
strategy is a bad idea*. On a hold measured in days it contributes cents, and
the model says so. On a hold measured in minutes it dominates, and the model
says that too. A cost model that only ever produces a small number is not
telling you anything.

Latency drift is random. Set `seed` on the model — the hub does, from the run
config — or two runs of the same strategy will not agree.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum

import numpy as np

from .contract import Side


class FillTiming(str, Enum):
    """When an order decided at bar *t* actually fills.

    NEXT_OPEN is the default and the honest one: you cannot trade at a close
    you have only just observed. CLOSE exists for comparison against the
    older scripts, which all filled at the signal bar's own price and
    therefore booked a free option on every entry.
    """

    NEXT_OPEN = "next_open"   # bar t+1 open (falls back to t+1 close)
    CLOSE = "close"           # bar t close — optimistic, for comparison only


@dataclass
class LatencyModel:
    """Signal-to-execution delay and the price drift it causes.

    Ported from `stat-arb-v1`. The three named presets there are preserved
    below as classmethods.
    """

    mean_latency_ms: float = 100.0
    std_latency_ms: float = 50.0
    #: Scales the volatility used for drift. >1 says the moments you want to
    #: trade are more volatile than average, which they are.
    volatility_multiplier: float = 2.0

    @classmethod
    def optimistic(cls) -> "LatencyModel":
        return cls(50.0, 20.0, 1.5)

    @classmethod
    def realistic(cls) -> "LatencyModel":
        return cls(100.0, 50.0, 2.0)

    @classmethod
    def conservative(cls) -> "LatencyModel":
        return cls(200.0, 100.0, 3.0)

    def draw_latency_ms(self, rng: np.random.Generator) -> float:
        return max(0.0, rng.normal(self.mean_latency_ms, self.std_latency_ms))

    def drift(self, rng: np.random.Generator, volatility: float,
              bar_seconds: float) -> float:
        """Fractional price move during the delay.

        `volatility` is the per-bar return standard deviation; the delay is
        expressed as a fraction of a bar and the move scales with its square
        root, as a random walk does.
        """
        if not math.isfinite(volatility) or volatility <= 0 or bar_seconds <= 0:
            return 0.0
        latency_ms = self.draw_latency_ms(rng)
        fraction = latency_ms / 1000.0 / bar_seconds
        return float(rng.normal(0.0, volatility * math.sqrt(fraction)
                                * self.volatility_multiplier))

    def worst_case_ms(self, sigmas: float = 3.0) -> float:
        return self.mean_latency_ms + sigmas * self.std_latency_ms


@dataclass
class CostModel:
    """The full cost of getting in and out.

    Defaults are US large-cap retail-ish: 1bp commission and 2bp of spread per
    side, so a round trip on both legs of a pairs trade costs about 12bp of
    gross notional before any drift.
    """

    commission_bps: float = 1.0
    slippage_bps: float = 2.0
    latency: LatencyModel | None = field(default_factory=LatencyModel.realistic)
    timing: FillTiming = FillTiming.NEXT_OPEN
    #: Seconds per bar. The hub overwrites this from the dataset's frequency.
    bar_seconds: float = 6.5 * 3600.0
    seed: int = 7

    def __post_init__(self) -> None:
        self._rng = np.random.default_rng(self.seed)

    def reset(self) -> None:
        """Re-seed. The hub calls this at the start of every run so a run is
        reproducible regardless of what ran before it."""
        self._rng = np.random.default_rng(self.seed)

    # ── the two numbers the hub needs ──────────────────────────────────
    def fill_price(self, reference_price: float, side: Side,
                   opening: bool, volatility: float = 0.0) -> float:
        """Price actually transacted at.

        `side` is the *position* direction and `opening` says whether we are
        establishing or unwinding it, because those determine whether this
        particular order buys or sells:

            open long   → buy      close long  → sell
            open short  → sell     close short → buy

        Slippage always hurts: buys fill high, sells fill low.
        """
        buying = (side is Side.LONG) == opening
        edge = self.slippage_bps / 10_000.0
        drift = 0.0
        if self.latency is not None:
            drift = self.latency.drift(self._rng, volatility, self.bar_seconds)
        move = (edge + drift) if buying else (-edge + drift)
        price = reference_price * (1.0 + move)
        return max(price, 1e-6)

    def commission(self, notional: float) -> float:
        return abs(notional) * self.commission_bps / 10_000.0

    def round_trip_bps(self) -> float:
        """Total cost of getting in and out of one leg, in basis points.
        Strategies that gate on cost (see `stat_arb_ev`) read this."""
        return 2.0 * (self.commission_bps + self.slippage_bps)

    def describe(self) -> dict:
        return {
            "commission_bps": self.commission_bps,
            "slippage_bps": self.slippage_bps,
            "round_trip_bps": self.round_trip_bps(),
            "timing": self.timing.value,
            "latency": None if self.latency is None else {
                "mean_ms": self.latency.mean_latency_ms,
                "std_ms": self.latency.std_latency_ms,
                "vol_multiplier": self.latency.volatility_multiplier,
            },
            "seed": self.seed,
        }


#: The three scenarios `stat-arb-v1/main_tester.py` compared. Kept by name so
#: the latency-sensitivity study it ran can be reproduced from the GUI.
SCENARIOS: dict[str, CostModel] = {
    "frictionless": CostModel(commission_bps=0.0, slippage_bps=0.0, latency=None),
    "optimistic": CostModel(commission_bps=0.5, slippage_bps=0.5,
                            latency=LatencyModel.optimistic()),
    "realistic": CostModel(commission_bps=1.0, slippage_bps=2.0,
                           latency=LatencyModel.realistic()),
    "conservative": CostModel(commission_bps=2.0, slippage_bps=8.0,
                              latency=LatencyModel.conservative()),
}

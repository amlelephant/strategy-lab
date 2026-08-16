"""
lab/strategies/
===============
Every algorithm the platform knows about.

Adding one is a single file plus a single line here. Nothing else in the
repository needs to change — the CLI, the GUI's dropdown, the sweep runner and
the contract tests all read from the registry.

Read `AGENTS.md` before writing one; `buy_and_hold.py` is the shortest
complete example of the contract.
"""

from .bw_cross_sectional import BWCrossSectional
from .bw_valuation import BWValuation
from .buy_and_hold import BuyAndHold
from .mean_reversion import MeanReversion
from .stat_arb import StatArb
from .stat_arb_ev import StatArbEV

__all__ = [
    "BWCrossSectional", "BWValuation", "BuyAndHold", "MeanReversion",
    "StatArb", "StatArbEV",
]

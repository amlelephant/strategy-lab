"""
lab/strategies/
===============
Every algorithm the platform knows about.

Adding one is a single file plus a single line here. Nothing else in the
repository needs to change — the CLI, the GUI's dropdown, the sweep runner and
the contract tests all read from the registry.

Read `AGENTS.md` before writing one — §2 is a complete strategy you can copy.

Every file here ends with an `if __name__ == "__main__"` block, so
`python -m lab.strategies.<name>` backtests that one strategy. That is the
supported way to try a parameter value — see `lab/api.py`.
"""

import warnings

# `python -m lab.strategies.thing` imports this package first, which imports
# `thing`, and then runpy warns that it is about to execute a module already
# in `sys.modules`. That is exactly what we intend it to do, and the warning
# would otherwise be the first thing printed above every strategy's output.
# Registered here because this package is imported before runpy emits it.
warnings.filterwarnings(
    "ignore", category=RuntimeWarning,
    message=r".*found in sys\.modules after import of package.*")

from .bw_cross_sectional import BWCrossSectional
from .hundred_day_mov_avg import HundredDayMovAvg
from .mean_reversion import MeanReversion
from .stat_arb_ev import StatArbEV

# Two files are intentionally not imported here, and so are not registered —
# neither appears in the CLI, GUI or sweep runner. Both are unchanged and
# still runnable directly.
#
# BWValuation (fixed-anchor scoring, `lab/strategies/bw_valuation.py`):
# showing it beside `BWCrossSectional` put two versions of the same idea on
# the showcase. `python -m lab.strategies.bw_valuation` still works.
#
# StatArb (unscreened z-score pairs trade, `lab/strategies/stat_arb.py`):
# its cointegration screen was ported into `StatArbEV` below, which now runs
# both that screen and the EV gates, so it no longer needs a second,
# unfiltered strategy beside it on the showcase to be comparable against.
# `python -m lab.strategies.stat_arb` still works.
__all__ = [
    "BWCrossSectional", "HundredDayMovAvg",
    "MeanReversion", "StatArbEV",
]

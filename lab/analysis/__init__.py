"""
Analysis tools a strategy can call, but which are not strategies themselves.

These are the "is this relationship real" questions — the ones you ask *before*
deciding what to trade. Keeping them out of `strategies/` means a screen can be
shared, tested and reasoned about on its own, and means a strategy's file stays
about its decision rule.
"""

from .cointegration import (CointegrationResult, engle_granger, hurst,
                            prefilter, variance_ratio)

__all__ = ["CointegrationResult", "engle_granger", "hurst", "prefilter",
           "variance_ratio"]

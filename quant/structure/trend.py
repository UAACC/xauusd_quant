"""Trend confirmation by counting LL/LH (or HH/HL) events on swing structure.

The friend's H4 trend rule — "出现 3 个 lower lows" — counts events, not an
unbroken trailing chain: real H4 structure regularly prints counter-bounces
(a HL inside an otherwise descending sequence) that should NOT invalidate
the broader downtrend.

A "lower-low event" is any low-swing whose price is strictly less than the
immediately prior low-swing. ``count_lower_lows(swings, window=N)`` filters
to low-swings only and counts these transitions over the most recent ``N``
low-swings (or all of them when ``window`` is None).

``is_downtrend`` requires BOTH a sufficient number of LL events AND LH
events (highs and lows must agree); ``is_uptrend`` mirrors this for HH+HL.
The default window of 6 of each kind covers about three weeks of H4 macro
structure; ``min_count=3`` matches the friend's threshold.
"""

from __future__ import annotations

from quant.structure.swings import Swing


def _filter_prices(swings: list[Swing], kind: str, window: int | None) -> list[float]:
    prices = [s.price for s in swings if s.kind == kind]
    if window is not None:
        prices = prices[-window:]
    return prices


def _count_strict_transitions(prices: list[float], decreasing: bool) -> int:
    """Count of i where prices[i] < prices[i-1] (decreasing) or > (increasing)."""
    if len(prices) < 2:
        return 0
    out = 0
    for i in range(1, len(prices)):
        if decreasing and prices[i] < prices[i - 1]:
            out += 1
        elif (not decreasing) and prices[i] > prices[i - 1]:
            out += 1
    return out


def count_lower_lows(swings: list[Swing], window: int | None = None) -> int:
    """Number of low-swings whose price is strictly < the prior low-swing's.

    Args:
        swings: full swing list (will be filtered to lows).
        window: if given, only the most recent ``window`` low-swings are considered.
    """
    return _count_strict_transitions(_filter_prices(swings, "low", window), decreasing=True)


def count_lower_highs(swings: list[Swing], window: int | None = None) -> int:
    return _count_strict_transitions(_filter_prices(swings, "high", window), decreasing=True)


def count_higher_highs(swings: list[Swing], window: int | None = None) -> int:
    return _count_strict_transitions(_filter_prices(swings, "high", window), decreasing=False)


def count_higher_lows(swings: list[Swing], window: int | None = None) -> int:
    return _count_strict_transitions(_filter_prices(swings, "low", window), decreasing=False)


def is_downtrend(swings: list[Swing], min_count: int = 3, window: int | None = 6) -> bool:
    """True iff LL count AND LH count over the recent ``window`` both ≥ ``min_count``."""
    return (count_lower_lows(swings, window) >= min_count
            and count_lower_highs(swings, window) >= min_count)


def is_uptrend(swings: list[Swing], min_count: int = 3, window: int | None = 6) -> bool:
    return (count_higher_highs(swings, window) >= min_count
            and count_higher_lows(swings, window) >= min_count)

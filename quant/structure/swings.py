"""Fractal swing-high / swing-low detection on OHLC bars.

A bar at index ``i`` is a **swing high** when its ``high`` is strictly greater
than the highs of the ``n_left`` bars immediately before and the ``n_right``
bars immediately after it. Symmetric for swing lows on ``low``. The default
2-2 fractal yields a 5-bar window — the most common SMC convention.

The function returns a list of ``Swing`` records sorted by time, with the
bar's positional index, timestamp, price, and kind. Boundary bars (the
first ``n_left`` and last ``n_right``) cannot be swings and are skipped.

Strictness matters: we use ``>`` and ``<`` (not ``>=`` / ``<=``), so equal-
price plateaus do not register. This keeps swing counts stable when the
broker quotes flat regions, at the cost of missing swings whose neighbors
happen to tie. For SMC purposes this is the right trade-off.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np
import pandas as pd


SwingKind = Literal["high", "low"]


@dataclass(frozen=True)
class Swing:
    index: int
    time: pd.Timestamp
    price: float
    kind: SwingKind


def detect_swings(
    bars: pd.DataFrame,
    n_left: int = 2,
    n_right: int = 2,
) -> list[Swing]:
    """Return all fractal swings in ``bars`` as ``Swing`` records, time-sorted.

    Args:
        bars: OHLCV DataFrame; must contain ``time``, ``high``, ``low`` columns.
        n_left: bars to the left that must be strictly lower (highs) / higher (lows).
        n_right: bars to the right with the same condition.

    A bar's index is its row position in the input DataFrame (0-based).
    Boundary bars (first ``n_left`` and last ``n_right``) are never swings.
    Returns empty list if there are too few bars for any interior position.
    """
    if n_left < 1 or n_right < 1:
        raise ValueError(f"n_left and n_right must be >= 1; got {n_left}, {n_right}")
    n = len(bars)
    if n < n_left + n_right + 1:
        return []

    high = bars["high"].to_numpy()
    low = bars["low"].to_numpy()
    times = bars["time"].to_numpy()

    swings: list[Swing] = []
    for i in range(n_left, n - n_right):
        h_window_left = high[i - n_left : i]
        h_window_right = high[i + 1 : i + 1 + n_right]
        if high[i] > h_window_left.max() and high[i] > h_window_right.max():
            swings.append(Swing(index=i, time=pd.Timestamp(times[i]), price=float(high[i]), kind="high"))
            continue  # a bar can't be both high and low swing simultaneously
        l_window_left = low[i - n_left : i]
        l_window_right = low[i + 1 : i + 1 + n_right]
        if low[i] < l_window_left.min() and low[i] < l_window_right.min():
            swings.append(Swing(index=i, time=pd.Timestamp(times[i]), price=float(low[i]), kind="low"))

    return swings

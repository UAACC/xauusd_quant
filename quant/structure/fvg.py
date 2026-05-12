"""Fair Value Gap (3-bar price imbalance) detection.

Definitions
-----------
A **bullish FVG** appears when the impulse bar at ``i-1`` rallies hard
enough that bar ``i-2``'s high prints below bar ``i``'s low — the gap
``(bar[i-2].high, bar[i].low)`` was never traded through, leaving an
unfilled imbalance::

           bar[i]                  ┐
                                   │  upper_edge = bar[i].low
                       (FVG zone)  │
                                   │  lower_edge = bar[i-2].high
    bar[i-2]                       ┘
              bar[i-1]   (impulse)

A **bearish FVG** is the symmetric pattern when the impulse bar pushes
down so fast that bar ``i-2``'s low prints above bar ``i``'s high.

SMC use
-------
- A bullish FVG approached from above acts as support; the lower edge is
  the deeper target.
- A bearish FVG approached from below acts as resistance; the upper edge
  is the deeper target. (This is what the SMC strategy's TP candidate
  uses — friend's reference 4770 came from a bearish FVG upper edge.)

The detector returns every FVG in the bar sequence, in left-to-right
order. Filtering for "still unfilled at time T" or "above current price"
is done downstream by the strategy / TP-candidate logic.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import pandas as pd


FVGDirection = Literal["bullish", "bearish"]


@dataclass(frozen=True)
class FVG:
    index_left: int           # bar[i-2]
    index_right: int          # bar[i]
    time_left: pd.Timestamp
    time_right: pd.Timestamp
    lower_edge: float
    upper_edge: float
    direction: FVGDirection


def detect_fvgs(bars: pd.DataFrame) -> list[FVG]:
    """Scan all 3-bar windows; return every FVG in time order.

    Args:
        bars: OHLC DataFrame with ``time``, ``high``, ``low`` columns.

    Strict inequality: a bar with ``bar[i].low == bar[i-2].high`` does NOT
    create a bullish FVG (no gap to fill).
    """
    n = len(bars)
    if n < 3:
        return []

    high = bars["high"].to_numpy()
    low = bars["low"].to_numpy()
    times = bars["time"].to_numpy()

    fvgs: list[FVG] = []
    for i in range(2, n):
        # Bullish FVG: gap above bar[i-2].high, below bar[i].low
        if high[i - 2] < low[i]:
            fvgs.append(FVG(
                index_left=i - 2,
                index_right=i,
                time_left=pd.Timestamp(times[i - 2]),
                time_right=pd.Timestamp(times[i]),
                lower_edge=float(high[i - 2]),
                upper_edge=float(low[i]),
                direction="bullish",
            ))
        # Bearish FVG: gap below bar[i-2].low, above bar[i].high
        elif low[i - 2] > high[i]:
            fvgs.append(FVG(
                index_left=i - 2,
                index_right=i,
                time_left=pd.Timestamp(times[i - 2]),
                time_right=pd.Timestamp(times[i]),
                lower_edge=float(high[i]),
                upper_edge=float(low[i - 2]),
                direction="bearish",
            ))

    return fvgs

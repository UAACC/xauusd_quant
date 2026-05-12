"""Break-of-Structure detection on a bar series.

A BOS is the first bar that closes strictly past a structural level (a
prior swing high for an upside BOS, prior swing low for downside). The SMC
strategy uses two-stage BOS confirmation:

1. ``first_close_above(h4_bars, prior_lh)`` finds the candidate H4 BOS bar.
2. ``n_consecutive_closes_above(m15_bars, prior_lh, n=2, after_time=h4_bos_time)``
   confirms that M15 sustains above the level — guards against an H4 close
   that wicked above only because of a single M15 spike.

Symmetric ``_below`` variants serve the short-side BOS and the long-side
invalidation rule (after upside BOS, if H4 closes back BELOW the broken LH,
the setup is killed).

``after_time`` is **strict** (>): the bar at ``after_time`` itself is
excluded. Pass the timestamp of the last bar you want to ignore (typically
the H4 BOS bar's time when filtering M15 sustain).
"""

from __future__ import annotations

import pandas as pd


def _filter_after(bars: pd.DataFrame, after_time: pd.Timestamp | None) -> pd.DataFrame:
    if after_time is None:
        return bars
    return bars[bars["time"] > after_time]


def first_close_above(
    bars: pd.DataFrame,
    level: float,
    after_time: pd.Timestamp | None = None,
) -> int | None:
    """Index of the first bar (in original ``bars`` indexing) with ``close > level``.

    Returns ``None`` if no such bar exists. ``after_time`` is strict.
    """
    sub = _filter_after(bars, after_time)
    above = sub[sub["close"] > level]
    if above.empty:
        return None
    return int(above.index[0])


def first_close_below(
    bars: pd.DataFrame,
    level: float,
    after_time: pd.Timestamp | None = None,
) -> int | None:
    """Mirror of ``first_close_above`` for ``close < level``."""
    sub = _filter_after(bars, after_time)
    below = sub[sub["close"] < level]
    if below.empty:
        return None
    return int(below.index[0])


def n_consecutive_closes_above(
    bars: pd.DataFrame,
    level: float,
    n: int,
    after_time: pd.Timestamp | None = None,
) -> int | None:
    """Index of the bar that COMPLETES ``n`` consecutive closes > ``level``.

    A single close at or below ``level`` resets the running count. Returns
    ``None`` if the run is never completed in the (optionally filtered)
    series.
    """
    if n < 1:
        raise ValueError(f"n must be >= 1; got {n}")
    sub = _filter_after(bars, after_time)
    streak = 0
    for idx, row in sub.iterrows():
        if row["close"] > level:
            streak += 1
            if streak >= n:
                return int(idx)
        else:
            streak = 0
    return None


def n_consecutive_closes_below(
    bars: pd.DataFrame,
    level: float,
    n: int,
    after_time: pd.Timestamp | None = None,
) -> int | None:
    if n < 1:
        raise ValueError(f"n must be >= 1; got {n}")
    sub = _filter_after(bars, after_time)
    streak = 0
    for idx, row in sub.iterrows():
        if row["close"] < level:
            streak += 1
            if streak >= n:
                return int(idx)
        else:
            streak = 0
    return None

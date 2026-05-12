"""Retest detection: price returning to within proximity of a structural level.

After an upside BOS, the SMC strategy waits passively for price to come
back down to the broken swing-high (the new "support"). The retest is
identified when a bar's ``low`` falls within ``max_distance_pct`` of the
level — symmetric, so a low slightly below the level still counts (price
briefly broke through, then can recover). Beyond that tolerance, the move
is a real break (invalidation), not a retest.

Default tolerance 0.5% on a $4660 level ≈ ±$23 — comparable to the
strategy's $20 SL distance, so a "retest" is anything that could plausibly
become an entry. Strategy callers tune this per timeframe / volatility
regime.

The "hold" portion of the retest sequence (N consecutive closes above the
level immediately after the touch) is delegated to
``quant.structure.bos.n_consecutive_closes_above`` — same primitive serves
both BOS-sustain and retest-hold checks.
"""

from __future__ import annotations

import pandas as pd


def _filter_after(bars: pd.DataFrame, after_time: pd.Timestamp | None) -> pd.DataFrame:
    if after_time is None:
        return bars
    return bars[bars["time"] > after_time]


def first_low_within_pct(
    bars: pd.DataFrame,
    level: float,
    max_distance_pct: float,
    after_time: pd.Timestamp | None = None,
) -> int | None:
    """Index of the first bar whose ``low`` is within ``max_distance_pct`` of ``level``.

    Distance is symmetric: ``|low - level| / level * 100 <= max_distance_pct``.

    Use this for upside-BOS retest detection (price coming back down to the
    broken LH from above). Returns ``None`` if no such bar in the (filtered)
    series. ``after_time`` is strict (>).
    """
    if max_distance_pct < 0:
        raise ValueError(f"max_distance_pct must be >= 0; got {max_distance_pct}")
    if level == 0:
        raise ValueError("level must be non-zero")

    sub = _filter_after(bars, after_time)
    if sub.empty:
        return None

    abs_tol = abs(level) * max_distance_pct / 100.0
    diff = (sub["low"] - level).abs()
    matches = sub[diff <= abs_tol]
    if matches.empty:
        return None
    return int(matches.index[0])


def first_high_within_pct(
    bars: pd.DataFrame,
    level: float,
    max_distance_pct: float,
    after_time: pd.Timestamp | None = None,
) -> int | None:
    """Mirror of ``first_low_within_pct`` for downside-BOS retests (high
    approaching a broken swing low from below)."""
    if max_distance_pct < 0:
        raise ValueError(f"max_distance_pct must be >= 0; got {max_distance_pct}")
    if level == 0:
        raise ValueError("level must be non-zero")

    sub = _filter_after(bars, after_time)
    if sub.empty:
        return None

    abs_tol = abs(level) * max_distance_pct / 100.0
    diff = (sub["high"] - level).abs()
    matches = sub[diff <= abs_tol]
    if matches.empty:
        return None
    return int(matches.index[0])

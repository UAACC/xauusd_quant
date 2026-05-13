"""Wilder's ADX (Average Directional Index) — trend strength indicator.

ADX measures HOW STRONG a trend is regardless of direction. High ADX
(>= 25) = strong trend; low ADX (< 20) = ranging market.

Used to filter BOS-reversal signals: a structural break is most
meaningful at the end of a STRONG trend, not when the market is already
chopping sideways.

Reference: Wilder, J. (1978). New Concepts in Technical Trading Systems.
"""

from __future__ import annotations

import pandas as pd


def adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder's ADX over ``period`` bars. First ~2×period values are NaN."""
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)

    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = ((up_move > down_move) & (up_move > 0)) * up_move
    minus_dm = ((down_move > up_move) & (down_move > 0)) * down_move

    # Wilder smoothing = EMA with alpha = 1/period
    atr = tr.ewm(alpha=1.0 / period, adjust=False).mean()
    plus_di = 100 * plus_dm.ewm(alpha=1.0 / period, adjust=False).mean() / atr
    minus_di = 100 * minus_dm.ewm(alpha=1.0 / period, adjust=False).mean() / atr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, pd.NA)
    return dx.ewm(alpha=1.0 / period, adjust=False).mean()


def adx_at(bars: pd.DataFrame, t: pd.Timestamp, period: int = 14) -> float | None:
    """Compute ADX(period) using only bars strictly before ``t``."""
    prior = bars[bars["time"] < t]
    if len(prior) < 2 * period + 1:
        return None
    a = adx(prior["high"], prior["low"], prior["close"], period=period)
    last = a.dropna()
    if last.empty:
        return None
    return float(last.iloc[-1])

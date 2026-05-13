"""Wilder's Average True Range — volatility metric in price units.

Used by stops/targets that want to scale with current market volatility
instead of using a fixed dollar distance. A fixed $20 SL is "tight" on a
calm day and "loose" on a volatile one; ATR-scaled stops normalize this.

Reference: Wilder, J. (1978). New Concepts in Technical Trading Systems.
"""

from __future__ import annotations

import pandas as pd


def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder's ATR over ``period`` bars.

    Returns a Series aligned to ``close.index``. The first ``period`` values
    are NaN (need full window of true ranges to compute).
    """
    prev_close = close.shift(1)
    true_range = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return true_range.rolling(period, min_periods=period).mean()


def atr_at(bars: pd.DataFrame, t: pd.Timestamp, period: int = 14) -> float | None:
    """Compute ATR(period) using only bars strictly BEFORE ``t``.

    Returns ``None`` if there are not enough prior bars (need ``period + 1``
    bars before ``t`` to compute the first true range).
    """
    prior = bars[bars["time"] < t]
    if len(prior) < period + 1:
        return None
    a = atr(prior["high"], prior["low"], prior["close"], period=period)
    last = a.dropna()
    if last.empty:
        return None
    return float(last.iloc[-1])

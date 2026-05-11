"""
Bollinger-band mean-reversion scalper — classic retail strategy, used here
as a baseline / teaching example.

Rules (symmetric long+short):
  - Compute ``ma = close.rolling(period).mean()`` and
    ``sd = close.rolling(period).std()``.
  - Upper band = ``ma + num_std * sd``; lower band = ``ma - num_std * sd``.
  - Entry: close < lower -> LONG; close > upper -> SHORT.
  - Exit: close crosses back to ``ma`` (mean-reversion target), OR
          time-in-trade >= ``time_stop_bars``, OR
          adverse move >= ``atr_stop_mult * ATR(14)`` (catastrophic stop).

The position series is computed in a stateful one-pass loop because the
exit condition depends on entry price (for the ATR stop) and entry time
(for the time stop). For ~10^5 M1 bars this runs in well under a second.

Why this strategy specifically
------------------------------
It's the most-quoted retail "scalping" recipe and almost always shows a
positive gross PnL on calm markets — which makes it the perfect target for
demonstrating how the broker's cost stack eats the edge. After spread +
commission + slippage is deducted, the same strategy typically turns net
negative on liquid CFD markets. Use this as a sanity check that your cost
model is doing real work before testing any "real" strategy on top of it.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class BBParams:
    period: int = 20
    num_std: float = 2.0
    time_stop_bars: int = 30
    atr_period: int = 14
    atr_stop_mult: float = 1.5  # set None / 0 to disable

    def label(self) -> str:
        return f"BB({self.period},{self.num_std:g})_ts{self.time_stop_bars}_atr{self.atr_stop_mult:g}"


def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder's Average True Range."""
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(period, min_periods=period).mean()


def bb_position(
    bars: pd.DataFrame,
    params: BBParams = BBParams(),
) -> pd.DataFrame:
    """Run the BB mean-reversion state machine on a bar DataFrame.

    Returns a DataFrame with columns: ``ma``, ``upper``, ``lower``,
    ``signal`` (raw entry suggestion, -1/0/+1 each bar), and ``position``
    (the position actually held at the close of that bar, -1/0/+1).
    """
    close = bars["close"]
    high = bars["high"]
    low = bars["low"]

    ma = close.rolling(params.period, min_periods=params.period).mean()
    sd = close.rolling(params.period, min_periods=params.period).std()
    upper = ma + params.num_std * sd
    lower = ma - params.num_std * sd
    a = atr(high, low, close, params.atr_period)

    signal = np.where(close < lower, 1, np.where(close > upper, -1, 0)).astype("int8")

    n = len(close)
    pos = np.zeros(n, dtype="int8")
    cur = 0
    entry_i = -1
    entry_px = 0.0

    c_arr = close.to_numpy()
    ma_arr = ma.to_numpy()
    a_arr = a.to_numpy()
    sig_arr = signal

    use_atr_stop = params.atr_stop_mult is not None and params.atr_stop_mult > 0

    for i in range(n):
        # Skip until indicators are warm.
        if np.isnan(ma_arr[i]):
            pos[i] = 0
            continue

        if cur == 0:
            if sig_arr[i] == 1:
                cur, entry_i, entry_px = 1, i, c_arr[i]
            elif sig_arr[i] == -1:
                cur, entry_i, entry_px = -1, i, c_arr[i]
        else:
            c = c_arr[i]
            m = ma_arr[i]
            exit_now = False
            # Mean-reversion target hit
            if cur == 1 and c >= m:
                exit_now = True
            elif cur == -1 and c <= m:
                exit_now = True
            # Time stop
            if not exit_now and (i - entry_i) >= params.time_stop_bars:
                exit_now = True
            # ATR catastrophic stop
            if not exit_now and use_atr_stop and not np.isnan(a_arr[i]):
                stop_dist = params.atr_stop_mult * a_arr[i]
                if cur == 1 and c <= entry_px - stop_dist:
                    exit_now = True
                elif cur == -1 and c >= entry_px + stop_dist:
                    exit_now = True
            if exit_now:
                cur, entry_i, entry_px = 0, -1, 0.0
        pos[i] = cur

    out = pd.DataFrame({
        "ma": ma,
        "upper": upper,
        "lower": lower,
        "signal": pd.Series(signal, index=close.index),
        "position": pd.Series(pos, index=close.index),
    })
    return out

"""Analyst-mode utilities — quantitative features built on TA-Lib + numpy.

Provides the "5-axis institutional analyst" toolkit beyond raw OHLCV:
- Z-score / std-dev distance from moving averages (TA-Lib MAs)
- Range position within N-day window
- Synthetic DXY (USD strength proxy from available majors)
- Volatility regime classifier (ATR percentile rank)
- Bollinger-band-style envelope distance
- RSI + MACD divergence detection (against price extrema)

All inputs are pandas DataFrames with the canonical bar schema
(time, open, high, low, close, tick_volume). All outputs are pure values
(floats / dicts) — no side effects, no MT5 calls. The MT5 layer is in
``eval_trade.pull_live_context``; this module only crunches numbers.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd
import talib


# ---------------------------------------------------------------------------
# Z-score / distance utilities
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ZScoreReading:
    value: float
    mean: float
    std: float
    z: float
    percentile: float  # empirical percentile within the lookback window


def zscore(series: pd.Series, lookback: int = 50, value: Optional[float] = None) -> ZScoreReading:
    """Z-score of ``value`` (default: last point) vs the prior ``lookback`` window.

    Returns the value, mean, std, z-score, and empirical percentile. Z > 2 (or < -2)
    is the institutional ~5%-tail threshold. Percentile is a non-parametric
    fallback when the underlying distribution isn't normal.
    """
    s = series.dropna()
    if len(s) < lookback + 1:
        raise ValueError(f"need at least {lookback + 1} points, got {len(s)}")
    window = s.iloc[-lookback - 1:-1]  # last `lookback` points before current
    val = float(value) if value is not None else float(s.iloc[-1])
    mu = float(window.mean())
    sigma = float(window.std(ddof=1))
    z = (val - mu) / sigma if sigma > 0 else 0.0
    pct = float((window < val).sum() / len(window)) * 100
    return ZScoreReading(value=val, mean=mu, std=sigma, z=z, percentile=pct)


def std_distance_from_ma(
    closes: pd.Series, period: int = 20, ma_type: str = "EMA", lookback_for_std: int = 50,
) -> dict:
    """Distance of current close from moving average, measured in std-dev units.

    ma_type ∈ {"EMA", "SMA"}; uses TA-Lib for the MA computation. The std-dev
    is computed on the residual (close - MA) over the trailing ``lookback_for_std``
    bars, giving an adaptive band whose width tracks recent realized vol.

    Returns dict with: ma, current_close, distance, std_resid, z.
    """
    closes = closes.dropna().astype(float)
    arr = closes.to_numpy()
    if len(arr) < max(period, lookback_for_std) + 5:
        raise ValueError(f"need >= {max(period, lookback_for_std) + 5} bars")
    if ma_type.upper() == "EMA":
        ma = talib.EMA(arr, timeperiod=period)
    elif ma_type.upper() == "SMA":
        ma = talib.SMA(arr, timeperiod=period)
    else:
        raise ValueError(f"ma_type must be EMA or SMA, got {ma_type!r}")
    residual = arr - ma
    last_close = float(arr[-1])
    last_ma = float(ma[-1])
    last_resid = last_close - last_ma
    resid_window = residual[-lookback_for_std - 1:-1]
    resid_window = resid_window[~np.isnan(resid_window)]
    sigma = float(np.std(resid_window, ddof=1)) if len(resid_window) > 1 else 0.0
    z = last_resid / sigma if sigma > 0 else 0.0
    return {
        "ma": last_ma,
        "current_close": last_close,
        "distance": last_resid,
        "std_resid": sigma,
        "z": z,
    }


# ---------------------------------------------------------------------------
# Range position
# ---------------------------------------------------------------------------

def range_position(bars: pd.DataFrame, lookback_bars: int) -> dict:
    """Where in the rolling N-bar range is current close (0% = at low, 100% = at high).

    Useful across TFs: "Current at 78% of last 60 H4 bars range" tells you the
    setup is closer to a top than a bottom in objective terms.
    """
    win = bars.tail(lookback_bars)
    if len(win) < 2:
        raise ValueError("not enough bars for range_position")
    high = float(win["high"].max())
    low = float(win["low"].min())
    current = float(bars["close"].iloc[-1])
    pct = (current - low) / (high - low) * 100 if high > low else 50.0
    return {
        "lookback_bars": lookback_bars,
        "high": high,
        "low": low,
        "current": current,
        "range_pct": pct,
        "range_width": high - low,
    }


# ---------------------------------------------------------------------------
# Synthetic DXY (since IC Markets has no DXY ticker)
# ---------------------------------------------------------------------------

# Original ICE DXY weights (1973 baseline):
#   EUR 57.6%, JPY 13.6%, GBP 11.9%, CAD 9.1%, SEK 4.2%, CHF 3.6%
# We have EURUSD, USDJPY, GBPUSD, USDCHF on IC Markets demo.
# Re-normalize to the ~86.7% we can cover. USDCAD / USDSEK are missing — we
# accept the ~13% representation gap. For our use case (USD direction signal,
# not absolute level), this proxy moves directionally with real DXY.

_DXY_PROXY_WEIGHTS = {
    "EURUSD": -0.576 / 0.867,  # invert (EURUSD up = USD down)
    "USDJPY": +0.136 / 0.867,
    "GBPUSD": -0.119 / 0.867,
    "USDCHF": +0.036 / 0.867,
}


def synthetic_dxy(midprices: dict[str, float]) -> Optional[float]:
    """USD-strength proxy from major-pair midprices. Higher = USD stronger.

    Output is a log-weighted index, anchored such that current cross-section
    gives a non-arbitrary number you can track relative-change against. The
    *absolute* value isn't comparable to real DXY (no calibration anchor) —
    use ``synthetic_dxy_change_pct`` for inter-temporal comparison.

    Returns None if any required price is missing or non-positive.
    """
    log_sum = 0.0
    for sym, weight in _DXY_PROXY_WEIGHTS.items():
        p = midprices.get(sym)
        if p is None or p <= 0:
            return None
        log_sum += weight * math.log(p)
    # Anchor at a constant; the raw output is arbitrary, but relative changes
    # are meaningful. Multiply by 100 to keep magnitude in DXY-like range.
    return math.exp(log_sum) * 100


def synthetic_dxy_series(bars_by_symbol: dict[str, pd.DataFrame], tf_field: str = "close") -> pd.Series:
    """Build a synthetic DXY time series from D1 (or any TF) bars of the 4 majors.

    bars_by_symbol must include EURUSD, USDJPY, GBPUSD, USDCHF with aligned
    ``time`` indexes. Returns a Series indexed by time with the proxy value.
    """
    required = list(_DXY_PROXY_WEIGHTS.keys())
    missing = [s for s in required if s not in bars_by_symbol]
    if missing:
        raise ValueError(f"missing bars for {missing}")

    # Align on time
    merged = bars_by_symbol[required[0]][["time", tf_field]].rename(columns={tf_field: required[0]})
    for sym in required[1:]:
        merged = merged.merge(
            bars_by_symbol[sym][["time", tf_field]].rename(columns={tf_field: sym}),
            on="time", how="inner",
        )
    out_values = []
    for _, row in merged.iterrows():
        midprices = {sym: float(row[sym]) for sym in required}
        out_values.append(synthetic_dxy(midprices))
    return pd.Series(out_values, index=merged["time"], name="dxy_proxy")


# ---------------------------------------------------------------------------
# Volatility regime classifier
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class VolRegime:
    atr_current: float
    atr_p25: float
    atr_p50: float
    atr_p75: float
    atr_p90: float
    percentile: float
    label: str  # LOW / NORMAL / ELEVATED / EXTREME


def vol_regime(bars: pd.DataFrame, period: int = 14, lookback: int = 200) -> VolRegime:
    """Classify current ATR regime against its trailing distribution.

    ATR computed via TA-Lib (Wilder method). Percentile rank against trailing
    ``lookback`` bars. Labels:
        < 25th  -> LOW       (compressed, breakout pending)
        25-75   -> NORMAL    (regime baseline)
        75-90   -> ELEVATED  (above-average risk)
        > 90    -> EXTREME   (climax / panic / news)
    """
    h = bars["high"].astype(float).to_numpy()
    l = bars["low"].astype(float).to_numpy()
    c = bars["close"].astype(float).to_numpy()
    atr = talib.ATR(h, l, c, timeperiod=period)
    series = pd.Series(atr).dropna()
    if len(series) < lookback + 1:
        lookback = max(20, len(series) - 1)
    window = series.iloc[-lookback - 1:-1]
    current = float(series.iloc[-1])
    p25 = float(window.quantile(0.25))
    p50 = float(window.quantile(0.50))
    p75 = float(window.quantile(0.75))
    p90 = float(window.quantile(0.90))
    pct = float((window < current).sum() / len(window)) * 100
    if pct < 25:
        label = "LOW"
    elif pct < 75:
        label = "NORMAL"
    elif pct < 90:
        label = "ELEVATED"
    else:
        label = "EXTREME"
    return VolRegime(
        atr_current=current, atr_p25=p25, atr_p50=p50, atr_p75=p75, atr_p90=p90,
        percentile=pct, label=label,
    )


# ---------------------------------------------------------------------------
# Bollinger envelope distance
# ---------------------------------------------------------------------------

def bollinger_distance(closes: pd.Series, period: int = 20, num_std: float = 2.0) -> dict:
    """Bollinger band reading on the close series. Uses TA-Lib BBANDS.

    Returns: upper, middle, lower, current, pct_from_middle, pct_b
    (pct_b is the canonical %B = (price - lower) / (upper - lower), > 1 means
    above upper band, < 0 below lower).
    """
    arr = closes.astype(float).to_numpy()
    if len(arr) < period + 1:
        raise ValueError(f"need >= {period + 1} closes")
    upper, middle, lower = talib.BBANDS(
        arr, timeperiod=period, nbdevup=num_std, nbdevdn=num_std, matype=0,
    )
    last = float(arr[-1])
    u, m, lo = float(upper[-1]), float(middle[-1]), float(lower[-1])
    pct_b = (last - lo) / (u - lo) if u > lo else 0.5
    pct_from_mid = (last - m) / m * 100 if m > 0 else 0.0
    return {
        "upper": u, "middle": m, "lower": lo,
        "current": last,
        "pct_b": pct_b,
        "pct_from_middle": pct_from_mid,
        "above_upper": last > u,
        "below_lower": last < lo,
    }


# ---------------------------------------------------------------------------
# RSI + MACD divergence detection
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Divergence:
    kind: str  # "bullish" | "bearish" | "hidden_bullish" | "hidden_bearish"
    indicator: str  # "RSI" | "MACD"
    price_extremes: tuple[float, float]  # (earlier, later)
    indicator_extremes: tuple[float, float]
    earlier_bar_idx: int
    later_bar_idx: int


def find_divergences(
    bars: pd.DataFrame,
    indicator: str = "RSI",
    rsi_period: int = 14,
    macd_fast: int = 12, macd_slow: int = 26, macd_signal: int = 9,
    pivot_n: int = 5,
    lookback_bars: int = 100,
) -> list[Divergence]:
    """Detect RSI or MACD divergences vs price within the last N bars.

    "Regular" divergences (the trade-relevant kind):
      Bearish: price makes higher high, indicator makes lower high  -> reversal warning
      Bullish: price makes lower low,  indicator makes higher low   -> reversal warning

    "Hidden" (continuation) divergences:
      Hidden bearish: price makes lower high, indicator makes higher high  -> trend continuation down
      Hidden bullish: price makes higher low, indicator makes lower low    -> trend continuation up

    pivot_n controls how many bars on each side must be lower/higher to mark a pivot.
    """
    closes = bars["close"].astype(float).to_numpy()
    highs = bars["high"].astype(float).to_numpy()
    lows = bars["low"].astype(float).to_numpy()
    if indicator.upper() == "RSI":
        ind = talib.RSI(closes, timeperiod=rsi_period)
        ind_name = "RSI"
    elif indicator.upper() == "MACD":
        macd, signal, hist = talib.MACD(closes, fastperiod=macd_fast, slowperiod=macd_slow, signalperiod=macd_signal)
        ind = hist  # MACD histogram is the canonical divergence-source
        ind_name = "MACD"
    else:
        raise ValueError(f"indicator must be RSI or MACD, got {indicator!r}")

    n = len(closes)
    start = max(0, n - lookback_bars)

    def is_pivot_high(arr, i, w):
        if i < w or i >= len(arr) - w:
            return False
        if np.isnan(arr[i]):
            return False
        left = arr[i - w:i]
        right = arr[i + 1:i + 1 + w]
        return all(arr[i] > x for x in left if not np.isnan(x)) and all(arr[i] > x for x in right if not np.isnan(x))

    def is_pivot_low(arr, i, w):
        if i < w or i >= len(arr) - w:
            return False
        if np.isnan(arr[i]):
            return False
        left = arr[i - w:i]
        right = arr[i + 1:i + 1 + w]
        return all(arr[i] < x for x in left if not np.isnan(x)) and all(arr[i] < x for x in right if not np.isnan(x))

    price_highs = [i for i in range(start, n) if is_pivot_high(highs, i, pivot_n)]
    price_lows = [i for i in range(start, n) if is_pivot_low(lows, i, pivot_n)]

    divergences: list[Divergence] = []
    # Bearish divergence: latest two price highs (a, b) with b_price > a_price
    # but indicator_at_b < indicator_at_a
    for j in range(1, len(price_highs)):
        a, b = price_highs[j - 1], price_highs[j]
        if highs[b] > highs[a] and ind[b] < ind[a]:
            divergences.append(Divergence(
                kind="bearish", indicator=ind_name,
                price_extremes=(float(highs[a]), float(highs[b])),
                indicator_extremes=(float(ind[a]), float(ind[b])),
                earlier_bar_idx=a, later_bar_idx=b,
            ))
        elif highs[b] < highs[a] and ind[b] > ind[a]:
            divergences.append(Divergence(
                kind="hidden_bearish", indicator=ind_name,
                price_extremes=(float(highs[a]), float(highs[b])),
                indicator_extremes=(float(ind[a]), float(ind[b])),
                earlier_bar_idx=a, later_bar_idx=b,
            ))
    # Bullish divergence: latest two price lows (a, b) with b_price < a_price
    # but indicator_at_b > indicator_at_a
    for j in range(1, len(price_lows)):
        a, b = price_lows[j - 1], price_lows[j]
        if lows[b] < lows[a] and ind[b] > ind[a]:
            divergences.append(Divergence(
                kind="bullish", indicator=ind_name,
                price_extremes=(float(lows[a]), float(lows[b])),
                indicator_extremes=(float(ind[a]), float(ind[b])),
                earlier_bar_idx=a, later_bar_idx=b,
            ))
        elif lows[b] > lows[a] and ind[b] < ind[a]:
            divergences.append(Divergence(
                kind="hidden_bullish", indicator=ind_name,
                price_extremes=(float(lows[a]), float(lows[b])),
                indicator_extremes=(float(ind[a]), float(ind[b])),
                earlier_bar_idx=a, later_bar_idx=b,
            ))
    return divergences


# ---------------------------------------------------------------------------
# Volume Profile (price-by-volume)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class VolumeProfile:
    poc: float            # Point of Control (highest-volume price)
    vah: float            # Value Area High (70% of volume upper bound)
    val: float            # Value Area Low (70% of volume lower bound)
    bin_edges: np.ndarray
    bin_volumes: np.ndarray


def volume_profile(bars: pd.DataFrame, num_bins: int = 50, value_area_pct: float = 70.0) -> VolumeProfile:
    """Compute POC / VAH / VAL from OHLCV bars (uses tick_volume as the proxy).

    Approximation: each bar's volume is distributed uniformly across its [low, high]
    range. POC = price bin with the most volume. Value Area = the ``value_area_pct``%
    of total volume spread symmetrically around the POC.
    """
    if bars.empty:
        raise ValueError("empty bars")
    overall_low = float(bars["low"].min())
    overall_high = float(bars["high"].max())
    if overall_high <= overall_low:
        raise ValueError("flat range, can't compute profile")

    edges = np.linspace(overall_low, overall_high, num_bins + 1)
    bin_vol = np.zeros(num_bins)

    for _, b in bars.iterrows():
        bar_low = float(b["low"])
        bar_high = float(b["high"])
        bar_vol = float(b["tick_volume"])
        # distribute this bar's volume across the bins its range spans
        lo_idx = max(0, int((bar_low - overall_low) / (overall_high - overall_low) * num_bins))
        hi_idx = min(num_bins - 1, int((bar_high - overall_low) / (overall_high - overall_low) * num_bins))
        span = max(1, hi_idx - lo_idx + 1)
        per_bin = bar_vol / span
        for k in range(lo_idx, hi_idx + 1):
            bin_vol[k] += per_bin

    poc_idx = int(np.argmax(bin_vol))
    poc_price = (edges[poc_idx] + edges[poc_idx + 1]) / 2

    # Value Area: expand from POC outward until we capture value_area_pct% of total volume
    total_vol = bin_vol.sum()
    target_vol = total_vol * (value_area_pct / 100)
    captured = bin_vol[poc_idx]
    upper, lower = poc_idx, poc_idx
    while captured < target_vol and (upper < num_bins - 1 or lower > 0):
        next_up = bin_vol[upper + 1] if upper < num_bins - 1 else -1
        next_down = bin_vol[lower - 1] if lower > 0 else -1
        if next_up >= next_down:
            upper += 1
            captured += next_up
        else:
            lower -= 1
            captured += next_down
    vah = edges[upper + 1]
    val_ = edges[lower]
    return VolumeProfile(
        poc=poc_price, vah=float(vah), val=float(val_),
        bin_edges=edges, bin_volumes=bin_vol,
    )

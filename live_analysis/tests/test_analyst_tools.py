"""Tests for live_analysis.analyst_tools — TA-Lib-backed analytical utilities."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from live_analysis.analyst_tools import (
    bollinger_distance,
    find_divergences,
    range_position,
    std_distance_from_ma,
    synthetic_dxy,
    synthetic_dxy_series,
    vol_regime,
    volume_profile,
    zscore,
)


# ---------------------------------------------------------------------------
# zscore
# ---------------------------------------------------------------------------

def test_zscore_known_normal_series() -> None:
    rng = np.random.default_rng(42)
    s = pd.Series(rng.normal(loc=100, scale=5, size=200))
    # Append a known outlier
    s = pd.concat([s, pd.Series([130.0])], ignore_index=True)
    z = zscore(s, lookback=200)
    assert z.value == 130.0
    assert 5 < z.z < 7  # 30 / ~5 std = ~6σ
    assert z.percentile == 100  # max of window


def test_zscore_value_at_mean_gives_zero() -> None:
    s = pd.Series(np.ones(100) * 50.0)
    # Append same value
    s = pd.concat([s, pd.Series([50.0])], ignore_index=True)
    z = zscore(s, lookback=100)
    # std is 0 -> z is 0 by convention
    assert z.z == 0.0


def test_zscore_raises_when_too_few_points() -> None:
    s = pd.Series([1, 2, 3])
    with pytest.raises(ValueError, match="need at least"):
        zscore(s, lookback=10)


# ---------------------------------------------------------------------------
# std_distance_from_ma
# ---------------------------------------------------------------------------

def test_std_distance_uses_talib_ema() -> None:
    rng = np.random.default_rng(7)
    closes = pd.Series(100 + np.cumsum(rng.normal(0, 0.5, size=100)))
    result = std_distance_from_ma(closes, period=20, ma_type="EMA", lookback_for_std=50)
    assert "ma" in result
    assert "z" in result
    assert isinstance(result["z"], float)
    # z should be a finite number
    assert math.isfinite(result["z"])


def test_std_distance_invalid_ma_type() -> None:
    closes = pd.Series(range(100), dtype=float)
    with pytest.raises(ValueError, match="ma_type"):
        std_distance_from_ma(closes, ma_type="BANANA")


# ---------------------------------------------------------------------------
# range_position
# ---------------------------------------------------------------------------

def test_range_position_at_high() -> None:
    bars = pd.DataFrame({
        "time": pd.date_range("2026-01-01", periods=10, freq="h", tz="UTC"),
        "open": np.linspace(100, 110, 10),
        "high": np.linspace(101, 112, 10),
        "low": np.linspace(99, 108, 10),
        "close": np.linspace(100, 112, 10),
    })
    rp = range_position(bars, lookback_bars=10)
    # close at 112 vs high 112, low 99 -> at 100% of range
    assert rp["range_pct"] == 100.0


def test_range_position_at_midpoint() -> None:
    bars = pd.DataFrame({
        "time": pd.date_range("2026-01-01", periods=5, freq="h", tz="UTC"),
        "open": [100, 95, 105, 110, 90],
        "high": [105, 100, 110, 115, 95],
        "low": [95, 90, 100, 105, 85],
        "close": [100, 100, 100, 100, 100],  # all at 100, mid of 85-115
    })
    rp = range_position(bars, lookback_bars=5)
    # current 100, low 85, high 115 -> 50%
    assert 49 <= rp["range_pct"] <= 51


# ---------------------------------------------------------------------------
# synthetic_dxy
# ---------------------------------------------------------------------------

def test_synthetic_dxy_returns_finite_positive() -> None:
    midprices = {
        "EURUSD": 1.0850,
        "USDJPY": 154.50,
        "GBPUSD": 1.2700,
        "USDCHF": 0.9050,
    }
    val = synthetic_dxy(midprices)
    assert val is not None
    assert val > 0
    assert math.isfinite(val)


def test_synthetic_dxy_returns_none_if_missing_pair() -> None:
    midprices = {"EURUSD": 1.08, "USDJPY": 154}  # missing GBP and CHF
    assert synthetic_dxy(midprices) is None


def test_synthetic_dxy_direction_inverse_of_eur() -> None:
    """Higher EURUSD (weaker USD) should give lower DXY value."""
    base = {"EURUSD": 1.10, "USDJPY": 150, "GBPUSD": 1.27, "USDCHF": 0.90}
    weaker_usd = base | {"EURUSD": 1.15}
    stronger_usd = base | {"EURUSD": 1.05}
    assert synthetic_dxy(stronger_usd) > synthetic_dxy(base) > synthetic_dxy(weaker_usd)


def test_synthetic_dxy_series_from_aligned_bars() -> None:
    dates = pd.date_range("2026-01-01", periods=10, freq="D", tz="UTC")
    bars = {
        "EURUSD": pd.DataFrame({"time": dates, "close": np.linspace(1.08, 1.10, 10)}),
        "USDJPY": pd.DataFrame({"time": dates, "close": np.linspace(154, 156, 10)}),
        "GBPUSD": pd.DataFrame({"time": dates, "close": np.linspace(1.27, 1.29, 10)}),
        "USDCHF": pd.DataFrame({"time": dates, "close": np.linspace(0.90, 0.92, 10)}),
    }
    series = synthetic_dxy_series(bars)
    assert len(series) == 10
    assert all(math.isfinite(v) for v in series.values)


# ---------------------------------------------------------------------------
# vol_regime
# ---------------------------------------------------------------------------

def test_vol_regime_classifies_normal() -> None:
    rng = np.random.default_rng(0)
    n = 300
    closes = 100 + np.cumsum(rng.normal(0, 0.3, n))
    bars = pd.DataFrame({
        "time": pd.date_range("2026-01-01", periods=n, freq="h", tz="UTC"),
        "open": closes - 0.1, "high": closes + 0.5, "low": closes - 0.5, "close": closes,
        "tick_volume": [100] * n,
    })
    vr = vol_regime(bars, period=14, lookback=200)
    assert vr.label in ("LOW", "NORMAL", "ELEVATED", "EXTREME")
    assert 0 <= vr.percentile <= 100


def test_vol_regime_extreme_on_recent_spike() -> None:
    rng = np.random.default_rng(0)
    n = 250
    # Mostly calm series
    closes = 100 + np.cumsum(rng.normal(0, 0.1, n))
    highs = closes + 0.3
    lows = closes - 0.3
    # Append giant spike at end
    closes = np.concatenate([closes, [100.0]])
    highs = np.concatenate([highs, [110.0]])
    lows = np.concatenate([lows, [90.0]])
    bars = pd.DataFrame({
        "time": pd.date_range("2026-01-01", periods=n + 1, freq="h", tz="UTC"),
        "open": closes - 0.1, "high": highs, "low": lows, "close": closes,
        "tick_volume": [100] * (n + 1),
    })
    vr = vol_regime(bars, period=14, lookback=200)
    assert vr.label == "EXTREME"
    assert vr.percentile > 90


# ---------------------------------------------------------------------------
# bollinger_distance
# ---------------------------------------------------------------------------

def test_bollinger_pct_b_in_unit_range_when_in_band() -> None:
    rng = np.random.default_rng(0)
    closes = pd.Series(100 + np.cumsum(rng.normal(0, 0.2, 100)))
    bb = bollinger_distance(closes, period=20)
    # Most random walks will be inside the band ⇒ 0 <= %B <= 1
    assert -0.5 < bb["pct_b"] < 1.5
    assert bb["lower"] < bb["middle"] < bb["upper"]


def test_bollinger_above_upper_band_when_spike() -> None:
    closes = pd.Series([100.0] * 30 + [115.0])  # spike at end
    bb = bollinger_distance(closes, period=20)
    assert bb["above_upper"]
    assert bb["pct_b"] > 1.0


# ---------------------------------------------------------------------------
# find_divergences
# ---------------------------------------------------------------------------

def test_find_divergences_returns_list() -> None:
    rng = np.random.default_rng(0)
    n = 200
    closes = 100 + np.cumsum(rng.normal(0, 0.5, n))
    bars = pd.DataFrame({
        "time": pd.date_range("2026-01-01", periods=n, freq="h", tz="UTC"),
        "open": closes, "high": closes + 0.5, "low": closes - 0.5, "close": closes,
        "tick_volume": [100] * n,
    })
    divs = find_divergences(bars, indicator="RSI", lookback_bars=100)
    assert isinstance(divs, list)
    for d in divs:
        assert d.kind in ("bullish", "bearish", "hidden_bullish", "hidden_bearish")
        assert d.indicator == "RSI"


def test_find_divergences_invalid_indicator() -> None:
    bars = pd.DataFrame({
        "time": pd.date_range("2026-01-01", periods=100, freq="h", tz="UTC"),
        "open": [100.0] * 100, "high": [101.0] * 100, "low": [99.0] * 100,
        "close": [100.0] * 100, "tick_volume": [100] * 100,
    })
    with pytest.raises(ValueError, match="indicator must be"):
        find_divergences(bars, indicator="BANANA")


# ---------------------------------------------------------------------------
# volume_profile
# ---------------------------------------------------------------------------

def test_volume_profile_poc_at_concentration() -> None:
    """Plant most volume at price 100, verify POC is near 100."""
    n = 100
    bars = pd.DataFrame({
        "time": pd.date_range("2026-01-01", periods=n, freq="h", tz="UTC"),
        "open": [100.0] * n,
        "high": [101.0] * n,
        "low": [99.0] * n,
        "close": [100.0] * n,
        "tick_volume": [1000] * n,  # all volume in tight 99-101 range
    })
    # Add one outlier bar with much wider range but same volume
    bars = pd.concat([bars, pd.DataFrame([{
        "time": pd.Timestamp("2026-01-15", tz="UTC"),
        "open": 100.0, "high": 120.0, "low": 80.0, "close": 100.0, "tick_volume": 1000,
    }])], ignore_index=True)
    vp = volume_profile(bars, num_bins=40)
    # POC should be inside the 99-101 cluster, not in the 80-120 outlier zone
    assert 98 < vp.poc < 102


def test_volume_profile_value_area_contains_poc() -> None:
    n = 100
    bars = pd.DataFrame({
        "time": pd.date_range("2026-01-01", periods=n, freq="h", tz="UTC"),
        "open": np.linspace(95, 105, n),
        "high": np.linspace(96, 106, n),
        "low": np.linspace(94, 104, n),
        "close": np.linspace(95, 105, n),
        "tick_volume": [100] * n,
    })
    vp = volume_profile(bars, num_bins=30, value_area_pct=70)
    assert vp.val <= vp.poc <= vp.vah

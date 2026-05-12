"""Tests for fractal swing detection."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from quant.structure.swings import Swing, detect_swings


def _bars(highs, lows) -> pd.DataFrame:
    n = len(highs)
    return pd.DataFrame({
        "time": pd.date_range("2026-01-01", periods=n, freq="4h", tz="UTC"),
        "open": highs,
        "high": highs,
        "low": lows,
        "close": highs,
        "tick_volume": [100] * n,
    })


# ---------------------------------------------------------------------------
# Synthetic shape tests
# ---------------------------------------------------------------------------

def test_single_peak_5_bar_window():
    """A single peak in a 5-bar 2-2 window should be detected as one swing high."""
    bars = _bars(
        highs=[1, 2, 5, 2, 1],
        lows=[1, 2, 5, 2, 1],
    )
    swings = detect_swings(bars, n_left=2, n_right=2)
    assert len(swings) == 1
    assert swings[0].kind == "high"
    assert swings[0].index == 2
    assert swings[0].price == 5


def test_single_trough_5_bar_window():
    bars = _bars(
        highs=[5, 4, 1, 4, 5],
        lows=[5, 4, 1, 4, 5],
    )
    swings = detect_swings(bars, n_left=2, n_right=2)
    assert len(swings) == 1
    assert swings[0].kind == "low"
    assert swings[0].index == 2
    assert swings[0].price == 1


def test_flat_series_returns_no_swings():
    """Equal-price plateau should not register (strict > comparison)."""
    bars = _bars([5] * 10, [3] * 10)
    swings = detect_swings(bars, n_left=2, n_right=2)
    assert swings == []


def test_alternating_zigzag():
    """A clean zigzag should yield alternating highs and lows."""
    highs = [1, 3, 1, 3, 1, 3, 1]
    lows = [1, 3, 1, 3, 1, 3, 1]
    bars = _bars(highs, lows)
    # 1-1 fractal: bars 1, 2, 3, 4, 5 are all interior; alternating extrema
    swings = detect_swings(bars, n_left=1, n_right=1)
    kinds = [s.kind for s in swings]
    assert kinds == ["high", "low", "high", "low", "high"]


def test_too_few_bars_returns_empty():
    bars = _bars([1, 2, 3], [1, 2, 3])
    assert detect_swings(bars, n_left=2, n_right=2) == []


def test_boundary_bars_skipped():
    """First n_left and last n_right bars cannot be swings (no neighbors)."""
    # Peak at index 0 has no left neighbors -> not a swing
    bars = _bars([10, 1, 1, 1, 1, 1, 1], [10, 1, 1, 1, 1, 1, 1])
    swings = detect_swings(bars, n_left=2, n_right=2)
    assert swings == []


def test_wider_fractal_filters_smaller_swings():
    """A 3-3 fractal demands wider context than 1-1, so it returns fewer swings."""
    highs = [1, 3, 1, 5, 1, 3, 1, 5, 1, 3, 1]
    lows = highs[:]
    bars = _bars(highs, lows)
    s1 = detect_swings(bars, n_left=1, n_right=1)
    s3 = detect_swings(bars, n_left=3, n_right=3)
    assert len(s3) < len(s1)


def test_swing_carries_time_from_bar():
    bars = _bars([1, 2, 5, 2, 1], [1, 2, 5, 2, 1])
    swings = detect_swings(bars, n_left=2, n_right=2)
    assert swings[0].time == bars.loc[2, "time"]


# ---------------------------------------------------------------------------
# Real-data ground truth tests (the May 4 / May 1 SMC setup)
# ---------------------------------------------------------------------------

def test_apr_may_h4_finds_capitulation_low(smc_h4_apr_may):
    """The May 4 13:00 UTC bar (L=4500.72, vol=268283) must be detected
    as a swing low under the standard 2-2 fractal."""
    swings = detect_swings(smc_h4_apr_may, n_left=2, n_right=2)
    target_time = pd.Timestamp("2026-05-04 13:00:00", tz="UTC")
    matches = [s for s in swings if s.kind == "low" and s.time == target_time]
    assert len(matches) == 1, f"May 4 13:00 UTC capitulation low not detected; found {len(matches)} matching swings"
    assert abs(matches[0].price - 4500.72) < 0.01


def test_apr_may_h4_finds_may1_pinbar_high(smc_h4_apr_may):
    """The May 1 13:00 UTC bar (H=4660.32) must be detected as a swing high.
    This is the LH that BOS will eventually break."""
    swings = detect_swings(smc_h4_apr_may, n_left=2, n_right=2)
    target_time = pd.Timestamp("2026-05-01 13:00:00", tz="UTC")
    matches = [s for s in swings if s.kind == "high" and s.time == target_time]
    assert len(matches) == 1
    assert abs(matches[0].price - 4660.32) < 0.01


def test_apr_may_h4_finds_apr29_prior_low(smc_h4_apr_may):
    """The Apr 29 13:00 UTC bar (L=4510.29) must be detected as a swing low.
    The May 4 capitulation low is compared against this 'prior low' by the
    failed-breakdown logic."""
    swings = detect_swings(smc_h4_apr_may, n_left=2, n_right=2)
    target_time = pd.Timestamp("2026-04-29 13:00:00", tz="UTC")
    matches = [s for s in swings if s.kind == "low" and s.time == target_time]
    assert len(matches) == 1
    assert abs(matches[0].price - 4510.29) < 0.01


def test_swings_are_sorted_by_time(smc_h4_apr_may):
    swings = detect_swings(smc_h4_apr_may, n_left=2, n_right=2)
    times = [s.time for s in swings]
    assert times == sorted(times)

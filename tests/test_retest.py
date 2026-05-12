"""Tests for retest detection (price approaching a structural level)."""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from quant.data.parquet import read_bars_month
from quant.structure.bos import n_consecutive_closes_above
from quant.structure.retest import (
    first_low_within_pct,
    first_high_within_pct,
)


def _bars(highs_lows) -> pd.DataFrame:
    """highs_lows is a list of (high, low) tuples."""
    n = len(highs_lows)
    return pd.DataFrame({
        "time": pd.date_range("2026-01-01", periods=n, freq="15min", tz="UTC"),
        "open": [(h + l) / 2 for h, l in highs_lows],
        "high": [h for h, l in highs_lows],
        "low": [l for h, l in highs_lows],
        "close": [(h + l) / 2 for h, l in highs_lows],
        "tick_volume": [100] * n,
    })


# ---------------------------------------------------------------------------
# first_low_within_pct
# ---------------------------------------------------------------------------

def test_low_exactly_at_level_triggers():
    bars = _bars([(110, 105), (108, 100)])  # bar 1's low == 100
    idx = first_low_within_pct(bars, level=100.0, max_distance_pct=0.5)
    assert idx == 1


def test_low_within_tolerance_triggers():
    """Low at 100.4 vs level 100 -> 0.4% distance, within 0.5% tolerance."""
    bars = _bars([(110, 105), (102, 100.4)])
    idx = first_low_within_pct(bars, level=100.0, max_distance_pct=0.5)
    assert idx == 1


def test_low_just_outside_tolerance_does_not_trigger():
    bars = _bars([(110, 105), (102, 100.6)])  # 0.6% > 0.5%
    idx = first_low_within_pct(bars, level=100.0, max_distance_pct=0.5)
    assert idx is None


def test_low_below_tolerance_window_still_triggers():
    """Low dipping slightly below level (within tolerance) is also a 'retest'."""
    bars = _bars([(110, 105), (105, 99.7)])
    idx = first_low_within_pct(bars, level=100.0, max_distance_pct=0.5)
    assert idx == 1


def test_low_far_below_does_not_trigger():
    """A deep break (low far below) is NOT a retest — outside tolerance window."""
    bars = _bars([(110, 105), (95, 90)])  # low 90, level 100, distance = 10%
    idx = first_low_within_pct(bars, level=100.0, max_distance_pct=0.5)
    assert idx is None


def test_first_low_within_pct_returns_first_match():
    bars = _bars([
        (110, 105),  # 0
        (107, 100),  # 1  retest
        (108, 102),  # 2  also a retest, but we want the FIRST
    ])
    idx = first_low_within_pct(bars, level=100.0, max_distance_pct=0.5)
    assert idx == 1


def test_first_low_within_pct_with_after_time():
    """Match before after_time is ignored."""
    bars = _bars([
        (110, 100),   # 0  matches but ignored
        (108, 105),   # 1
        (109, 100),   # 2  matches
    ])
    cutoff = bars.loc[1, "time"]
    idx = first_low_within_pct(bars, level=100.0, max_distance_pct=0.5, after_time=cutoff)
    assert idx == 2


def test_first_high_within_pct_mirror():
    """For short-side BOS retests: high comes UP to a broken swing low from below."""
    bars = _bars([
        (90, 80),    # 0
        (99.7, 90),  # 1  high within 0.5% of 100
    ])
    idx = first_high_within_pct(bars, level=100.0, max_distance_pct=0.5)
    assert idx == 1


# ---------------------------------------------------------------------------
# Composition with bos.n_consecutive_closes_above (the full retest+hold flow)
# ---------------------------------------------------------------------------

def test_retest_then_hold_composition():
    """Synthetic: BOS at level=100, price up, retest dips to 100, then 2 closes above -> entry."""
    # bars: [(high, low)] -> close = midpoint
    bars = _bars([
        (105, 102),  # 0  post-BOS, no retest
        (108, 100),  # 1  retest (low touches level)
        (107, 102),  # 2  close = 104.5 > 100 (1st hold close)
        (109, 105),  # 3  close = 107 > 100 (2nd hold close -> entry trigger)
    ])
    retest_idx = first_low_within_pct(bars, level=100.0, max_distance_pct=0.5)
    assert retest_idx == 1
    hold_idx = n_consecutive_closes_above(
        bars, level=100.0, n=2, after_time=bars.loc[retest_idx, "time"],
    )
    assert hold_idx == 3


# ---------------------------------------------------------------------------
# Real-data ground truth — M15 retest after the H4 BOS at May 6 05:00 UTC
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def m15_apr_may() -> pd.DataFrame:
    repo_root = Path(__file__).resolve().parent.parent
    apr = read_bars_month(repo_root / "data", "XAUUSD", "M15", 2026, 4)
    may = read_bars_month(repo_root / "data", "XAUUSD", "M15", 2026, 5)
    return pd.concat([apr, may], ignore_index=True).sort_values("time").reset_index(drop=True)


def test_apr_may_m15_retest_of_lh_after_bos(m15_apr_may):
    """After H4 BOS at May 6 05:00 UTC, M15 should print a retest of 4660.32
    within the strategy's 2-H4-bar wait window (~8 hours) at 0.5% tolerance."""
    bos_time = pd.Timestamp("2026-05-06 04:45:00", tz="UTC")  # exclusive after_time
    retest_idx = first_low_within_pct(
        m15_apr_may, level=4660.32, max_distance_pct=0.5, after_time=bos_time,
    )
    assert retest_idx is not None, "no M15 retest of broken LH detected"
    retest_bar = m15_apr_may.iloc[retest_idx]
    # Retest must occur within 8 hours (= 2 H4 bars) of BOS — friend's kill switch
    deadline = pd.Timestamp("2026-05-06 13:00:00", tz="UTC")
    assert retest_bar["time"] < deadline, (
        f"retest at {retest_bar['time']} is past the 2-H4-bar deadline {deadline}"
    )

"""Tests for volume-surge detection."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from quant.structure.volume import detect_volume_surges


def _bars(volumes) -> pd.DataFrame:
    n = len(volumes)
    return pd.DataFrame({
        "time": pd.date_range("2026-01-01", periods=n, freq="4h", tz="UTC"),
        "open": [4500.0] * n,
        "high": [4501.0] * n,
        "low": [4499.0] * n,
        "close": [4500.0] * n,
        "tick_volume": volumes,
    })


# ---------------------------------------------------------------------------
# Synthetic
# ---------------------------------------------------------------------------

def test_clear_surge_at_known_index():
    """Bar with 5x the surrounding volume should trigger; the others should not."""
    vols = [100] * 20 + [500]
    bars = _bars(vols)
    surge = detect_volume_surges(bars, lookback=20, multiplier=1.4)
    assert surge.iloc[20] is np.True_ or bool(surge.iloc[20]) is True
    assert not surge.iloc[:20].any()


def test_threshold_is_inclusive():
    """A bar with volume == multiplier * median should also trigger (>=, not >)."""
    vols = [100] * 20 + [140]  # exactly 1.4x median(100)
    bars = _bars(vols)
    surge = detect_volume_surges(bars, lookback=20, multiplier=1.4)
    assert bool(surge.iloc[20]) is True


def test_below_threshold_does_not_trigger():
    vols = [100] * 20 + [139]  # 1.39x median, just under
    bars = _bars(vols)
    surge = detect_volume_surges(bars, lookback=20, multiplier=1.4)
    assert bool(surge.iloc[20]) is False


def test_warmup_bars_do_not_trigger():
    """Until lookback bars have accumulated, surge cannot be evaluated."""
    vols = [100] * 5 + [9999] + [100] * 14
    bars = _bars(vols)
    surge = detect_volume_surges(bars, lookback=20, multiplier=1.4)
    # Index 5 has only 5 prior bars (insufficient lookback) → must be False
    assert bool(surge.iloc[5]) is False


def test_multiplier_param_changes_outcome():
    vols = [100] * 20 + [200]
    bars = _bars(vols)
    assert bool(detect_volume_surges(bars, 20, 1.5).iloc[20]) is True
    assert bool(detect_volume_surges(bars, 20, 2.5).iloc[20]) is False


def test_returns_series_aligned_to_input():
    bars = _bars([100] * 25)
    surge = detect_volume_surges(bars, lookback=20, multiplier=1.4)
    assert len(surge) == len(bars)
    assert surge.index.equals(bars.index)
    assert surge.dtype == bool


def test_zero_volume_bars_handled():
    """Median of zeros = 0; any positive volume is technically infinite multiple,
    but we should treat a 0-median window as 'no signal' to avoid div-by-zero NaN."""
    vols = [0] * 20 + [50]
    bars = _bars(vols)
    surge = detect_volume_surges(bars, lookback=20, multiplier=1.4)
    # Implementation choice documented in module: 0 median -> False
    assert bool(surge.iloc[20]) is False


# ---------------------------------------------------------------------------
# Real-data ground truth
# ---------------------------------------------------------------------------

def test_apr_may_h4_may4_capitulation_is_a_surge(smc_h4_apr_may):
    """May 4 13:00 UTC bar is the highest-volume bar in Apr-May; must trigger
    1.4x surge against the prior 20 H4 bars."""
    surge = detect_volume_surges(smc_h4_apr_may, lookback=20, multiplier=1.4)
    target_time = pd.Timestamp("2026-05-04 13:00:00", tz="UTC")
    idx = smc_h4_apr_may.index[smc_h4_apr_may["time"] == target_time]
    assert len(idx) == 1
    assert bool(surge.loc[idx[0]]) is True


def test_apr_may_h4_quiet_bar_is_not_a_surge(smc_h4_apr_may):
    """A typical Sunday-evening / overnight bar should not register a surge."""
    surge = detect_volume_surges(smc_h4_apr_may, lookback=20, multiplier=1.4)
    target_time = pd.Timestamp("2026-04-13 01:00:00", tz="UTC")  # post-weekend reopen quiet bar
    idx = smc_h4_apr_may.index[smc_h4_apr_may["time"] == target_time]
    assert len(idx) == 1
    # Either it's False, or warmup hasn't completed yet — both acceptable
    # for a "this should not be the noisy surge" assertion.
    assert bool(surge.loc[idx[0]]) is False

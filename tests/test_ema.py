"""Tests for EMA and confluence-distance helpers."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from quant.structure.ema import ema, distance_pct, is_within_pct


def test_ema_of_constant_is_constant():
    s = pd.Series([5.0] * 50)
    e = ema(s, span=20)
    assert (e.dropna() == 5.0).all()


def test_ema_decays_toward_new_value():
    """Step from 100 to 200 — EMA crawls upward but doesn't snap immediately."""
    s = pd.Series([100.0] * 30 + [200.0] * 30)
    e = ema(s, span=10)
    # First step bar: EMA still close to 100
    assert e.iloc[30] < 120.0
    # 30 bars later: EMA approached 200 but didn't reach exactly
    assert 195.0 < e.iloc[59] < 200.0


def test_ema_span_1_is_identity():
    s = pd.Series([1.0, 5.0, 3.0, 7.0])
    e = ema(s, span=1)
    pd.testing.assert_series_equal(e, s, check_names=False)


def test_ema_returns_aligned_series():
    s = pd.Series([1.0, 2.0, 3.0, 4.0], index=["a", "b", "c", "d"])
    e = ema(s, span=2)
    assert list(e.index) == ["a", "b", "c", "d"]


def test_ema_invalid_span_raises():
    s = pd.Series([1.0, 2.0])
    with pytest.raises(ValueError):
        ema(s, span=0)


def test_distance_pct_basic():
    assert distance_pct(110.0, 100.0) == pytest.approx(10.0)
    assert distance_pct(90.0, 100.0) == pytest.approx(10.0)


def test_distance_pct_zero_when_equal():
    assert distance_pct(100.0, 100.0) == 0.0


def test_distance_pct_zero_reference_raises():
    with pytest.raises(ValueError):
        distance_pct(100.0, 0.0)


def test_is_within_pct_inclusive_at_boundary():
    """Distance exactly equal to threshold counts as within (inclusive)."""
    assert is_within_pct(110.0, 100.0, max_pct=10.0) is True
    assert is_within_pct(110.01, 100.0, max_pct=10.0) is False


def test_is_within_pct_handles_below_and_above():
    assert is_within_pct(95.0, 100.0, max_pct=10.0) is True
    assert is_within_pct(85.0, 100.0, max_pct=10.0) is False


# ---------------------------------------------------------------------------
# Real-data sanity (no broker-quote-dependent assertions)
# ---------------------------------------------------------------------------

def test_ema_on_real_h4_close_is_within_recent_range(smc_h4_apr_may):
    """EMA20 of close should sit between recent min and max — sanity, not hand-pinned."""
    e = ema(smc_h4_apr_may["close"], span=20)
    e = e.dropna()
    assert e.min() >= smc_h4_apr_may["close"].min()
    assert e.max() <= smc_h4_apr_may["close"].max()
    assert not e.isna().any()

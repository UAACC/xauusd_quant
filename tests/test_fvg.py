"""Tests for Fair Value Gap (3-bar imbalance) detection."""
from __future__ import annotations

import pandas as pd
import pytest

from quant.structure.fvg import FVG, detect_fvgs


def _bars(triples) -> pd.DataFrame:
    """Build a bar DataFrame from a list of (high, low) pairs."""
    n = len(triples)
    return pd.DataFrame({
        "time": pd.date_range("2026-01-01", periods=n, freq="4h", tz="UTC"),
        "open": [h for h, l in triples],
        "high": [h for h, l in triples],
        "low": [l for h, l in triples],
        "close": [(h + l) / 2 for h, l in triples],
        "tick_volume": [100] * n,
    })


# ---------------------------------------------------------------------------
# Synthetic
# ---------------------------------------------------------------------------

def test_bullish_fvg_detected():
    """Bar[i-2].high=10, then big up-bar, then bar[i].low=15 -> bullish FVG (10..15)."""
    bars = _bars([(10, 8), (20, 12), (25, 15), (24, 18)])
    fvgs = detect_fvgs(bars)
    assert len(fvgs) == 1
    f = fvgs[0]
    assert f.direction == "bullish"
    assert f.lower_edge == 10
    assert f.upper_edge == 15
    assert f.index_left == 0
    assert f.index_right == 2


def test_bearish_fvg_detected():
    """Bar[i-2].low=15, big down-bar, bar[i].high=10 -> bearish FVG (10..15)."""
    bars = _bars([(20, 15), (16, 8), (10, 5), (8, 4)])
    fvgs = detect_fvgs(bars)
    assert len(fvgs) == 1
    f = fvgs[0]
    assert f.direction == "bearish"
    assert f.lower_edge == 10
    assert f.upper_edge == 15


def test_no_fvg_when_bars_overlap():
    """If bar[i].low <= bar[i-2].high, no bullish FVG (and symmetric for bearish)."""
    bars = _bars([(10, 8), (12, 9), (11, 9), (10, 8)])
    assert detect_fvgs(bars) == []


def test_strict_inequality_at_boundary():
    """Equal edges (bar[i].low == bar[i-2].high) is no FVG."""
    bars = _bars([(10, 8), (15, 9), (12, 10), (11, 9)])  # bar2.low=10 == bar0.high=10
    assert detect_fvgs(bars) == []


def test_too_few_bars_returns_empty():
    bars = _bars([(10, 8), (12, 9)])
    assert detect_fvgs(bars) == []


def test_multiple_fvgs_in_sequence():
    """Two separated impulse moves -> at least one FVG per impulse.

    Note: any two consecutive up-bars produce overlapping 3-bar windows
    that all count as bullish FVGs by definition. We assert presence of
    each impulse's gap, not an exact count — uniqueness/filtering is a
    downstream concern (the strategy de-dupes when picking TP candidates).
    """
    bars = _bars([
        (10, 8),    # 0
        (20, 12),   # 1
        (25, 15),   # 2  <- first impulse: bullish FVG with bar 0 (10..15)
        (24, 17),   # 3  consolidation
        (23, 17),   # 4
        (22, 17),   # 5
        (28, 25),   # 6  <- second impulse begins
        (35, 28),   # 7  <- bullish FVG with bar 5 (22..28)
    ])
    fvgs = detect_fvgs(bars)
    bullish = [f for f in fvgs if f.direction == "bullish"]
    edges = {(f.lower_edge, f.upper_edge) for f in bullish}
    assert (10.0, 15.0) in edges, f"first impulse FVG missing; got {edges}"
    assert any(f.upper_edge >= 25.0 for f in bullish), (
        f"no FVG from second impulse; got {edges}"
    )
    assert all(f.direction == "bullish" for f in fvgs)


def test_fvg_carries_times():
    bars = _bars([(10, 8), (20, 12), (25, 15)])
    f = detect_fvgs(bars)[0]
    assert f.time_left == bars.loc[0, "time"]
    assert f.time_right == bars.loc[2, "time"]


# ---------------------------------------------------------------------------
# Real-data ground truth — friend's "April 20 downtrend FVG"
# ---------------------------------------------------------------------------

def test_apr_may_h4_finds_bearish_fvg_in_apr20_window(smc_h4_apr_may):
    """Friend identified an unfilled bearish H4 FVG near April 20 with upper
    edge around 4770 (his quotes). Our H4 data should produce at least one
    bearish FVG in the April 18-25 downtrend window. Exact upper edge
    differs from friend's 4770 by the usual broker-quote noise (~20 USD)."""
    fvgs = detect_fvgs(smc_h4_apr_may)
    window_start = pd.Timestamp("2026-04-18", tz="UTC")
    window_end = pd.Timestamp("2026-04-25", tz="UTC")
    in_window = [
        f for f in fvgs
        if f.direction == "bearish"
        and window_start <= f.time_right <= window_end
    ]
    assert len(in_window) >= 1, (
        f"Expected at least one bearish FVG in Apr 18-25 downtrend; got {len(in_window)}"
    )
    # Upper edges should sit in the "low 4700s to mid 4800s" zone (where the
    # downtrend originated). Allow broad tolerance for broker-quote variance.
    upper_edges = [f.upper_edge for f in in_window]
    assert any(4700 <= ue <= 4900 for ue in upper_edges), (
        f"No bearish FVG upper edge in [4700, 4900]; got {upper_edges}"
    )

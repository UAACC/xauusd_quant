"""Tests for BOS (Break of Structure) detection on bar series."""
from __future__ import annotations

import pandas as pd
import pytest

from quant.data.parquet import read_bars_month
from quant.structure.bos import (
    first_close_above,
    first_close_below,
    n_consecutive_closes_above,
    n_consecutive_closes_below,
)


def _bars(closes) -> pd.DataFrame:
    n = len(closes)
    return pd.DataFrame({
        "time": pd.date_range("2026-01-01", periods=n, freq="4h", tz="UTC"),
        "open": closes,
        "high": [c + 1 for c in closes],
        "low": [c - 1 for c in closes],
        "close": closes,
        "tick_volume": [100] * n,
    })


# ---------------------------------------------------------------------------
# first_close_above / first_close_below
# ---------------------------------------------------------------------------

def test_first_close_above_basic():
    bars = _bars([100, 101, 99, 105, 103])
    idx = first_close_above(bars, level=104)
    assert idx == 3


def test_first_close_above_returns_none_when_no_break():
    bars = _bars([100, 99, 98, 97, 96])
    assert first_close_above(bars, level=200) is None


def test_first_close_above_strict_inequality():
    """Close == level does not count as 'above'."""
    bars = _bars([100, 101, 102, 103])
    assert first_close_above(bars, level=102) == 3  # 103 > 102; 102 itself doesn't count


def test_first_close_above_with_after_time():
    """Earlier breaks ignored when after_time is provided."""
    bars = _bars([100, 105, 99, 105, 99])
    cutoff = bars.loc[2, "time"]
    idx = first_close_above(bars, level=104, after_time=cutoff)
    assert idx == 3


def test_first_close_above_after_time_inclusive_or_exclusive():
    """after_time semantics: 'strictly after' (>), not '>='."""
    bars = _bars([100, 105, 99, 105])
    cutoff = bars.loc[1, "time"]   # the first 105 bar's time
    idx = first_close_above(bars, level=104, after_time=cutoff)
    # bar 1 itself is excluded (after_time is strictly-after) -> next break at 3
    assert idx == 3


def test_first_close_below_basic():
    bars = _bars([100, 101, 99, 95, 105])
    idx = first_close_below(bars, level=96)
    assert idx == 3


# ---------------------------------------------------------------------------
# n_consecutive_closes_above / below
# ---------------------------------------------------------------------------

def test_n_consecutive_closes_above_completes():
    bars = _bars([100, 99, 105, 106, 95, 110, 111, 112])
    # Starting from idx 0, looking for 3 consecutive closes > 100
    # Closes 105, 106 (idx 2,3) is 2 -> not yet
    # 110, 111, 112 (idx 5,6,7) is 3 -> idx 7 completes
    idx = n_consecutive_closes_above(bars, level=100, n=3)
    assert idx == 7


def test_n_consecutive_closes_above_n_equals_1():
    bars = _bars([100, 99, 105])
    idx = n_consecutive_closes_above(bars, level=100, n=1)
    assert idx == 2


def test_n_consecutive_closes_above_resets_on_break():
    bars = _bars([105, 106, 99, 107, 108])
    # First two would have completed n=3 if uninterrupted, but bar 2 breaks
    # the chain. Then 107, 108 = 2 consecutive, not 3 yet.
    idx = n_consecutive_closes_above(bars, level=100, n=3)
    assert idx is None


def test_n_consecutive_closes_above_no_completion():
    bars = _bars([99, 98, 97])
    assert n_consecutive_closes_above(bars, level=100, n=2) is None


def test_n_consecutive_closes_above_with_after_time():
    bars = _bars([105, 106, 107, 99, 110, 111])
    cutoff = bars.loc[3, "time"]  # the 99 bar's time
    # After cutoff: bars with time > cutoff -> idx 4 (110), 5 (111) = 2 consecutive
    idx = n_consecutive_closes_above(bars, level=100, n=2, after_time=cutoff)
    assert idx == 5


def test_n_consecutive_closes_below():
    bars = _bars([100, 99, 98, 105, 95, 94])
    idx = n_consecutive_closes_below(bars, level=100, n=2)
    # bars 1,2 (99,98) is 2 consecutive < 100 -> completes at idx 2
    assert idx == 2


# ---------------------------------------------------------------------------
# Real-data ground truth — BOS at May 6 above the May 1 LH
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def m15_apr_may() -> pd.DataFrame:
    from pathlib import Path
    repo_root = Path(__file__).resolve().parent.parent
    apr = read_bars_month(repo_root / "data", "XAUUSD", "M15", 2026, 4)
    may = read_bars_month(repo_root / "data", "XAUUSD", "M15", 2026, 5)
    return pd.concat([apr, may], ignore_index=True).sort_values("time").reset_index(drop=True)


def test_apr_may_h4_bos_above_may1_lh(smc_h4_apr_may):
    """First H4 close strictly above the May 1 13:00 UTC LH (4660.32) after
    the May 4 capitulation should be the BOS bar — empirically May 6 05:00 UTC."""
    capitulation_time = pd.Timestamp("2026-05-04 13:00:00", tz="UTC")
    idx = first_close_above(smc_h4_apr_may, level=4660.32, after_time=capitulation_time)
    assert idx is not None, "no H4 BOS detected"
    bos_bar = smc_h4_apr_may.iloc[idx]
    expected_time = pd.Timestamp("2026-05-06 05:00:00", tz="UTC")
    assert bos_bar["time"] == expected_time
    assert bos_bar["close"] > 4660.32


def test_apr_may_m15_sustains_above_lh_after_h4_bos(m15_apr_may):
    """After the H4 BOS at May 6 05:00 UTC, M15 should sustain above the LH
    (4660.32) for at least 2 consecutive closes — friend's M15 confirmation rule.

    The H4 BOS bar opens at 05:00 UTC. We check for 2 consecutive M15 closes
    above the level starting at or shortly after that time."""
    h4_bos_time = pd.Timestamp("2026-05-06 04:45:00", tz="UTC")  # after_time is exclusive
    idx = n_consecutive_closes_above(
        m15_apr_may, level=4660.32, n=2, after_time=h4_bos_time,
    )
    assert idx is not None, "M15 did not sustain 2 closes above LH after H4 BOS"
    completion_bar = m15_apr_may.iloc[idx]
    # Must complete within the H4 BOS bar's window (05:00 - 09:00 UTC) or shortly after
    assert completion_bar["time"] < pd.Timestamp("2026-05-06 12:00:00", tz="UTC")

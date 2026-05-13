"""Tests for the BOS-reversal strategy state machine."""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from quant.data.parquet import read_bars_month
from quant.strategies.bos_reversal import BosReversalSignal, detect_bos_reversal_signals


@pytest.fixture(scope="module")
def m15_apr_may() -> pd.DataFrame:
    repo_root = Path(__file__).resolve().parent.parent
    apr = read_bars_month(repo_root / "data", "XAUUSD", "M15", 2026, 4)
    may = read_bars_month(repo_root / "data", "XAUUSD", "M15", 2026, 5)
    return pd.concat([apr, may], ignore_index=True).sort_values("time").reset_index(drop=True)


# ---------------------------------------------------------------------------
# Real-data ground truth: the May 4 capitulation -> May 6 BOS setup must
# emit exactly one signal that mirrors the friend's narrative.
# ---------------------------------------------------------------------------

def test_apr_may_emits_may6_bos_signal(smc_h4_apr_may, m15_apr_may):
    signals = detect_bos_reversal_signals(smc_h4_apr_may, m15_apr_may)
    assert len(signals) >= 1, "no signals emitted on Apr-May fixture"

    # Find the signal whose capitulation is the May 4 13:00 UTC bar
    may4_capit = pd.Timestamp("2026-05-04 13:00:00", tz="UTC")
    matches = [s for s in signals if s.capitulation_time == may4_capit]
    assert len(matches) == 1, (
        f"expected exactly one signal from May 4 capitulation; "
        f"got {len(matches)}: {[(s.capitulation_time, s.entry_time) for s in matches]}"
    )
    sig = matches[0]
    # Friend's narrative: BOS at May 6 04:00-08:00 UTC range (broker 07:00-11:00 EEST)
    assert sig.direction == "long"
    assert sig.bos_level == pytest.approx(4660.32, abs=0.01)
    assert sig.bos_time == pd.Timestamp("2026-05-06 05:00:00", tz="UTC")
    # Entry should be after BOS and after the M15 retest+hold
    assert sig.entry_time > sig.bos_time
    assert sig.entry_time < pd.Timestamp("2026-05-07 00:00:00", tz="UTC")
    # SL/TP arithmetic: $20 SL, $40 TP per spec
    assert sig.sl_price == pytest.approx(sig.entry_price - 20.0)
    assert sig.tp_price == pytest.approx(sig.entry_price + 40.0)
    # Entry price must be above the broken LH (we entered after a hold)
    assert sig.entry_price > sig.bos_level


def test_apr_may_signals_sorted_chronologically(smc_h4_apr_may, m15_apr_may):
    signals = detect_bos_reversal_signals(smc_h4_apr_may, m15_apr_may)
    times = [s.entry_time for s in signals]
    assert times == sorted(times)


def test_apr_may_signal_metadata_consistent(smc_h4_apr_may, m15_apr_may):
    signals = detect_bos_reversal_signals(smc_h4_apr_may, m15_apr_may)
    for s in signals:
        # Every signal: capitulation < bos < entry
        assert s.capitulation_time < s.bos_time
        assert s.bos_time < s.entry_time
        # SL / TP relative to entry
        assert s.sl_price < s.entry_price < s.tp_price
        # Entry above the broken structural level
        assert s.entry_price > s.bos_level


# ---------------------------------------------------------------------------
# Sensitivity: knobs that should kill the May 4 signal
# ---------------------------------------------------------------------------

def test_signal_killed_by_extreme_volume_threshold(smc_h4_apr_may, m15_apr_may):
    """Set volume_multiplier so high (e.g. 5x) that no bar surges -> no signals."""
    signals = detect_bos_reversal_signals(
        smc_h4_apr_may, m15_apr_may,
        volume_multiplier=5.0,  # nothing in Apr-May hits 5x median
    )
    assert len(signals) == 0


def test_signal_killed_by_zero_retest_window(smc_h4_apr_may, m15_apr_may):
    """retest_max_distance_pct=0 means low must touch level exactly -> unlikely."""
    signals = detect_bos_reversal_signals(
        smc_h4_apr_may, m15_apr_may,
        retest_max_distance_pct=0.0,
    )
    # Could be 0; could also still hit if low exactly equals 4660.32. Either
    # way this should not pass with the standard May 6 setup.
    may4 = pd.Timestamp("2026-05-04 13:00:00", tz="UTC")
    matches = [s for s in signals if s.capitulation_time == may4]
    assert len(matches) == 0


def test_signal_killed_by_too_strict_trend(smc_h4_apr_may, m15_apr_may):
    """trend_min_count=10 demands an unrealistic level of trend confirmation."""
    signals = detect_bos_reversal_signals(
        smc_h4_apr_may, m15_apr_may,
        trend_min_count=10,
    )
    assert len(signals) == 0


# ---------------------------------------------------------------------------
# Empty / edge inputs
# ---------------------------------------------------------------------------

def test_empty_inputs_return_empty():
    empty = pd.DataFrame(columns=["time", "open", "high", "low", "close", "tick_volume"])
    assert detect_bos_reversal_signals(empty, empty) == []


# ---------------------------------------------------------------------------
# Dedup + capitulation expiration (Phase 1 follow-up fixes)
# ---------------------------------------------------------------------------

def test_apr_may_dedup_no_duplicate_entries(smc_h4_apr_may, m15_apr_may):
    """No two signals should share the same (entry_time, entry_price).
    Pre-fix the May 7 07:30 / 4743.00 entry was emitted twice (one from
    Apr 27 capitulation, one from Apr 28); dedup must keep only one."""
    signals = detect_bos_reversal_signals(smc_h4_apr_may, m15_apr_may)
    keys = [(s.entry_time, s.entry_price) for s in signals]
    assert len(keys) == len(set(keys)), f"duplicate signals found: {keys}"


def test_apr_may_capitulation_expiration_kills_stale_setups(smc_h4_apr_may, m15_apr_may):
    """With max_h4_bars_capitulation_to_bos=3, the May 7 setups (capitulation
    in late April, BOS 9+ days later) are killed; the May 6 setup
    (capitulation -> BOS = ~9 H4 bars) is also killed under this strict cap."""
    strict = detect_bos_reversal_signals(
        smc_h4_apr_may, m15_apr_may, max_h4_bars_capitulation_to_bos=3,
    )
    permissive = detect_bos_reversal_signals(
        smc_h4_apr_may, m15_apr_may, max_h4_bars_capitulation_to_bos=100,
    )
    # Strict cap removes more signals than permissive
    assert len(strict) < len(permissive)


def test_apr_may_default_cap_keeps_friend_may6_setup(smc_h4_apr_may, m15_apr_may):
    """Default cap (12 H4 bars) must KEEP the friend's reference May 6 setup,
    whose capitulation -> BOS gap is ~9 H4 bars."""
    signals = detect_bos_reversal_signals(smc_h4_apr_may, m15_apr_may)
    may4_capit = pd.Timestamp("2026-05-04 13:00:00", tz="UTC")
    matches = [s for s in signals if s.capitulation_time == may4_capit]
    assert len(matches) == 1, "default cap should keep the friend's May 6 setup"


# ---------------------------------------------------------------------------
# Min capitulation-to-BOS gap (friend追问 #2 follow-up)
# Friend's rejected #2 had capitulation -> BOS = 1 H4 bar (4h), which they
# called "oversold bounce, not a real reversal". The default min=3 should
# filter such cases while keeping the May 6 setup (9-bar gap) intact.
# ---------------------------------------------------------------------------

def test_apr_may_default_min_keeps_friend_may6_setup(smc_h4_apr_may, m15_apr_may):
    """Default min (3 H4 bars) must KEEP the May 6 reference setup
    (capitulation -> BOS = 9 H4 bars, comfortably above floor)."""
    signals = detect_bos_reversal_signals(smc_h4_apr_may, m15_apr_may)
    may4_capit = pd.Timestamp("2026-05-04 13:00:00", tz="UTC")
    matches = [s for s in signals if s.capitulation_time == may4_capit]
    assert len(matches) == 1, "default min should keep the friend's May 6 setup"


def test_apr_may_min_gap_filter_is_strictly_monotonic(smc_h4_apr_may, m15_apr_may):
    """Raising min_h4_bars_capitulation_to_bos never INCREASES signal count."""
    counts = []
    for m in (0, 3, 6, 9, 12):
        sigs = detect_bos_reversal_signals(
            smc_h4_apr_may, m15_apr_may,
            min_h4_bars_capitulation_to_bos=m,
        )
        counts.append((m, len(sigs)))
    # monotonically non-increasing
    for (m_a, n_a), (m_b, n_b) in zip(counts, counts[1:]):
        assert n_a >= n_b, f"min={m_a}->{n_a} but min={m_b}->{n_b} should be <= {n_a}"


def test_apr_may_min_gap_above_may6_kills_it(smc_h4_apr_may, m15_apr_may):
    """A min above the May 6 setup's actual gap must kill it.

    Note: capitulation->BOS gap is ~9-10 H4 bars (varies with broker feed:
    a one-bar wick difference around the May 6 BOS bar shifts the gap by 1).
    Use min=11 as the unambiguous-too-strict bound so the test is feed-
    robust."""
    signals = detect_bos_reversal_signals(
        smc_h4_apr_may, m15_apr_may,
        min_h4_bars_capitulation_to_bos=11,
    )
    may4_capit = pd.Timestamp("2026-05-04 13:00:00", tz="UTC")
    matches = [s for s in signals if s.capitulation_time == may4_capit]
    assert len(matches) == 0, "min=11 should reject May 6 setup (~9-10-bar gap)"

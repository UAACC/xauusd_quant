"""Tests for trend confirmation by counting LL/LH (or HH/HL) events.

The metric counts swing-to-swing transitions (e.g., "this low < prior low" =
one LL event), optionally over a recent window. The friend's spec — "H4 出现
3 个 [LL] 我认为有效" — counts events, not unbroken chain length, because
real H4 structure has counter-bounces that don't cancel a downtrend.
"""
from __future__ import annotations

import pandas as pd
import pytest

from quant.structure.swings import Swing, detect_swings
from quant.structure.trend import (
    count_lower_lows,
    count_lower_highs,
    count_higher_highs,
    count_higher_lows,
    is_downtrend,
    is_uptrend,
)


def _swing(i: int, price: float, kind: str) -> Swing:
    return Swing(index=i, time=pd.Timestamp("2026-01-01", tz="UTC") + pd.Timedelta(hours=i),
                 price=price, kind=kind)


# ---------------------------------------------------------------------------
# count_lower_lows
# ---------------------------------------------------------------------------

def test_count_lls_strict_downtrend():
    """Four lows each strictly less than prior -> 3 LL events."""
    swings = [
        _swing(0, 100, "low"),
        _swing(2, 90,  "low"),
        _swing(4, 80,  "low"),
        _swing(6, 70,  "low"),
    ]
    assert count_lower_lows(swings) == 3


def test_count_lls_with_counter_bounce_still_counts():
    """A higher-low in the middle does NOT cancel surrounding LL events."""
    swings = [
        _swing(0, 100, "low"),
        _swing(2, 90,  "low"),  # LL
        _swing(4, 95,  "low"),  # HL (not LL)
        _swing(6, 85,  "low"),  # LL
        _swing(8, 75,  "low"),  # LL
    ]
    # LL events: 90<100, 85<95, 75<85 = 3
    assert count_lower_lows(swings) == 3


def test_count_lls_equal_does_not_count():
    """Equal-priced lows: not strictly less, no event."""
    swings = [
        _swing(0, 100, "low"),
        _swing(2, 90,  "low"),  # LL
        _swing(4, 90,  "low"),  # equal -> NOT LL
        _swing(6, 80,  "low"),  # LL
    ]
    assert count_lower_lows(swings) == 2


def test_count_lls_window_clips_to_recent():
    """With window=N, only the last N low-swings are considered."""
    swings = [_swing(i, 100 - i*10, "low") for i in range(10)]
    # All 9 events, but window=4 lows -> only the last 3 events count
    assert count_lower_lows(swings, window=4) == 3
    assert count_lower_lows(swings) == 9


def test_count_lls_empty():
    assert count_lower_lows([]) == 0


def test_count_lls_single_low():
    """Need at least 2 lows for any event."""
    assert count_lower_lows([_swing(0, 100, "low")]) == 0


def test_count_lls_ignores_highs():
    """Only low-swings are counted; intervening highs do not affect the count."""
    swings = [
        _swing(0, 100, "low"),
        _swing(1, 200, "high"),
        _swing(2, 90,  "low"),
        _swing(3, 150, "high"),
        _swing(4, 80,  "low"),
    ]
    assert count_lower_lows(swings) == 2


# ---------------------------------------------------------------------------
# count_lower_highs (mirror)
# ---------------------------------------------------------------------------

def test_count_lhs_strict():
    swings = [
        _swing(0, 100, "high"),
        _swing(2, 90,  "high"),
        _swing(4, 80,  "high"),
    ]
    assert count_lower_highs(swings) == 2


def test_count_lhs_with_counter_bounce():
    swings = [
        _swing(0, 100, "high"),
        _swing(2, 90,  "high"),  # LH
        _swing(4, 105, "high"),  # HH
        _swing(6, 95,  "high"),  # LH
    ]
    assert count_lower_highs(swings) == 2


# ---------------------------------------------------------------------------
# is_downtrend / is_uptrend
# ---------------------------------------------------------------------------

def test_is_downtrend_clean_3_3():
    swings = [
        _swing(0, 100, "high"),
        _swing(1, 80,  "low"),
        _swing(2, 95,  "high"),
        _swing(3, 70,  "low"),
        _swing(4, 90,  "high"),
        _swing(5, 60,  "low"),
    ]
    # 3 lows: 80, 70, 60 -> LL count = 2 (need 3 lows for 2 events;
    # needs more lows to hit 3 LL events).
    # So the "3 LL events" threshold actually requires 4+ lows.
    assert count_lower_lows(swings) == 2
    # Augment with one more LL to satisfy the 3-event default
    swings_ext = swings + [_swing(7, 85, "high"), _swing(8, 50, "low")]
    assert count_lower_lows(swings_ext) == 3
    assert count_lower_highs(swings_ext) == 3
    assert is_downtrend(swings_ext, min_count=3) is True
    assert is_uptrend(swings_ext, min_count=3) is False


def test_is_downtrend_requires_both_ll_and_lh():
    """3 LL events but only 2 LH events -> not a confirmed downtrend at min=3."""
    # Highs: 70, 100, 80 -> LH events: 80<100 = 1.
    # Add another high to get 2 LH events.
    swings = [
        _swing(0, 100, "low"),
        _swing(1, 70,  "high"),
        _swing(2, 90,  "low"),   # LL
        _swing(3, 100, "high"),  # HH (not LH vs 70)
        _swing(4, 80,  "low"),   # LL
        _swing(5, 95,  "high"),  # LH (vs 100)
        _swing(6, 70,  "low"),   # LL
        _swing(7, 80,  "high"),  # LH (vs 95)
    ]
    # LL events on lows [100, 90, 80, 70]: 90<100, 80<90, 70<80 = 3
    # LH events on highs [70, 100, 95, 80]: 95<100, 80<95 = 2
    assert count_lower_lows(swings) == 3
    assert count_lower_highs(swings) == 2
    assert is_downtrend(swings, min_count=3) is False
    assert is_downtrend(swings, min_count=2) is True


def test_is_uptrend_clean():
    swings = [
        _swing(0, 60,  "low"),
        _swing(1, 80,  "high"),
        _swing(2, 70,  "low"),
        _swing(3, 90,  "high"),
        _swing(4, 80,  "low"),
        _swing(5, 100, "high"),
        _swing(6, 90,  "low"),
        _swing(7, 110, "high"),
    ]
    # HH events on highs: 90>80, 100>90, 110>100 = 3
    # HL events on lows:  70>60, 80>70, 90>80 = 3
    assert is_uptrend(swings, min_count=3) is True
    assert is_downtrend(swings, min_count=3) is False


def test_is_downtrend_empty_swings():
    assert is_downtrend([], min_count=3) is False


def test_is_downtrend_window_constrains_recency():
    """Old downtrend events outside window do not count."""
    # 6 lows over time, descending: 6 lows -> 5 LL events overall, but we
    # cap window=3 lows -> 2 events
    swings = [_swing(i, 100 - i*5, "low") for i in range(6)]
    swings_with_highs = []
    for i, s in enumerate(swings):
        swings_with_highs.append(s)
        swings_with_highs.append(_swing(100 + i, 200 - i*5, "high"))
    # window=3 lows -> only last 3 lows considered -> 2 LL events
    # window=3 highs -> only last 3 highs -> 2 LH events
    # min_count=3 -> False even though full series would say True
    assert is_downtrend(swings_with_highs, min_count=3, window=3) is False
    assert is_downtrend(swings_with_highs, min_count=2, window=3) is True


# ---------------------------------------------------------------------------
# Real-data ground truth — friend's "H4 downtrend prior to May 4 capitulation"
# ---------------------------------------------------------------------------

def test_apr_may_h4_downtrend_confirmed_before_may4_capitulation(smc_h4_apr_may):
    """At the time just before the May 4 13:00 UTC capitulation H4 bar,
    the H4 swing structure shows enough LL/LH events to confirm downtrend.

    Using window=6 swings of each kind (~3 weeks of H4 macro structure)
    and min_count=3 (friend's threshold). Counter-bounces in early May
    do not cancel the LL/LH events from the Apr 24 - Apr 29 leg."""
    swings = detect_swings(smc_h4_apr_may, n_left=2, n_right=2)
    cutoff = pd.Timestamp("2026-05-04 09:00:00", tz="UTC")  # bar BEFORE the 13:00 UTC capitulation
    swings_before = [s for s in swings if s.time < cutoff]
    ll = count_lower_lows(swings_before, window=6)
    lh = count_lower_highs(swings_before, window=6)
    assert ll >= 3, f"only {ll} LL events in last 6 lows"
    assert lh >= 3, f"only {lh} LH events in last 6 highs"
    assert is_downtrend(swings_before, min_count=3, window=6) is True

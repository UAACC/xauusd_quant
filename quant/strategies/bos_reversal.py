"""SMC BOS-reversal strategy: 8-stage signal detector.

Walks through H4 swing structure; for each capitulation-low candidate
(swing low + volume surge in a confirmed downtrend), looks forward through
H4 + M15 bars for BOS, retest, and hold. Emits a ``BosReversalSignal`` at
the M15 close that completes the hold.

Only A-grade signals (every stage satisfied) are emitted; B-grade and below
are rejected per the strategy spec.

Primary timeframe: **H4** for trend/capitulation/BOS structure.
Confirmation timeframe: **M15** for BOS-sustain, retest, and hold.

Parameters default to the values in the strategy spec but are overridable
for sensitivity analysis. See module docstring at ``HANDOFF.md`` §8 for the
full rule set + ambiguity resolutions.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import pandas as pd

from quant.structure.bos import (
    first_close_above,
    first_close_below,
    n_consecutive_closes_above,
)
from quant.structure.retest import first_low_within_pct
from quant.structure.swings import detect_swings
from quant.structure.trend import is_downtrend
from quant.structure.volume import detect_volume_surges


@dataclass(frozen=True)
class BosReversalSignal:
    direction: Literal["long", "short"]
    entry_time: pd.Timestamp
    entry_price: float
    sl_price: float
    tp_price: float
    bos_level: float                # the LH (long) or LL (short) that was broken
    bos_time: pd.Timestamp          # H4 bar that closed across the level
    capitulation_time: pd.Timestamp # H4 swing low (long) / high (short) bar
    setup_grade: str = "A"


def detect_bos_reversal_signals(
    h4_bars: pd.DataFrame,
    m15_bars: pd.DataFrame,
    *,
    sl_distance: float = 20.0,
    tp_distance: float = 40.0,
    fractal_n: int = 2,
    trend_min_count: int = 3,
    trend_window: int = 6,
    volume_lookback: int = 20,
    volume_multiplier: float = 1.4,
    m15_bos_sustain: int = 2,
    max_h4_bars_capitulation_to_bos: int = 12,
    max_h4_bars_to_retest: int = 2,
    retest_max_distance_pct: float = 0.5,
    m15_hold_bars: int = 2,
) -> list[BosReversalSignal]:
    """Scan H4 + M15 bars; return all A-grade long BOS-reversal signals.

    Signals are returned in chronological order of ``entry_time`` and
    de-duplicated on ``(entry_time, entry_price)`` — when distinct
    capitulation candidates resolve to the same M15 entry bar, only one
    signal is kept (the one with the most-recent capitulation, which
    carries the freshest context). The friend's reference May 6 setup
    has ~9 H4 bars between capitulation and BOS, so the default
    ``max_h4_bars_capitulation_to_bos=12`` keeps it while killing the
    "stale" cases where price drifts back to a long-ago capitulation level.

    Stages (long setup):

    1. Trend  : ``trend_min_count`` LL events AND LH events in the last
                ``trend_window`` H4 swings of each kind.
    2. Capit. : H4 swing low whose volume ≥ ``volume_multiplier`` × median
                of the prior ``volume_lookback`` H4 bars.
    3. BOS    : first H4 close above the most recent prior LH after the
                capitulation, within ``max_h4_bars_capitulation_to_bos`` of
                the capitulation; then M15 sustains ``m15_bos_sustain``
                consecutive closes above the same level.
    4. Wait   : retest must occur within ``max_h4_bars_to_retest`` H4 bars
                of the H4 BOS; otherwise abandon. Also abandon if H4 closes
                back below the LH or below the prior swing low before the
                retest.
    5. Hold   : M15 retest (low within ``retest_max_distance_pct`` of LH),
                then ``m15_hold_bars`` consecutive M15 closes above the LH.
    6. Entry  : market BUY at the close of the bar that completes the hold.
                SL = entry − ``sl_distance``;  TP = entry + ``tp_distance``.

    Short setups (mirror) are not implemented yet — TODO.
    """
    if h4_bars.empty or m15_bars.empty:
        return []

    swings_h4 = detect_swings(h4_bars, n_left=fractal_n, n_right=fractal_n)
    surges_h4 = detect_volume_surges(
        h4_bars, lookback=volume_lookback, multiplier=volume_multiplier,
    )

    signals: list[BosReversalSignal] = []

    for swing in swings_h4:
        if swing.kind != "low":
            continue
        # Stage 2: capitulation = swing low + volume surge on the same H4 bar
        if not bool(surges_h4.iloc[swing.index]):
            continue

        # Stage 1: trend confirmation using swings strictly before this one
        swings_before = [s for s in swings_h4 if s.time < swing.time]
        if not is_downtrend(swings_before, min_count=trend_min_count, window=trend_window):
            continue

        prior_highs = [s for s in swings_before if s.kind == "high"]
        if not prior_highs:
            continue
        prior_lh = prior_highs[-1]   # most recent swing high → BOS target

        prior_lows = [s for s in swings_before if s.kind == "low"]
        if not prior_lows:
            continue
        prior_low_level = prior_lows[-1].price  # for invalidation check

        # Stage 3: H4 close above LH after capitulation
        bos_idx = first_close_above(h4_bars, level=prior_lh.price, after_time=swing.time)
        if bos_idx is None:
            continue
        bos_time = h4_bars.iloc[bos_idx]["time"]

        # Stage 3a: capitulation-to-BOS staleness cap. Without this, a
        # capitulation from weeks ago can re-fire as soon as price drifts
        # back through its prior LH, even when the original capitulation's
        # context (failed-breakdown, volume surge) is no longer relevant.
        capit_to_bos_h4_bars = (bos_time - swing.time) / pd.Timedelta(hours=4)
        if capit_to_bos_h4_bars > max_h4_bars_capitulation_to_bos:
            continue

        # Stage 3b: M15 sustain — N consecutive closes above LH from the H4 BOS bar onward
        # Subtract 1 second to make 'after' inclusive of bars at bos_time.
        m15_sustain_idx = n_consecutive_closes_above(
            m15_bars, level=prior_lh.price, n=m15_bos_sustain,
            after_time=bos_time - pd.Timedelta(seconds=1),
        )
        if m15_sustain_idx is None:
            continue
        m15_sustain_time = m15_bars.iloc[m15_sustain_idx]["time"]

        # Stage 4: M15 retest within max_h4_bars_to_retest of H4 BOS
        retest_deadline = bos_time + pd.Timedelta(hours=4 * max_h4_bars_to_retest)
        retest_idx = first_low_within_pct(
            m15_bars, level=prior_lh.price, max_distance_pct=retest_max_distance_pct,
            after_time=m15_sustain_time,
        )
        if retest_idx is None:
            continue
        retest_time = m15_bars.iloc[retest_idx]["time"]
        if retest_time > retest_deadline:
            continue

        # Invalidation: H4 closes back below LH or below prior swing low
        # AT ANY POINT between BOS and retest -> abandon
        invalid_lh_idx = first_close_below(h4_bars, level=prior_lh.price, after_time=bos_time)
        if invalid_lh_idx is not None:
            inv_lh_time = h4_bars.iloc[invalid_lh_idx]["time"]
            if inv_lh_time < retest_time:
                continue
        invalid_pl_idx = first_close_below(h4_bars, level=prior_low_level, after_time=bos_time)
        if invalid_pl_idx is not None:
            inv_pl_time = h4_bars.iloc[invalid_pl_idx]["time"]
            if inv_pl_time < retest_time:
                continue

        # Stage 5b: M15 hold = N consecutive closes above LH after retest
        hold_idx = n_consecutive_closes_above(
            m15_bars, level=prior_lh.price, n=m15_hold_bars,
            after_time=retest_time,
        )
        if hold_idx is None:
            continue

        # Stage 6: emit signal at the close of the hold-completing M15 bar
        entry_bar = m15_bars.iloc[hold_idx]
        entry_price = float(entry_bar["close"])
        signals.append(BosReversalSignal(
            direction="long",
            entry_time=pd.Timestamp(entry_bar["time"]),
            entry_price=entry_price,
            sl_price=entry_price - sl_distance,
            tp_price=entry_price + tp_distance,
            bos_level=float(prior_lh.price),
            bos_time=pd.Timestamp(bos_time),
            capitulation_time=pd.Timestamp(swing.time),
        ))

    # De-duplicate on (entry_time, entry_price). When two capitulations
    # produce the same M15 entry bar (different histories converging on the
    # same BOS+retest+hold sequence), keep only one — they're the same
    # trade. Prefer the most recent capitulation (freshest context).
    signals.sort(key=lambda s: (s.entry_time, -s.capitulation_time.value))
    deduped: dict[tuple[pd.Timestamp, float], BosReversalSignal] = {}
    for s in signals:
        key = (s.entry_time, s.entry_price)
        if key not in deduped:
            deduped[key] = s
    out = sorted(deduped.values(), key=lambda s: s.entry_time)
    return out

"""Volume-surge detection by rolling-median multiplier.

A bar is flagged as a surge when its volume is at least ``multiplier`` times
the median of the prior ``lookback`` bars (excluding the current bar). Using
median rather than mean keeps the threshold robust against the occasional
outlier — one previous spike doesn't desensitize the detector.

Default parameters (``lookback=20``, ``multiplier=1.4``) match the SMC
strategy spec: H4 capitulation requires the trigger bar's tick_volume to be
≥ 1.4 × median of the prior 20 H4 bars.

Edge case: a 20-bar window with median 0 (truly dead market — only happens
during multi-bar weekend gaps) cannot produce a meaningful ratio. We return
False there rather than dividing by zero or treating any positive volume as
infinitely large.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def detect_volume_surges(
    bars: pd.DataFrame,
    lookback: int = 20,
    multiplier: float = 1.4,
    volume_col: str = "tick_volume",
) -> pd.Series:
    """Return a boolean Series, True where bar's volume ≥ multiplier × median(prior lookback bars).

    Args:
        bars: DataFrame with at least ``volume_col``.
        lookback: number of prior bars to compute the median over (excludes current).
        multiplier: surge threshold; the ``>=`` comparison is inclusive.
        volume_col: column name holding the volume series.

    The first ``lookback`` bars cannot trigger (insufficient history). A
    median of 0 also returns False (see module docstring).
    """
    if lookback < 1:
        raise ValueError(f"lookback must be >= 1; got {lookback}")
    if multiplier <= 0:
        raise ValueError(f"multiplier must be > 0; got {multiplier}")

    vol = bars[volume_col].astype("float64")
    # ``shift(1)`` so the rolling window ends one bar BEFORE the current bar
    # (the current bar's volume is what we're comparing against).
    rolling_median = vol.shift(1).rolling(lookback, min_periods=lookback).median()
    threshold = rolling_median * multiplier
    surge = (vol >= threshold) & (rolling_median > 0)
    return surge.fillna(False).astype(bool)

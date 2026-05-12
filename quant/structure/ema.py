"""EMA computation + price-to-EMA confluence helpers.

The SMC strategy uses M1 and M5 EMA20/EMA50 as confluence guides during
the post-BOS retest stage — when price approaches the broken LH from above
*and* sits near a major EMA, the retest is judged higher quality. This
module supplies the building blocks; the consuming logic in
``quant.strategies.bos_reversal`` decides what "near" means and how the
confluence weights into the entry decision.

Convention: ``adjust=False`` so the EMA is the canonical "1 - alpha" form
that traders see on charts (TradingView, MT5, etc.), not pandas' default
adjusted form which is mean-of-weighted-bars.
"""

from __future__ import annotations

import pandas as pd


def ema(series: pd.Series, span: int) -> pd.Series:
    """Exponential moving average with the chart-standard ``adjust=False``.

    Args:
        series: numeric series (typically close prices).
        span: smoothing factor; alpha = 2 / (span + 1).

    Returns a Series with the same index. The first value equals the first
    input (no warm-up NaN), matching MT5 / TradingView convention.
    """
    if span < 1:
        raise ValueError(f"span must be >= 1; got {span}")
    return series.ewm(span=span, adjust=False).mean()


def distance_pct(price: float, reference: float) -> float:
    """Absolute percentage distance from ``price`` to ``reference``.

    e.g. ``distance_pct(110, 100) -> 10.0``. Always non-negative.
    Raises ``ValueError`` if ``reference == 0`` (undefined).
    """
    if reference == 0:
        raise ValueError("reference must be non-zero for percentage distance")
    return abs(price - reference) / abs(reference) * 100.0


def is_within_pct(price: float, reference: float, max_pct: float) -> bool:
    """True if ``price`` is within ``max_pct`` percent of ``reference`` (inclusive)."""
    return distance_pct(price, reference) <= max_pct

"""Shared pytest fixtures.

The ``smc_h4_apr_may`` fixture loads the real Apr-May 2026 H4 bars from
the data lake — the SMC primitives are validated against the well-known
"May 4 capitulation -> May 6 BOS" setup the friend described, with these
ground-truth pivots (our broker quotes differ from his by ~10-30 USD;
that's normal feed variance, the structure is identical):

    Apr 29 13:00 UTC   prior swing low   ~4510.29
    May  1 13:00 UTC   pinbar swing high ~4660.32  (BOS target LH)
    May  4 13:00 UTC   capitulation low   4500.72  (vol=268283, Apr-May max)
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_ROOT = REPO_ROOT / "data"


@pytest.fixture(scope="session")
def smc_h4_apr_may() -> pd.DataFrame:
    """H4 XAUUSD bars covering the friend's reference SMC setup.

    Returned DataFrame has the canonical bar schema (time/open/high/low/close/
    tick_volume/spread/real_volume), sorted by time, indexed 0..n-1.
    """
    from quant.data.parquet import read_bars_month

    apr = read_bars_month(DATA_ROOT, "XAUUSD", "H4", 2026, 4)
    may = read_bars_month(DATA_ROOT, "XAUUSD", "H4", 2026, 5)
    df = pd.concat([apr, may], ignore_index=True).sort_values("time").reset_index(drop=True)
    return df


@pytest.fixture(scope="session")
def h1_apr_may() -> pd.DataFrame:
    """H1 XAUUSD bars covering the friend's reference SMC setup.

    Used by the micro-CHoCH structural filter (stage 3b in
    ``bos_reversal``): on the May 6 reference, H1 n=2 fractal swings
    at 05-05 02:00 (4546.69) and 05-05 14:00 (4586.68) form the
    higher-high pair below the macro LH 4673.
    """
    from quant.data.parquet import read_bars_month

    apr = read_bars_month(DATA_ROOT, "XAUUSD", "H1", 2026, 4)
    may = read_bars_month(DATA_ROOT, "XAUUSD", "H1", 2026, 5)
    return pd.concat([apr, may], ignore_index=True).sort_values("time").reset_index(drop=True)

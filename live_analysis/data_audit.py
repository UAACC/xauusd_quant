"""Data quality + indicator-accuracy audit for the discretionary-analyst project.

Four audits:
1. Feed integrity: spread distribution by hour-of-day, M1 gap detection (excludes
   weekend + daily 22:00 UTC rollover gaps).
2. Indicator accuracy: cross-check our hand-rolled quant.structure indicators
   (ATR, ADX) against TA-Lib reference implementations. Known-bug findings:
   our ATR is SMA-of-TR not Wilder smoothing -- causes ~7-15% divergence from
   TA-Lib reference. Documented; xauusd_quant project decides on patch.
3. Coverage: M1 series spans the requested window with no unexpected gaps.
4. Cross-source: D1 closes compared to Yahoo Finance equivalent (e.g. SI=F for
   XAGUSD, ^NDX for USTEC). Sanity-checks that broker data isn't materially
   diverging from an independent source.

Symbol -> Yahoo mapping (extend as needed):
    XAGUSD -> SI=F (silver futures)
    XAUUSD -> GC=F (gold futures)
    USTEC  -> ^NDX (Nasdaq 100 cash)
    US500  -> ^GSPC (S&P 500)
    BTCUSD -> BTC-USD
    XTIUSD -> CL=F (WTI crude futures)

Run: ``python -m live_analysis.data_audit --symbol XAGUSD --days 30``
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

from typing import Optional

import numpy as np
import pandas as pd
import talib

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))


def pull_bars_for_audit(symbol: str, timeframe_const, lookback_days: int):
    """Pull bars from MT5 for the audit. Returns DataFrame in UTC time."""
    from mt5_connect import init_mt5
    import MetaTrader5 as mt5
    from quant.config import get_demo_mt5_path
    from quant.data.broker_time import discover_clock

    if not init_mt5(terminal_path=get_demo_mt5_path()):
        raise RuntimeError("MT5 init failed")
    try:
        if not mt5.symbol_select(symbol, True):
            raise RuntimeError(f"symbol_select({symbol}) failed")
        acct = mt5.account_info()
        clock = discover_clock(mt5, acct.server, symbol)
        end_utc = datetime.now(tz=timezone.utc)
        start_utc = end_utc - timedelta(days=lookback_days)
        sb = clock.utc_dt_to_broker_naive(start_utc)
        eb = clock.utc_dt_to_broker_naive(end_utc)
        rates = mt5.copy_rates_range(symbol, timeframe_const, sb, eb)
        if rates is None or len(rates) == 0:
            raise RuntimeError(f"empty rates for {symbol}")
        df = pd.DataFrame(rates)
        df["time"] = df["time"].apply(
            lambda s: pd.Timestamp(clock.broker_msc_to_utc_msc(int(s) * 1000), unit="ms", tz="UTC")
        )
        return df.sort_values("time").reset_index(drop=True)
    finally:
        mt5.shutdown()


# ---------------------------------------------------------------------------
# Audit 1: Spread distribution by hour-of-day
# ---------------------------------------------------------------------------

def audit_spread_by_hour(m1_bars: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """Spread (in points) percentiles by hour-of-day UTC. Output is a tidy table."""
    df = m1_bars.copy()
    df["hour"] = df["time"].dt.hour
    df["spread"] = df["spread"].astype(float)
    grouped = df.groupby("hour")["spread"].agg(
        n="count",
        p25=lambda s: s.quantile(0.25),
        p50=lambda s: s.quantile(0.50),
        p75=lambda s: s.quantile(0.75),
        p95=lambda s: s.quantile(0.95),
        max="max",
    ).reset_index()
    return grouped


# ---------------------------------------------------------------------------
# Audit 2: M1 gap detection
# ---------------------------------------------------------------------------

def audit_m1_gaps(m1_bars: pd.DataFrame, max_gap_min: int = 5) -> pd.DataFrame:
    """Find unexpected gaps in the M1 series.

    Filters out two classes of expected gaps:
      - Weekend boundary: Friday close -> Sunday open (~48-50 hours)
      - Daily rollover: broker's ~1-hour break around 22:00 UTC each weekday
        (IC Markets / most MT5 brokers close briefly at end-of-day for settlement)
    """
    df = m1_bars.copy().sort_values("time").reset_index(drop=True)
    df["delta_min"] = df["time"].diff().dt.total_seconds() / 60
    gaps = df[df["delta_min"] > max_gap_min].copy()
    # Weekend gap (Friday close -> Sunday open)
    gaps["is_weekend"] = (gaps["time"].dt.weekday == 6) & (gaps["delta_min"] > 60 * 24)
    # Daily rollover: gap starts somewhere in 21:00-23:00 UTC, lasts < 90 min
    bar_hour = gaps["time"].dt.hour
    gaps["is_rollover"] = (bar_hour.isin([21, 22, 23])) & (gaps["delta_min"] < 90)
    return gaps[~(gaps["is_weekend"] | gaps["is_rollover"])][["time", "delta_min"]]


# ---------------------------------------------------------------------------
# Audit 3: Indicator accuracy (our quant.structure vs TA-Lib)
# ---------------------------------------------------------------------------

def audit_atr_against_talib(bars: pd.DataFrame, period: int = 14) -> dict:
    """Compare our ATR (quant.structure.atr.atr) to TA-Lib ATR. Wilder method both."""
    from quant.structure.atr import atr as our_atr_func

    h = bars["high"].astype(float).to_numpy()
    l = bars["low"].astype(float).to_numpy()
    c = bars["close"].astype(float).to_numpy()
    talib_atr = talib.ATR(h, l, c, timeperiod=period)
    our_series = our_atr_func(
        pd.Series(h, name="high"), pd.Series(l, name="low"), pd.Series(c, name="close"),
        period=period,
    )
    our_arr = our_series.to_numpy()
    # Compare last 50 values (skip warmup NaNs)
    n = min(50, len(talib_atr))
    sample_size = n
    diffs = []
    for i in range(-sample_size, 0):
        a = talib_atr[i]
        b = our_arr[i]
        if not np.isnan(a) and not np.isnan(b):
            diffs.append(abs(a - b))
    max_diff = max(diffs) if diffs else 0.0
    mean_diff = sum(diffs) / len(diffs) if diffs else 0.0
    return {
        "talib_last": float(talib_atr[-1]),
        "ours_last": float(our_arr[-1]),
        "max_diff": float(max_diff),
        "mean_diff": float(mean_diff),
        "match": max_diff < 1e-3,
    }


YAHOO_TICKER_MAP: dict[str, str] = {
    "XAGUSD": "SI=F",
    "XAUUSD": "GC=F",
    "USTEC":  "^NDX",
    "US500":  "^GSPC",
    "BTCUSD": "BTC-USD",
    "XTIUSD": "CL=F",
}


def audit_cross_source(symbol: str, mt5_d1_bars: pd.DataFrame, lookback_days: int = 30) -> Optional[dict]:
    """Compare MT5 D1 closes to Yahoo Finance equivalent for the same symbol.

    Returns a dict with summary stats, or None if the symbol has no Yahoo mapping
    or yfinance isn't available.
    """
    yf_ticker = YAHOO_TICKER_MAP.get(symbol)
    if yf_ticker is None:
        return None
    try:
        import yfinance as yf
    except ImportError:
        return {"error": "yfinance not installed -- pip install yfinance"}

    end_date = mt5_d1_bars["time"].max().date()
    start_date = (mt5_d1_bars["time"].max() - pd.Timedelta(days=lookback_days * 2)).date()
    try:
        yf_data = yf.Ticker(yf_ticker).history(
            start=start_date.isoformat(), end=(end_date + pd.Timedelta(days=1)).isoformat(),
        )
    except Exception as e:
        return {"error": f"yfinance pull failed: {e}"}

    if yf_data.empty:
        return {"error": f"yfinance returned empty for {yf_ticker}"}

    yf_df = yf_data.reset_index()
    yf_df["date"] = yf_df["Date"].dt.date
    yf_df = yf_df[["date", "Close"]].rename(columns={"Close": "yf_close"})

    mt5_df = mt5_d1_bars.copy()
    mt5_df["date"] = mt5_df["time"].dt.date
    mt5_df = mt5_df[["date", "close"]].rename(columns={"close": "mt5_close"})

    merged = pd.merge(mt5_df, yf_df, on="date", how="inner")
    if merged.empty:
        return {"error": "no overlapping dates"}

    merged["diff_abs"] = merged["mt5_close"] - merged["yf_close"]
    merged["diff_pct"] = merged["diff_abs"] / merged["yf_close"] * 100

    return {
        "yahoo_ticker": yf_ticker,
        "matched_dates": len(merged),
        "mean_diff_pct": float(merged["diff_pct"].mean()),
        "median_diff_pct": float(merged["diff_pct"].median()),
        "max_abs_diff_pct": float(merged["diff_pct"].abs().max()),
        "std_diff_pct": float(merged["diff_pct"].std()),
        "correlation": float(merged["mt5_close"].corr(merged["yf_close"])),
        "samples": merged.tail(5).to_dict("records"),
    }


def audit_adx_against_talib(bars: pd.DataFrame, period: int = 14) -> dict:
    """Compare our ADX (quant.structure.adx.adx) to TA-Lib ADX."""
    from quant.structure.adx import adx as our_adx_func

    h = bars["high"].astype(float).to_numpy()
    l = bars["low"].astype(float).to_numpy()
    c = bars["close"].astype(float).to_numpy()
    talib_adx = talib.ADX(h, l, c, timeperiod=period)

    # Our adx returns a Series
    our_series = our_adx_func(
        pd.Series(h, name="high"),
        pd.Series(l, name="low"),
        pd.Series(c, name="close"),
        period=period,
    )
    our_arr = our_series.to_numpy()

    sample_size = min(50, len(talib_adx))
    diffs = []
    for i in range(-sample_size, 0):
        a = talib_adx[i]
        b = our_arr[i]
        if not np.isnan(a) and not np.isnan(b):
            diffs.append(abs(a - b))
    max_diff = max(diffs) if diffs else 0.0
    mean_diff = sum(diffs) / len(diffs) if diffs else 0.0
    return {
        "talib_last": float(talib_adx[-1]),
        "ours_last": float(our_arr[-1]),
        "max_diff": float(max_diff),
        "mean_diff": float(mean_diff),
        "match": max_diff < 1e-2,  # ADX has stricter rounding sensitivity
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--symbol", default="XAGUSD")
    p.add_argument("--days", type=int, default=30, help="lookback for M1 audits")
    p.add_argument("--h4-bars", type=int, default=200, help="number of H4 bars for indicator audit")
    args = p.parse_args(argv)

    import MetaTrader5 as mt5
    print(f"=== Data Audit for {args.symbol} ===\n")

    # ---- Pull M1 ----
    print(f"[1/4] Pulling M1 for last {args.days} days...")
    m1 = pull_bars_for_audit(args.symbol, mt5.TIMEFRAME_M1, args.days)
    print(f"      {len(m1):,} bars  ({m1['time'].iloc[0]} -> {m1['time'].iloc[-1]})\n")

    # ---- Spread by hour ----
    print("[2/4] Spread distribution by hour-of-day UTC (in points):")
    spread_table = audit_spread_by_hour(m1, args.symbol)
    print(spread_table.to_string(index=False, float_format="%.1f"))
    print(f"\n      Best (tightest spread) hour: {int(spread_table.loc[spread_table['p50'].idxmin(), 'hour']):02d}:00 UTC")
    print(f"      Worst (widest) hour:          {int(spread_table.loc[spread_table['p50'].idxmax(), 'hour']):02d}:00 UTC")

    # ---- M1 gaps ----
    print("\n[3/4] M1 gap detection (>5min, excluding weekends):")
    gaps = audit_m1_gaps(m1)
    if gaps.empty:
        print("      No unexpected gaps. Feed integrity OK.")
    else:
        print(f"      Found {len(gaps)} suspicious gaps:")
        for _, row in gaps.head(10).iterrows():
            print(f"        {row['time']}  gap={row['delta_min']:.1f}min")
        if len(gaps) > 10:
            print(f"        ... +{len(gaps) - 10} more")

    # ---- Indicator audit (H4) ----
    print(f"\n[4/4] Indicator accuracy vs TA-Lib ({args.h4_bars} H4 bars):")
    h4 = pull_bars_for_audit(args.symbol, mt5.TIMEFRAME_H4, max(args.days * 6, args.h4_bars // 6 + 30))
    h4 = h4.tail(args.h4_bars).reset_index(drop=True)
    print(f"      Using {len(h4)} H4 bars")

    atr_result = audit_atr_against_talib(h4, period=14)
    status = "OK" if atr_result["match"] else "MISMATCH"
    print(f"      ATR(14):  ours={atr_result['ours_last']:.4f}  talib={atr_result['talib_last']:.4f}  "
          f"max_diff={atr_result['max_diff']:.6f}  [{status}]")

    adx_result = audit_adx_against_talib(h4, period=14)
    status = "OK" if adx_result["match"] else "MISMATCH"
    print(f"      ADX(14):  ours={adx_result['ours_last']:.4f}  talib={adx_result['talib_last']:.4f}  "
          f"max_diff={adx_result['max_diff']:.6f}  [{status}]")

    # ---- Cross-source ----
    print(f"\n[5/5] Cross-source verification (MT5 vs Yahoo Finance):")
    d1 = pull_bars_for_audit(args.symbol, mt5.TIMEFRAME_D1, max(60, args.days * 2))
    cross = audit_cross_source(args.symbol, d1, lookback_days=args.days)
    if cross is None:
        print(f"      No Yahoo mapping for {args.symbol} -- skipping cross-source")
    elif "error" in cross:
        print(f"      ERROR: {cross['error']}")
    else:
        print(f"      Yahoo ticker:        {cross['yahoo_ticker']}")
        print(f"      Matched dates:       {cross['matched_dates']}")
        print(f"      Correlation:         {cross['correlation']:.6f}")
        print(f"      Mean diff (MT5-YF):  {cross['mean_diff_pct']:+.3f}%")
        print(f"      Median diff:         {cross['median_diff_pct']:+.3f}%")
        print(f"      Max abs diff:        {cross['max_abs_diff_pct']:.3f}%")
        print(f"      Std of diff:         {cross['std_diff_pct']:.3f}%")
        if cross["correlation"] < 0.95:
            print(f"      [WARN] Correlation < 0.95 -- significant divergence from independent source")
        if abs(cross["mean_diff_pct"]) > 1.0:
            print(f"      [WARN] Systematic bias > 1% -- broker may be using different product/feed")

    print("\n=== Audit complete ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())

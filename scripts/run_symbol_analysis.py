"""Full institutional analysis on a single symbol using the analyst toolkit.

Usage: python scripts/run_symbol_analysis.py [SYMBOL]   (default XAGUSD)
"""
import sys
import time
from pathlib import Path

import pandas as pd
import talib

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from live_analysis.eval_trade import pull_live_context
from live_analysis.analyst_tools import (
    zscore, std_distance_from_ma, range_position, vol_regime,
    bollinger_distance, find_divergences, volume_profile, synthetic_dxy,
)
from quant.structure.swings import detect_swings


SYMBOL = sys.argv[1] if len(sys.argv) > 1 else "XAGUSD"

# Choose cross-asset companions based on primary
CROSS_ASSET_MAP = {
    "XAGUSD": ["EURUSD", "USDJPY", "GBPUSD", "USDCHF", "US500", "USTEC", "XAUUSD", "BTCUSD"],
    "XAUUSD": ["EURUSD", "USDJPY", "GBPUSD", "USDCHF", "US500", "USTEC", "XAGUSD", "BTCUSD"],
    "USTEC":  ["EURUSD", "USDJPY", "GBPUSD", "USDCHF", "US500", "XAUUSD", "BTCUSD", "XTIUSD"],
    "US500":  ["EURUSD", "USDJPY", "GBPUSD", "USDCHF", "USTEC", "XAUUSD", "BTCUSD", "XTIUSD"],
}
cross_asset = CROSS_ASSET_MAP.get(SYMBOL, ["EURUSD", "USDJPY", "GBPUSD", "USDCHF", "US500", "USTEC", "XAUUSD", "BTCUSD"])

t0 = time.perf_counter()
ctx = pull_live_context(SYMBOL, cross_asset_symbols=cross_asset)
elapsed = (time.perf_counter() - t0) * 1000

current = float(ctx["bars"]["M1"]["close"].iloc[-1])
print(f"# {SYMBOL} INSTITUTIONAL ANALYSIS")
print(f"# Snapshot: {ctx['snapshot_utc']}  (pull {elapsed:.0f}ms)")
print(f"# Current: {current:.4f}  spread: {ctx['live_spread_pts']:.0f}pt\n")

print("=" * 80)
print("AXIS 1: MULTI-TF STRUCTURE (Trend / Z-score / Range / Vol)")
print("=" * 80)

results_by_tf = {}
for tf in ["D1", "H4", "H1", "M30", "M15", "M5", "M1"]:
    df = ctx["bars"][tf]
    closes = df["close"]
    last = float(closes.iloc[-1])

    arr = closes.values.astype(float)
    ema9 = talib.EMA(arr, 9)[-1]
    ema20 = talib.EMA(arr, 20)[-1]
    ema50 = talib.EMA(arr, 50)[-1]
    stack_up = ema9 > ema20 > ema50
    stack_dn = ema9 < ema20 < ema50
    trend = "UP_STACK" if stack_up else "DOWN_STACK" if stack_dn else "MIXED"

    try:
        z = zscore(closes, lookback=50)
    except ValueError:
        z = None
    try:
        sd20 = std_distance_from_ma(closes, period=20, ma_type="EMA", lookback_for_std=50)
    except ValueError:
        sd20 = None
    try:
        vr = vol_regime(df, period=14, lookback=min(200, len(df) - 20))
    except (ValueError, Exception):
        vr = None
    try:
        rp_short = range_position(df, lookback_bars=min(20, len(df)))
        rp_long = range_position(df, lookback_bars=min(60, len(df)))
    except ValueError:
        rp_short = rp_long = None
    try:
        bb = bollinger_distance(closes, period=20)
    except ValueError:
        bb = None

    results_by_tf[tf] = {
        "trend": trend, "z": z, "sd20": sd20, "vr": vr,
        "rp_short": rp_short, "rp_long": rp_long, "bb": bb,
        "ema9": ema9, "ema20": ema20, "ema50": ema50,
    }

    z_str = f"{z.z:+.2f}sd" if z else "n/a"
    sd20_str = f"{sd20['z']:+.2f}sd" if sd20 else "n/a"
    vr_str = f"{vr.label:8s}(P{vr.percentile:.0f})" if vr else "n/a"
    rp_str = f"{rp_short['range_pct']:.0f}%/{rp_long['range_pct']:.0f}%" if rp_short else "n/a"
    bb_str = f"%B={bb['pct_b']:.2f}" if bb else "n/a"
    ema_pos = "+" if last > ema20 else "-"
    print(f"  {tf:3s}  trend={trend:10s}  z={z_str:>8s}  vs-EMA20={sd20_str:>9s}  "
          f"vol={vr_str}  range(20/60)={rp_str:>10s}  {bb_str}")

print()
print("=" * 80)
print("AXIS 2: STRUCTURE LEVELS (Swings + Volume Profile)")
print("=" * 80)

h4 = ctx["bars"]["H4"]
swings_h4 = detect_swings(h4, n_left=2, n_right=2)
print(f"  Last 8 H4 swings:")
for s in swings_h4[-8:]:
    print(f"    {s.kind:5s}  {s.time.strftime('%m-%d %H:%M')}  {s.price:.4f}")

h1 = ctx["bars"]["H1"]
h1_5d = h1[h1["time"] >= (pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=5))]
vp_h1 = volume_profile(h1_5d, num_bins=30)
print(f"\n  H1 5-day Volume Profile:")
print(f"    POC (highest vol price): {vp_h1.poc:.4f}  (current {(current - vp_h1.poc):+.4f})")
print(f"    Value Area High (VAH):   {vp_h1.vah:.4f}  ({(current - vp_h1.vah):+.4f})")
print(f"    Value Area Low (VAL):    {vp_h1.val:.4f}  ({(current - vp_h1.val):+.4f})")

if current > vp_h1.vah:
    va_loc = "ABOVE VAH (price rejected from value, momentum / overextension)"
elif current < vp_h1.val:
    va_loc = "BELOW VAL (rejected from value, breakdown / oversold)"
else:
    va_loc = "INSIDE VALUE AREA (accepted price; range-bound)"
print(f"    Current location: {va_loc}")

d1_60 = ctx["bars"]["D1"].tail(60)
vp_d1 = volume_profile(d1_60, num_bins=40)
print(f"\n  D1 60-day Volume Profile:")
print(f"    POC: {vp_d1.poc:.4f}  VAH: {vp_d1.vah:.4f}  VAL: {vp_d1.val:.4f}")
print(f"    Current vs POC: {current - vp_d1.poc:+.4f}  ({(current/vp_d1.poc - 1)*100:+.2f}%)")

print()
print("=" * 80)
print("AXIS 3: DIVERGENCES (RSI + MACD)")
print("=" * 80)

for tf in ["D1", "H4", "H1"]:
    df = ctx["bars"][tf]
    rsi_divs = find_divergences(df, indicator="RSI", lookback_bars=100)
    macd_divs = find_divergences(df, indicator="MACD", lookback_bars=100)
    recent_rsi = [d for d in rsi_divs if d.later_bar_idx >= len(df) - 15]
    recent_macd = [d for d in macd_divs if d.later_bar_idx >= len(df) - 15]
    print(f"  {tf}: RSI {len(recent_rsi)} recent, MACD {len(recent_macd)} recent (last 15 bars)")
    for d in recent_rsi[-3:]:
        bar_time = df["time"].iloc[d.later_bar_idx]
        print(f"      RSI  {d.kind:18s} {bar_time.strftime('%m-%d %H:%M')}  "
              f"price={d.price_extremes[1]:.4f}")
    for d in recent_macd[-3:]:
        bar_time = df["time"].iloc[d.later_bar_idx]
        print(f"      MACD {d.kind:18s} {bar_time.strftime('%m-%d %H:%M')}  "
              f"price={d.price_extremes[1]:.4f}")

print()
print("=" * 80)
print("AXIS 4: CROSS-ASSET + SYNTHETIC DXY")
print("=" * 80)

ca = ctx["cross_asset"]
midprices = {}
for sym in ["EURUSD", "USDJPY", "GBPUSD", "USDCHF"]:
    if sym in ca and "bid" in ca[sym]:
        midprices[sym] = (ca[sym]["bid"] + ca[sym]["ask"]) / 2
dxy = synthetic_dxy(midprices) if len(midprices) == 4 else None
if dxy:
    print(f"  Synthetic DXY proxy: {dxy:.2f}   (higher = stronger USD = headwind for XAG)")
print(f"  EURUSD: {midprices.get('EURUSD', 0):.4f}")
print()

print(f"  Cross-asset returns (1d / 5d / 20d):")
for sym in ["XAUUSD", "EURUSD", "USDJPY", "US500", "USTEC", "BTCUSD"]:
    if sym in ca and "d1_change_pct" in ca[sym] and ca[sym]["d1_change_pct"] is not None:
        d = ca[sym]
        print(f"    {sym:8s}  1d={d['d1_change_pct']:+5.2f}%  "
              f"5d={d['d5_change_pct']:+6.2f}%  20d={d['d20_change_pct']:+6.2f}%")

if "XAUUSD" in ca and "bid" in ca["XAUUSD"]:
    xau_mid = (ca["XAUUSD"]["bid"] + ca["XAUUSD"]["ask"]) / 2
    ratio = xau_mid / current
    print(f"\n  Gold-Silver Ratio: XAU/XAG = {xau_mid:.2f}/{current:.4f} = {ratio:.2f}")
    print(f"  (120d range: P5=48.6 / P50=61.3 / P95=65.2)")
    if ratio < 55:
        print(f"  -> Silver EXPENSIVE vs gold (mean-rev edge bias = short XAG / long XAU)")
    elif ratio > 65:
        print(f"  -> Silver CHEAP vs gold (mean-rev edge bias = long XAG / short XAU)")
    else:
        print(f"  -> Ratio fair, no pair-trade signal from ratio")

print()
print("=" * 80)
print("AXIS 5: SETUP CATEGORY CLASSIFICATION")
print("=" * 80)

extreme_count = sum(1 for r in results_by_tf.values()
                    if r["vr"] and r["vr"].label == "EXTREME")
elevated_count = sum(1 for r in results_by_tf.values()
                     if r["vr"] and r["vr"].label == "ELEVATED")
range_high_count = sum(1 for r in results_by_tf.values()
                       if r["rp_long"] and r["rp_long"]["range_pct"] > 80)
range_low_count = sum(1 for r in results_by_tf.values()
                      if r["rp_long"] and r["rp_long"]["range_pct"] < 20)

print(f"  TF count by vol regime:    EXTREME {extreme_count}/7   ELEVATED {elevated_count}/7")
print(f"  TFs at >80% of 60-bar range: {range_high_count}/7")
print(f"  TFs at <20% of 60-bar range: {range_low_count}/7")

d1_r = results_by_tf["D1"]
h4_r = results_by_tf["H4"]
m15_r = results_by_tf["M15"]
print()
print("  Setup category fingerprint:")

cat1_long = d1_r["trend"] == "UP_STACK" and h4_r["trend"] in ("UP_STACK", "MIXED")
cat1_short = d1_r["trend"] == "DOWN_STACK" and h4_r["trend"] in ("DOWN_STACK", "MIXED")
print(f"    Cat 1 (trend-follow): long={cat1_long}, short={cat1_short}")

# Cat 3 climax: high range + recent bearish divergence + vol elevated/normal
d1_at_top = d1_r["rp_long"] and d1_r["rp_long"]["range_pct"] > 75
h4_at_top = h4_r["rp_long"] and h4_r["rp_long"]["range_pct"] > 75
h4_bear_div = [d for d in find_divergences(ctx["bars"]["H4"], indicator="RSI", lookback_bars=80)
               if d.kind == "bearish" and d.later_bar_idx >= len(ctx["bars"]["H4"]) - 20]
d1_bear_div = [d for d in find_divergences(ctx["bars"]["D1"], indicator="RSI", lookback_bars=100)
               if d.kind == "bearish" and d.later_bar_idx >= len(ctx["bars"]["D1"]) - 15]
print(f"    Cat 3 (climax-exhaustion short):")
print(f"      D1 >75% of range: {d1_at_top}    H4 >75%: {h4_at_top}")
print(f"      D1 bearish RSI div recent: {len(d1_bear_div) > 0}")
print(f"      H4 bearish RSI div recent: {len(h4_bear_div) > 0}")

high_z_h4 = h4_r["sd20"] and abs(h4_r["sd20"]["z"]) > 1.5
high_z_d1 = d1_r["sd20"] and abs(d1_r["sd20"]["z"]) > 1.5
print(f"    Cat 4 (mean-reversion):")
print(f"      |z| > 1.5sd vs EMA20:  D1={high_z_d1}, H4={high_z_h4}")
print(f"      Plus signature on pair ratio (already shown above)")

cat2_signature = bb and bb["above_upper"] if (bb := h4_r.get("bb")) else False
print(f"    Cat 2 (failed-breakout): H4 above upper Bollinger band = {cat2_signature}")

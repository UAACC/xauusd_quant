"""Intraday-focused analysis for XAUUSD (or any symbol).

Different framework from swing analysis:
- D1/H4 = context only (bias)
- M15/M5/M1 = primary timeframe
- Session-aware: Asian / London / NY
- VWAP anchored at UTC midnight
- Opening range (first 30/60 min of London/NY open)
- Session high/low tracking
- Distance-to-VWAP signals
"""
import sys
import time
from pathlib import Path
from datetime import datetime, timezone, timedelta

import numpy as np
import pandas as pd
import talib

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from live_analysis.eval_trade import pull_live_context
from live_analysis.analyst_tools import (
    zscore, vol_regime, volume_profile, find_divergences, range_position,
)

SYMBOL = sys.argv[1] if len(sys.argv) > 1 else "XAUUSD"

# Session boundaries (UTC)
SESSIONS = {
    "Asian":  (22, 8),    # 22:00 -> 08:00 next day
    "London": (7, 16),    # 07:00 -> 16:00
    "NY":     (13, 21),   # 13:00 -> 21:00
}


def session_for_time(t: pd.Timestamp) -> str:
    """Identify which session this UTC time falls in (returns 'overlap' if 2)."""
    hour = t.hour
    in_london = 7 <= hour < 16
    in_ny = 13 <= hour < 21
    in_asian = hour >= 22 or hour < 8
    if in_london and in_ny:
        return "London/NY overlap"
    if in_london:
        return "London"
    if in_ny:
        return "NY"
    if in_asian:
        return "Asian"
    return "between"


def vwap_anchored(bars: pd.DataFrame, anchor_time: pd.Timestamp) -> pd.Series:
    """Compute VWAP anchored at anchor_time onwards. Uses tick_volume as proxy."""
    subset = bars[bars["time"] >= anchor_time].copy()
    if subset.empty:
        return pd.Series(dtype=float)
    typical = (subset["high"] + subset["low"] + subset["close"]) / 3
    cum_vol = subset["tick_volume"].cumsum()
    cum_pv = (typical * subset["tick_volume"]).cumsum()
    vwap = cum_pv / cum_vol
    vwap.index = subset["time"]
    return vwap


def session_levels(bars: pd.DataFrame, now_utc: pd.Timestamp) -> dict:
    """Compute today's session high/low/open for each session."""
    today_start = now_utc.normalize()  # 00:00 UTC today
    yesterday_start = today_start - pd.Timedelta(days=1)

    # Today's UTC day so far
    today_bars = bars[bars["time"] >= today_start]
    # Asian session: 22:00 yesterday -> 08:00 today
    asian_start = yesterday_start + pd.Timedelta(hours=22)
    asian_end = today_start + pd.Timedelta(hours=8)
    asian_bars = bars[(bars["time"] >= asian_start) & (bars["time"] < asian_end)]
    # London: 07:00 - 16:00 today
    london_bars = bars[
        (bars["time"] >= today_start + pd.Timedelta(hours=7)) &
        (bars["time"] < today_start + pd.Timedelta(hours=16))
    ]
    # NY: 13:00 - 21:00 today
    ny_bars = bars[
        (bars["time"] >= today_start + pd.Timedelta(hours=13)) &
        (bars["time"] < today_start + pd.Timedelta(hours=21))
    ]

    out = {}
    for name, df in [("Today_overall", today_bars), ("Asian", asian_bars),
                      ("London", london_bars), ("NY", ny_bars)]:
        if df.empty:
            out[name] = {"high": None, "low": None, "open": None, "close": None, "bars": 0}
        else:
            out[name] = {
                "high": float(df["high"].max()),
                "low": float(df["low"].min()),
                "open": float(df.iloc[0]["open"]),
                "close": float(df.iloc[-1]["close"]),
                "bars": len(df),
            }
    return out


def opening_range(bars: pd.DataFrame, session_open: pd.Timestamp, minutes: int = 30) -> dict:
    """Opening Range = high/low of first N minutes of a session."""
    end = session_open + pd.Timedelta(minutes=minutes)
    subset = bars[(bars["time"] >= session_open) & (bars["time"] < end)]
    if subset.empty:
        return {"high": None, "low": None, "completed": False, "bars": 0}
    return {
        "high": float(subset["high"].max()),
        "low": float(subset["low"].min()),
        "open": float(subset.iloc[0]["open"]),
        "completed": pd.Timestamp.now(tz="UTC") >= end,
        "bars": len(subset),
    }


# ============================================================================
# Pull data
# ============================================================================
t0 = time.perf_counter()
ctx = pull_live_context(SYMBOL, cross_asset_symbols=["EURUSD", "USDJPY"])
elapsed = (time.perf_counter() - t0) * 1000

now = pd.Timestamp.now(tz="UTC")
current = float(ctx["bars"]["M1"]["close"].iloc[-1])
print(f"# {SYMBOL} INTRADAY ANALYSIS")
print(f"# Snapshot: {now}  (pull {elapsed:.0f}ms)")
print(f"# Current: {current:.4f}  Spread: {ctx['live_spread_pts']:.0f}pt")
print(f"# Current session: {session_for_time(now)}\n")

# ============================================================================
# Section 1: Today's session map
# ============================================================================
print("=" * 80)
print("SECTION 1: SESSION LEVELS (UTC time-of-day)")
print("=" * 80)
m1 = ctx["bars"]["M1"]
m5 = ctx["bars"]["M5"]
m15 = ctx["bars"]["M15"]

# Use M5 for session-level computation
sl = session_levels(m5, now)
for name, d in sl.items():
    if d["bars"] == 0:
        print(f"  {name:18s} no bars yet")
    else:
        print(f"  {name:18s} O={d['open']:.2f}  H={d['high']:.2f}  L={d['low']:.2f}  "
              f"C={d['close']:.2f}  range=${d['high']-d['low']:.2f}  ({d['bars']} M5 bars)")

# ============================================================================
# Section 2: Today's VWAP (anchored at 00:00 UTC)
# ============================================================================
print()
print("=" * 80)
print("SECTION 2: VWAP analysis")
print("=" * 80)

today_start = now.normalize()
vwap_today = vwap_anchored(m5, today_start)
if not vwap_today.empty:
    current_vwap = float(vwap_today.iloc[-1])
    dist_vwap = current - current_vwap
    print(f"  Today VWAP (00:00 UTC anchor):  {current_vwap:.4f}")
    print(f"  Current vs VWAP:                {dist_vwap:+.4f} ({dist_vwap/current_vwap*100:+.3f}%)")

# Also Asian-session VWAP (anchored at 22:00 yesterday)
asian_anchor = (today_start - pd.Timedelta(days=1)) + pd.Timedelta(hours=22)
vwap_asian = vwap_anchored(m5, asian_anchor)
if not vwap_asian.empty:
    asian_vwap = float(vwap_asian.iloc[-1])
    dist_av = current - asian_vwap
    print(f"  Asian-anchored VWAP (since 22:00 UTC yesterday): {asian_vwap:.4f}")
    print(f"  Current vs Asian VWAP:          {dist_av:+.4f} ({dist_av/asian_vwap*100:+.3f}%)")

# ============================================================================
# Section 3: Opening Range (London + NY)
# ============================================================================
print()
print("=" * 80)
print("SECTION 3: OPENING RANGES")
print("=" * 80)

london_open = today_start + pd.Timedelta(hours=7)
ny_open = today_start + pd.Timedelta(hours=13)

for name, session_open in [("London", london_open), ("NY", ny_open)]:
    if now < session_open:
        eta_min = (session_open - now).total_seconds() / 60
        print(f"  {name:7s} open at {session_open.strftime('%H:%M')} UTC — in {eta_min:.0f} min")
        continue
    or_30 = opening_range(m5, session_open, minutes=30)
    or_60 = opening_range(m5, session_open, minutes=60)
    print(f"  {name:7s} 30-min OR:  H={or_30['high']}  L={or_30['low']}  "
          f"open={or_30['open']}  ({'completed' if or_30['completed'] else 'in progress'})")
    if or_60["bars"] > 0:
        print(f"  {name:7s} 60-min OR:  H={or_60['high']}  L={or_60['low']}  "
              f"({'completed' if or_60['completed'] else 'in progress'})")

# ============================================================================
# Section 4: Intraday momentum + structure
# ============================================================================
print()
print("=" * 80)
print("SECTION 4: INTRADAY MOMENTUM (M15 / M5 / M1)")
print("=" * 80)

for tf, df in [("M15", m15), ("M5", m5), ("M1", m1)]:
    closes = df["close"].astype(float).to_numpy()
    rsi = talib.RSI(closes, 14)
    # Last 6 closes
    last_6 = closes[-6:]
    last_rsi = rsi[-1]
    # Direction of last 6 bars
    direction = "UP" if last_6[-1] > last_6[0] else "DOWN" if last_6[-1] < last_6[0] else "FLAT"
    # ATR
    atr = talib.ATR(df["high"].astype(float).values, df["low"].astype(float).values,
                     closes, 14)
    print(f"  {tf:4s}  last close={closes[-1]:.4f}  RSI(14)={last_rsi:.1f}  "
          f"6-bar={last_6[-1]-last_6[0]:+.4f} ({direction})  ATR={atr[-1]:.4f}")

# Recent 1-hour momentum
m1_last_60 = m1.tail(60)
if len(m1_last_60) >= 30:
    print(f"\n  Last 60min on M1:  open={m1_last_60.iloc[0]['open']:.4f}  "
          f"high={m1_last_60['high'].max():.4f}  low={m1_last_60['low'].min():.4f}  "
          f"close={current:.4f}")
    print(f"  Net 60min move:  {current - m1_last_60.iloc[0]['open']:+.4f}")

# ============================================================================
# Section 5: Intraday key levels (priced relative to current)
# ============================================================================
print()
print("=" * 80)
print("SECTION 5: INTRADAY KEY LEVELS")
print("=" * 80)

levels = []
# Session levels
for name, d in sl.items():
    if d["high"] is not None:
        levels.append((d["high"], f"{name} session HIGH"))
        levels.append((d["low"],  f"{name} session LOW"))
# VWAP
if not vwap_today.empty:
    levels.append((float(vwap_today.iloc[-1]), "Today VWAP"))
if not vwap_asian.empty:
    levels.append((float(vwap_asian.iloc[-1]), "Asian VWAP"))
# H1 POC from broader analysis
h1_5d = ctx["bars"]["H1"][ctx["bars"]["H1"]["time"] >= (now - pd.Timedelta(days=5))]
if len(h1_5d) > 10:
    from live_analysis.analyst_tools import volume_profile as vp_fn
    vp = vp_fn(h1_5d, num_bins=30)
    levels.append((vp.poc, "H1 5d POC"))
    levels.append((vp.vah, "H1 5d VAH"))
    levels.append((vp.val, "H1 5d VAL"))

# Sort and filter to nearby
nearby = [(p, t) for p, t in levels if p is not None and abs(p - current) / current < 0.015]
nearby.sort(key=lambda x: x[0])

print(f"  Levels within ±1.5% of current ({current:.2f}):")
for price, tag in nearby:
    arrow = " <-- *current*" if abs(price - current) < 1.0 else ""
    print(f"    {price:>10.4f}  ({price - current:+7.2f})  {tag}{arrow}")

# ============================================================================
# Section 6: Intraday setup category checks
# ============================================================================
print()
print("=" * 80)
print("SECTION 6: INTRADAY SETUP CATEGORIES")
print("=" * 80)

asian_high = sl["Asian"]["high"]
asian_low = sl["Asian"]["low"]
today_high = sl["Today_overall"]["high"]
today_low = sl["Today_overall"]["low"]

vwap_today_v = float(vwap_today.iloc[-1]) if not vwap_today.empty else None

print(f"  Asian range:    {asian_low} - {asian_high}  (width=${asian_high-asian_low:.2f})" if asian_high else "  Asian range: incomplete")

print()
print("  Setup candidates for intraday:")
print(f"\n  A. Asian Range Break (ARB):")
if asian_high and asian_low:
    if current > asian_high:
        print(f"     STATUS: BREAKING UP — current {current} > Asian high {asian_high}")
    elif current < asian_low:
        print(f"     STATUS: BREAKING DOWN — current {current} < Asian low {asian_low}")
    else:
        dist_to_high = asian_high - current
        dist_to_low = current - asian_low
        print(f"     STATUS: in range. Dist to high: ${dist_to_high:.2f}, dist to low: ${dist_to_low:.2f}")

print(f"\n  B. VWAP-anchored mean reversion:")
if vwap_today_v:
    dist_vwap = current - vwap_today_v
    print(f"     Current is ${dist_vwap:+.2f} from VWAP {vwap_today_v:.2f}")
    if abs(dist_vwap) > 5:
        bias = "SHORT bias (above VWAP)" if dist_vwap > 0 else "LONG bias (below VWAP)"
        print(f"     Stretched > $5 -> mean-rev candidate {bias}")
    else:
        print(f"     Tight to VWAP -> no MR setup, wait for stretch")

print(f"\n  C. London Open momentum (07:00 UTC):")
if now < london_open:
    eta = (london_open - now).total_seconds() / 60
    print(f"     London opens in {eta:.0f} minutes — watch first 30min for direction commit")
elif (now - london_open).total_seconds() / 60 < 30:
    print(f"     London open IN PROGRESS — first 30min defining range. Wait to see commit.")
else:
    or_30 = opening_range(m5, london_open, 30)
    if or_30["high"]:
        if current > or_30["high"]:
            print(f"     ORB UP — current > 30min high {or_30['high']}")
        elif current < or_30["low"]:
            print(f"     ORB DOWN — current < 30min low {or_30['low']}")
        else:
            print(f"     INSIDE 30min ORB ({or_30['low']}-{or_30['high']})")

# Liquidity sweep check
print(f"\n  D. Liquidity sweep + reversal:")
m5_last_4 = m5.tail(4)
for _, b in m5_last_4.iterrows():
    if asian_high and b["high"] > asian_high and b["close"] < asian_high:
        print(f"     SWEEP UP detected at {b['time']}: high {b['high']:.4f} > Asian high {asian_high:.4f}, closed below")
        break
    if asian_low and b["low"] < asian_low and b["close"] > asian_low:
        print(f"     SWEEP DOWN detected at {b['time']}: low {b['low']:.4f} < Asian low {asian_low:.4f}, closed above")
        break
else:
    print(f"     No recent sweep in last 4 M5 bars")

# ============================================================================
# Section 7: Vol regime check
# ============================================================================
print()
print("=" * 80)
print("SECTION 7: INTRADAY VOL REGIME (M15 + M5)")
print("=" * 80)

vr_m15 = vol_regime(m15, period=14, lookback=200)
vr_m5 = vol_regime(m5, period=14, lookback=200)
print(f"  M15  ATR={vr_m15.atr_current:.4f}  regime={vr_m15.label}  percentile={vr_m15.percentile:.0f}")
print(f"  M5   ATR={vr_m5.atr_current:.4f}  regime={vr_m5.label}  percentile={vr_m5.percentile:.0f}")
print()
print(f"  Implication:")
if vr_m15.label in ("LOW", "NORMAL"):
    print(f"    M15 vol is {vr_m15.label} -- intraday moves likely contained")
elif vr_m15.label == "ELEVATED":
    print(f"    M15 vol ELEVATED -- expect $5-15 moves per 15min, set wider stops")
else:
    print(f"    M15 vol EXTREME -- caution, news/event likely; spreads will widen")

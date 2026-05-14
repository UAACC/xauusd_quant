"""XAGUSD analysis verification - check every claim against fresh data."""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import pandas as pd
import numpy as np
import talib
from datetime import datetime, timezone, timedelta
from mt5_connect import init_mt5_live
import MetaTrader5 as mt5
from quant.config import get_live_mt5_path
from quant.structure.swings import detect_swings
import yfinance as yf
from live_analysis.analyst_tools import volume_profile

init_mt5_live(terminal_path=get_live_mt5_path(), allow_orders=False)

# 1. CURRENT PRICE
mt5.symbol_select("XAGUSD", True)
tick = mt5.symbol_info_tick("XAGUSD")
info = mt5.symbol_info("XAGUSD")
current_mid = (tick.bid + tick.ask) / 2
spread_pt = (tick.ask - tick.bid) / info.point

print("CLAIM 1: CURRENT PRICE")
print(f"  bid={tick.bid:.4f}  ask={tick.ask:.4f}  mid={current_mid:.4f}")
print(f"  spread={spread_pt:.0f}pt  tick_ts={datetime.fromtimestamp(tick.time, tz=timezone.utc)}")

# 2. D1 + cross-source
print("\nCLAIM 2: D1 LAST 3 (cross-verify Yahoo SI=F)")
end = datetime.now(tz=timezone.utc)
d1_rates = mt5.copy_rates_range("XAGUSD", mt5.TIMEFRAME_D1, end - timedelta(days=10), end)
d1 = pd.DataFrame(d1_rates)
d1["time"] = pd.to_datetime(d1["time"], unit="s")
for _, b in d1.tail(3).iterrows():
    print(f"  MT5  {b['time'].date()}: O={b['open']:.4f} H={b['high']:.4f} L={b['low']:.4f} C={b['close']:.4f}")
yf_data = yf.Ticker("SI=F").history(period="5d")
for _, b in yf_data.tail(3).iterrows():
    print(f"  YF   {b.name.date()}: O={b['Open']:.4f} H={b['High']:.4f} L={b['Low']:.4f} C={b['Close']:.4f}")

# 3. D1 EMA stack
print("\nCLAIM 3: D1 EMA STACK")
d1_long_rates = mt5.copy_rates_range("XAGUSD", mt5.TIMEFRAME_D1,
                                       end - timedelta(days=120), end)
d1_long = pd.DataFrame(d1_long_rates)
d1l_closes = d1_long["close"].astype(float).values
ema9 = talib.EMA(d1l_closes, 9)[-1]
ema20 = talib.EMA(d1l_closes, 20)[-1]
ema50 = talib.EMA(d1l_closes, 50)[-1]
print(f"  D1 last close: {d1l_closes[-1]:.4f}")
print(f"  D1 EMA9:  {ema9:.4f}")
print(f"  D1 EMA20: {ema20:.4f}")
print(f"  D1 EMA50: {ema50:.4f}")
print(f"  EMA9 > EMA20 > EMA50? {ema9 > ema20 > ema50}")
print(f"  EMA9 - EMA20 = {ema9 - ema20:+.4f}  EMA20 - EMA50 = {ema20 - ema50:+.4f}")

# 4. H4 swings
print("\nCLAIM 4: H4 SWINGS (n=2 fractal, last 20 days)")
h4_rates = mt5.copy_rates_range("XAGUSD", mt5.TIMEFRAME_H4,
                                  end - timedelta(days=20), end)
h4 = pd.DataFrame(h4_rates)
h4["time"] = pd.to_datetime(h4["time"], unit="s").dt.tz_localize("UTC")
swings = detect_swings(h4, n_left=2, n_right=2)
print(f"  Last 10 H4 swings:")
for s in swings[-10:]:
    print(f"    {s.kind:5s}  {s.time}  {s.price:.4f}")
print(f"  Most recent H4 bar: {h4['time'].iloc[-1]}")
print(f"  fractal n=2 confirmation gap: 2 bars after each swing")

# 5. H4 HL 85.61 break check
print("\nCLAIM 5: Has H4 HL 85.61 been broken?")
hl_target = 85.608
post = h4[h4["time"] > pd.Timestamp("2026-05-13 05:00", tz="UTC")]
min_low_since = float(post["low"].min())
print(f"  H4 HL @ 5/13 05:00: {hl_target}")
print(f"  Min low since: {min_low_since:.4f}  (diff {min_low_since - hl_target:+.4f})")
print(f"  Broken? {min_low_since < hl_target}")
if min_low_since < hl_target:
    breaking = post[post["low"] < hl_target].iloc[0]
    print(f"  First breaking H4 bar: {breaking['time']}")
    print(f"    O={breaking['open']:.4f} H={breaking['high']:.4f} L={breaking['low']:.4f} C={breaking['close']:.4f}")
    print(f"  CLOSE below 85.61? {breaking['close'] < hl_target}")

# 6. H1 5d Volume Profile
print("\nCLAIM 6: H1 5-DAY VOLUME PROFILE (CAVEAT: tick_volume proxy)")
h1_rates = mt5.copy_rates_range("XAGUSD", mt5.TIMEFRAME_H1,
                                  end - timedelta(days=5), end)
h1 = pd.DataFrame(h1_rates)
h1["time"] = pd.to_datetime(h1["time"], unit="s").dt.tz_localize("UTC")
print(f"  Sample: {len(h1)} H1 bars (last 5d)")
print(f"  Range: {h1['low'].min():.4f} - {h1['high'].max():.4f}")
vp = volume_profile(h1, num_bins=30)
print(f"  POC: {vp.poc:.4f}  VAH: {vp.vah:.4f}  VAL: {vp.val:.4f}")
print(f"  CAVEAT: tick_volume = # ticks, not actual contracts. Approximate not absolute.")

# 7. Z-scores per TF
print("\nCLAIM 7: Z-SCORES (current vs prior 50 closes per TF)")
def z_of(closes, n=50):
    if len(closes) < n + 1:
        return None
    window = closes[-n-1:-1]
    return (closes[-1] - np.mean(window)) / np.std(window, ddof=1)

for tf_name, tf_const in [("D1", mt5.TIMEFRAME_D1), ("H4", mt5.TIMEFRAME_H4),
                            ("H1", mt5.TIMEFRAME_H1), ("M30", mt5.TIMEFRAME_M30),
                            ("M15", mt5.TIMEFRAME_M15), ("M5", mt5.TIMEFRAME_M5)]:
    rates = mt5.copy_rates_range("XAGUSD", tf_const, end - timedelta(days=10), end)
    df = pd.DataFrame(rates)
    cls = df["close"].astype(float).values
    if len(cls) >= 51:
        z = z_of(cls)
        # Also EMA stack
        e9 = talib.EMA(cls, 9)[-1]
        e20 = talib.EMA(cls, 20)[-1]
        e50 = talib.EMA(cls, 50)[-1] if len(cls) >= 50 else None
        stack = "UP" if (e50 is not None and e9 > e20 > e50) else "DN" if (e50 is not None and e9 < e20 < e50) else "MX"
        print(f"  {tf_name:3s}  z={z:+.3f}sd  close={cls[-1]:.4f}  EMA20={e20:.4f}  stack={stack}")

# 8. Ratio
print("\nCLAIM 8: XAU/XAG RATIO")
mt5.symbol_select("XAUUSD", True)
xau = mt5.symbol_info_tick("XAUUSD")
xau_mid = (xau.bid + xau.ask) / 2
ratio = xau_mid / current_mid
print(f"  XAUUSD: {xau_mid:.4f}  XAGUSD: {current_mid:.4f}  Ratio: {ratio:.4f}")

# 9. H1 04:00 bar + RSI
print("\nCLAIM 9: H1 5/14 04:00 BAR + RSI")
h1_target = h1[h1["time"] == pd.Timestamp("2026-05-14 04:00", tz="UTC")]
if not h1_target.empty:
    b = h1_target.iloc[0]
    print(f"  Bar 5/14 04:00:  O={b['open']:.4f} H={b['high']:.4f} L={b['low']:.4f} C={b['close']:.4f}")
    # Compute RSI fully and look at this bar
    h1_closes = h1["close"].astype(float).values
    h1_rsi = talib.RSI(h1_closes, 14)
    target_idx = h1_target.index[0]
    print(f"  H1 RSI(14) at that bar: {h1_rsi[target_idx]:.2f}")
    # Recent prior lows for divergence check
    prior_lows = h1.iloc[max(0, target_idx-20):target_idx]
    print(f"  Prior 20 H1 lows: min={prior_lows['low'].min():.4f}")
    min_idx = prior_lows["low"].idxmin()
    if pd.notna(min_idx):
        prior_min_rsi = h1_rsi[min_idx]
        print(f"  Min-low bar idx {min_idx} ({h1['time'].iloc[min_idx]}): low={h1['low'].iloc[min_idx]:.4f} RSI={prior_min_rsi:.2f}")
        print(f"  Comparing target (idx {target_idx}, low {b['low']:.4f}, RSI {h1_rsi[target_idx]:.2f}):")
        print(f"    Price: {b['low']:.4f} vs {h1['low'].iloc[min_idx]:.4f} = " +
              ("HIGHER LOW" if b['low'] > h1['low'].iloc[min_idx] else "LOWER LOW"))
        print(f"    RSI:   {h1_rsi[target_idx]:.2f} vs {prior_min_rsi:.2f} = " +
              ("HIGHER RSI" if h1_rsi[target_idx] > prior_min_rsi else "LOWER RSI"))

mt5.shutdown()

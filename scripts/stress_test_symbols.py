"""
scripts/stress_test_symbols.py — run the BOS-reversal strategy across
multiple symbols with proportionally-scaled SL/TP, to test whether the
edge generalizes beyond XAUUSD/USTEC or is asset-specific.

For each symbol we compute SL/TP as a fixed % of the typical price at the
start of the backtest window. This normalizes for the different price
scales (XAUUSD ~$4700 vs BTCUSD ~$80000) without per-symbol tuning that
would risk data dredging.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from quant.backtest.event_engine import run_event_backtest
from quant.data.parquet import read_bars_month
from quant.strategies.bos_reversal import detect_bos_reversal_signals

DATA_ROOT = REPO_ROOT / "data"

# Per-symbol cost-model parameters. Values from earlier spec probe.
#   contract_size = $ PnL per 1 unit price move per lot
#   cost_rt_per_lot = round-trip spread+commission USD per lot
#                    (rough; index/crypto are spread-only on IC Raw Spread)
SYMBOLS = {
    "XAUUSD": dict(contract_size=100.0, cost_rt=12.0,  pct_sl=0.0043),
    "USTEC":  dict(contract_size=1.0,   cost_rt=0.5,   pct_sl=0.0043),
    "XAGUSD": dict(contract_size=1000.0, cost_rt=10.0, pct_sl=0.0043),
    "XTIUSD": dict(contract_size=100.0, cost_rt=2.0,   pct_sl=0.0043),
    "US500":  dict(contract_size=1.0,   cost_rt=1.0,   pct_sl=0.0043),
    "BTCUSD": dict(contract_size=1.0,   cost_rt=20.0,  pct_sl=0.0043),
}


def _load_months(symbol: str, timeframe: str, start, end) -> pd.DataFrame:
    frames = []
    y, m = start
    while (y, m) <= end:
        path = DATA_ROOT / "bars" / symbol / timeframe / f"{y:04d}" / f"{y:04d}-{m:02d}.parquet"
        if path.exists():
            frames.append(read_bars_month(DATA_ROOT, symbol, timeframe, y, m))
        m += 1
        if m > 12:
            m, y = 1, y + 1
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True).sort_values("time").reset_index(drop=True)


def main() -> int:
    start = (2022, 3)
    end = (2026, 5)

    print(f"{'symbol':<8}{'SL$':>8}{'TP$':>8}{'BE$':>8}"
          f"{'sig':>5}{'TP':>4}{'SL':>4}{'BE':>4}"
          f"{'win%':>7}{'$/trade':>10}{'gross':>10}{'net':>10}{'sharpe':>9}")
    print("-" * 100)

    rows = []
    for sym, params in SYMBOLS.items():
        h4 = _load_months(sym, "H4", start, end)
        h1 = _load_months(sym, "H1", start, end)
        m15 = _load_months(sym, "M15", start, end)
        if h4.empty or h1.empty or m15.empty:
            print(f"{sym:<8}  (no data)")
            continue

        # Use LATEST price (end of sample, closest to "live" reference) for
        # SL/TP scaling. Median would underweight late-period prices which
        # may have risen substantially (gold ~$2300 -> $4700 over 4y).
        typical_price = float(h4["close"].iloc[-1])
        sl_dist = typical_price * params["pct_sl"]
        tp_dist = sl_dist * 2.0  # R:R 2:1
        be_trigger = sl_dist * 0.75

        signals = detect_bos_reversal_signals(
            h4, m15, h1_bars=h1,
            sl_distance=sl_dist, tp_distance=tp_dist,
        )

        report = run_event_backtest(
            signals, m15,
            initial_balance=10_000.0,
            fixed_lots=0.01,
            contract_size=params["contract_size"],
            be_trigger_distance=be_trigger,
            cost_per_roundtrip_usd_per_lot=params["cost_rt"],
        )

        n = report.n_trades_closed
        avg = report.avg_trade_net if n else 0.0
        print(
            f"{sym:<8}{sl_dist:>8.2f}{tp_dist:>8.2f}{be_trigger:>8.2f}"
            f"{n:>5}{report.n_tp:>4}{report.n_sl:>4}{report.n_be:>4}"
            f"{report.win_rate*100:>6.1f}%"
            f"{avg:>+10.2f}{report.total_gross_pnl:>+10.2f}{report.total_net_pnl:>+10.2f}"
            f"{report.sharpe_annual:>+9.2f}"
        )
        rows.append((sym, n, report.win_rate, avg, report.sharpe_annual, report.total_net_pnl))

    # Aggregate stats across symbols
    print()
    total_sig = sum(r[1] for r in rows)
    weighted_win = sum(r[1] * r[2] for r in rows) / total_sig if total_sig else 0
    weighted_avg = sum(r[1] * r[3] for r in rows) / total_sig if total_sig else 0
    total_net = sum(r[5] for r in rows)
    n_profitable = sum(1 for r in rows if r[5] > 0)
    print("=== aggregate across all symbols ===")
    print(f"  total signals      : {total_sig}")
    print(f"  weighted win rate  : {weighted_win*100:.1f}%")
    print(f"  weighted edge      : ${weighted_avg:+.2f} per 0.01-lot trade")
    print(f"  total net (sum)    : ${total_net:+.2f}")
    print(f"  profitable symbols : {n_profitable} / {len(rows)}")

    return 0


if __name__ == "__main__":
    sys.exit(main())

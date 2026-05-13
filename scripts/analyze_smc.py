"""scripts/analyze_smc.py — break down the SMC backtest into raw vs compound,
year-by-year, equity curve shape.

Three diagnostics for the user's question "is the +484% real edge, or
compound + lucky-order artifact?":

1. RAW MODE  — fixed 0.01 lot every trade. Removes compounding entirely.
               If raw mode shows +EV, the strategy itself has an edge.
               If raw mode shows ~0 or negative, the +484% is purely
               compound + ordering luck.
2. YEAR SLICE — group by calendar year. Consistent edge means similar
               win-rate and net-positive across years. Concentrated edge
               (one year carries everything) means high path-dependence
               risk.
3. EQUITY CURVE — peak, trough, max drawdown depth + time-underwater.
               Tells us whether DDs are short and recoverable or long
               and crippling.
"""
from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from quant.backtest.event_engine import run_event_backtest
from quant.data.parquet import read_bars_month
from quant.strategies.bos_reversal import detect_bos_reversal_signals


def _load_months(symbol: str, timeframe: str, start: tuple[int, int], end: tuple[int, int]) -> pd.DataFrame:
    frames = []
    y, m = start
    while (y, m) <= end:
        path = REPO_ROOT / "data" / "bars" / symbol / timeframe / f"{y:04d}" / f"{y:04d}-{m:02d}.parquet"
        if path.exists():
            frames.append(read_bars_month(REPO_ROOT / "data", symbol, timeframe, y, m))
        m += 1
        if m > 12:
            m, y = 1, y + 1
    return pd.concat(frames, ignore_index=True).sort_values("time").reset_index(drop=True)


def yearly_breakdown(trades) -> str:
    by_year = defaultdict(list)
    for t in trades:
        by_year[t.entry_time.year].append(t)
    lines = [
        f"  {'year':<6} {'n':>3} {'TP':>3} {'SL':>3} {'BE':>3} {'win%':>6} "
        f"{'gross':>10} {'net':>10} {'avg_net':>9}",
    ]
    for y in sorted(by_year):
        ts = by_year[y]
        n = len(ts)
        n_tp = sum(1 for t in ts if t.exit_reason == "tp")
        n_sl = sum(1 for t in ts if t.exit_reason == "sl")
        n_be = sum(1 for t in ts if t.exit_reason == "be")
        wr = sum(1 for t in ts if t.net_pnl > 0) / n * 100
        gross = sum(t.gross_pnl for t in ts)
        net = sum(t.net_pnl for t in ts)
        avg_net = net / n
        lines.append(
            f"  {y:<6} {n:>3} {n_tp:>3} {n_sl:>3} {n_be:>3} {wr:>5.1f}% "
            f"{gross:>+10.2f} {net:>+10.2f} {avg_net:>+9.2f}"
        )
    return "\n".join(lines)


def equity_curve_analysis(trades, initial_balance: float) -> str:
    if not trades:
        return "  (no trades)"
    eq = [(trades[0].entry_time, initial_balance)]
    for t in trades:
        eq.append((t.exit_time, t.balance_after))

    peak = (eq[0][0], eq[0][1])
    trough = (eq[0][0], eq[0][1])
    max_dd = 0.0
    max_dd_start = None
    max_dd_end = None
    running_max = eq[0][1]
    running_max_t = eq[0][0]
    cur_dd_start = None
    for ts, bal in eq:
        if bal > running_max:
            running_max = bal
            running_max_t = ts
            cur_dd_start = None
            if bal > peak[1]:
                peak = (ts, bal)
        dd = bal - running_max
        if dd < 0 and cur_dd_start is None:
            cur_dd_start = running_max_t
        if dd < max_dd:
            max_dd = dd
            max_dd_start = cur_dd_start
            max_dd_end = ts
        if bal < trough[1]:
            trough = (ts, bal)

    underwater_days = (max_dd_end - max_dd_start).days if max_dd_start else 0

    lines = [
        f"  initial   : ${initial_balance:>12,.2f}  @ {eq[0][0]}",
        f"  peak      : ${peak[1]:>12,.2f}  @ {peak[0]}",
        f"  trough    : ${trough[1]:>12,.2f}  @ {trough[0]}",
        f"  final     : ${eq[-1][1]:>12,.2f}  @ {eq[-1][0]}",
        f"  max DD    : ${max_dd:>+12,.2f}  ({max_dd / max(running_max, 1) * 100:+.1f}% from peak)",
        f"  DD period : {max_dd_start}  →  {max_dd_end}  ({underwater_days} days underwater)",
    ]
    return "\n".join(lines)


def run_mode(label: str, signals, m15, **kwargs):
    print("\n" + "=" * 90)
    print(f"  {label}")
    print("=" * 90)
    rep = run_event_backtest(signals=signals, bars=m15, **kwargs)
    print(rep.summary())
    print(f"\n  --- year-by-year ---")
    print(yearly_breakdown(rep.trades))
    print(f"\n  --- equity curve ---")
    print(equity_curve_analysis(rep.trades, rep.initial_balance))


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--symbol", default="XAUUSD")
    ap.add_argument("--sl-distance", type=float, default=20.0,
                    help="SL distance in price units (XAUUSD 20, USTEC ~125)")
    ap.add_argument("--tp-distance", type=float, default=40.0,
                    help="TP distance in price units (XAUUSD 40, USTEC ~250)")
    ap.add_argument("--contract-size", type=float, default=100.0,
                    help="$ PnL per 1 unit price move per lot (XAUUSD 100, USTEC 1)")
    ap.add_argument("--be-trigger", type=float, default=15.0,
                    help="price move before SL moves to breakeven (XAUUSD 15, USTEC ~94 = 75%% of SL)")
    ap.add_argument("--cost-rt", type=float, default=12.0,
                    help="round-trip cost USD per lot (XAUUSD 12, USTEC ~0.1)")
    ap.add_argument("--use-atr-stops", action="store_true",
                    help="ATR-scale SL/TP instead of fixed dollar distance")
    ap.add_argument("--atr-sl-mult", type=float, default=1.5)
    ap.add_argument("--atr-tp-mult", type=float, default=3.0)
    ap.add_argument("--atr-period", type=int, default=14)
    args = ap.parse_args()

    print(f"loading {args.symbol} H4 + H1 + M15 bars: 2022-03 -> 2026-05 (50 months)")
    h4 = _load_months(args.symbol, "H4", (2022, 3), (2026, 5))
    h1 = _load_months(args.symbol, "H1", (2022, 3), (2026, 5))
    m15 = _load_months(args.symbol, "M15", (2022, 3), (2026, 5))
    print(f"  H4  : {len(h4):,}   H1 : {len(h1):,}   M15 : {len(m15):,}")

    if args.use_atr_stops:
        print(f"\nstrategy params: ATR-scaled (period={args.atr_period}, "
              f"SL×{args.atr_sl_mult:g}, TP×{args.atr_tp_mult:g})  "
              f"contract_size={args.contract_size}  be_trigger=${args.be_trigger:.2f}  "
              f"cost_rt=${args.cost_rt:.2f}/lot")
    else:
        print(f"\nstrategy params: SL=${args.sl_distance:.2f}  TP=${args.tp_distance:.2f}  "
              f"contract_size={args.contract_size}  be_trigger=${args.be_trigger:.2f}  "
              f"cost_rt=${args.cost_rt:.2f}/lot")
    signals = detect_bos_reversal_signals(
        h4, m15, h1_bars=h1,
        sl_distance=args.sl_distance, tp_distance=args.tp_distance,
        use_atr_stops=args.use_atr_stops,
        atr_sl_mult=args.atr_sl_mult, atr_tp_mult=args.atr_tp_mult,
        atr_period=args.atr_period,
    )
    print(f"  {len(signals)} A-grade signals")

    common = dict(
        contract_size=args.contract_size,
        be_trigger_distance=args.be_trigger,
        cost_per_roundtrip_usd_per_lot=args.cost_rt,
    )

    # Mode A: 20% risk, compounding
    run_mode(
        "MODE A — 20% risk-based, compounding (the original report)",
        signals, m15, initial_balance=10_000.0, risk_pct=0.20, **common,
    )

    # Mode B: fixed 0.01 lot, no compound effect
    run_mode(
        "MODE B — fixed 0.01 lot, no compound (raw strategy edge)",
        signals, m15, initial_balance=10_000.0, fixed_lots=0.01, **common,
    )

    # Mode C: 2% risk, compounding (a more realistic sizing for validation)
    run_mode(
        "MODE C — 2% risk, compounding (conservative sizing for validation period)",
        signals, m15, initial_balance=10_000.0, risk_pct=0.02, **common,
    )

    return 0


if __name__ == "__main__":
    sys.exit(main())

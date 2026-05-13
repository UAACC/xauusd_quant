"""
scripts/walk_forward_smc.py — partition the sample into rolling windows and
run the SMC BOS-reversal strategy independently on each, to test whether the
edge is stable across non-overlapping time periods or concentrated in 1-2
lucky stretches.

Because this strategy has NO fitted parameters (all rules are fixed,
derived from the friend's verbal spec), "walk-forward" here means
**windowed re-evaluation**, not parameter re-fitting:

    Window 1:   2022-03 .. 2022-08
    Window 2:   2022-09 .. 2023-02
    Window 3:   2023-03 .. 2023-08
    ...

Each window is a fresh $10k account using the same rule set. Per-window
metrics let us see:

    - is the edge persistent (most windows positive)?
    - or concentrated (1 huge winning window, others flat/negative)?
    - what's the Sharpe variance across windows?

Strategy edge is meaningful only when it survives this test.

Usage::

    python scripts/walk_forward_smc.py
    python scripts/walk_forward_smc.py --window 6 --start 2022-03 --end 2026-05
    python scripts/walk_forward_smc.py --window 12 --fixed-lots 0.01
"""

from __future__ import annotations

import argparse
import statistics
import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from quant.backtest.event_engine import run_event_backtest  # noqa: E402
from quant.data.parquet import read_bars_month  # noqa: E402
from quant.strategies.bos_reversal import detect_bos_reversal_signals  # noqa: E402

DATA_ROOT = REPO_ROOT / "data"
DEFAULT_SYMBOL = "XAUUSD"


def _parse_ym(s: str) -> tuple[int, int]:
    y, m = s.split("-")
    return int(y), int(m)


def _add_months(ym: tuple[int, int], n: int) -> tuple[int, int]:
    """Return (year, month) shifted forward by n months."""
    y, m = ym
    total = y * 12 + (m - 1) + n
    return total // 12, total % 12 + 1


def _ym_to_ts(ym: tuple[int, int]) -> pd.Timestamp:
    return pd.Timestamp(f"{ym[0]:04d}-{ym[1]:02d}-01", tz="UTC")


def _load_months(symbol: str, timeframe: str, start_ym: tuple[int, int], end_ym: tuple[int, int]) -> pd.DataFrame:
    frames = []
    cur = start_ym
    while cur <= end_ym:
        path = DATA_ROOT / "bars" / symbol / timeframe / f"{cur[0]:04d}" / f"{cur[0]:04d}-{cur[1]:02d}.parquet"
        if path.exists():
            frames.append(read_bars_month(DATA_ROOT, symbol, timeframe, cur[0], cur[1]))
        cur = _add_months(cur, 1)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True).sort_values("time").reset_index(drop=True)


def _windows(start_ym: tuple[int, int], end_ym: tuple[int, int], size_months: int) -> list[tuple[tuple[int, int], tuple[int, int]]]:
    out = []
    cur = start_ym
    while True:
        win_end = _add_months(cur, size_months)
        if win_end > end_ym:
            break
        out.append((cur, win_end))
        cur = win_end
    return out


def _slice(df: pd.DataFrame, start_ym: tuple[int, int], end_ym: tuple[int, int]) -> pd.DataFrame:
    if df.empty:
        return df
    start_ts = _ym_to_ts(start_ym)
    end_ts = _ym_to_ts(end_ym)
    mask = (df["time"] >= start_ts) & (df["time"] < end_ts)
    return df[mask].reset_index(drop=True)


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--symbol", default=DEFAULT_SYMBOL)
    ap.add_argument("--window", type=int, default=6,
                    help="Window size in months (default 6)")
    ap.add_argument("--start", default="2022-03")
    ap.add_argument("--end", default="2026-05")
    ap.add_argument("--initial-balance", type=float, default=10_000.0)
    ap.add_argument("--fixed-lots", type=float, default=0.01)
    ap.add_argument("--risk-based", action="store_true")
    ap.add_argument("--sl-distance", type=float, default=20.0,
                    help="SL price distance (XAUUSD 20, USTEC 125)")
    ap.add_argument("--tp-distance", type=float, default=40.0,
                    help="TP price distance (XAUUSD 40, USTEC 250)")
    ap.add_argument("--contract-size", type=float, default=100.0,
                    help="contract size (XAUUSD 100, USTEC 1)")
    ap.add_argument("--be-trigger", type=float, default=15.0,
                    help="BE trigger price move (XAUUSD 15, USTEC ~94)")
    ap.add_argument("--cost-rt", type=float, default=12.0)
    return ap.parse_args()


def main() -> int:
    args = parse_args()
    start_ym = _parse_ym(args.start)
    end_ym = _parse_ym(args.end)

    print(f"loading {args.symbol} H4 + H1 + M15 bars: {args.start} -> {args.end}")
    h4 = _load_months(args.symbol, "H4", start_ym, end_ym)
    h1 = _load_months(args.symbol, "H1", start_ym, end_ym)
    m15 = _load_months(args.symbol, "M15", start_ym, end_ym)
    print(f"  H4 {len(h4):,}   H1 {len(h1):,}   M15 {len(m15):,}")

    wins = _windows(start_ym, end_ym, args.window)
    print(f"\n{args.window}-month rolling windows: {len(wins)}")
    fixed_lots = None if args.risk_based else args.fixed_lots
    sizing_label = "risk-based" if fixed_lots is None else f"{fixed_lots} lots/trade"
    print(f"sizing: {sizing_label}   initial: ${args.initial_balance:,.0f}")
    print()

    header = (
        f"{'window':<22}{'n':>4}{'TP':>4}{'SL':>4}{'BE':>4}"
        f"{'win%':>7}{'gross':>12}{'net':>12}{'sharpe':>9}{'maxDD':>10}"
    )
    print(header)
    print("-" * len(header))

    per_window: list[dict] = []
    for ws, we in wins:
        h4_w = _slice(h4, ws, we)
        h1_w = _slice(h1, ws, we)
        m15_w = _slice(m15, ws, we)
        label = f"{ws[0]:04d}-{ws[1]:02d}..{we[0]:04d}-{we[1]:02d}"
        if h4_w.empty or h1_w.empty or m15_w.empty:
            print(f"{label:<22}  (no data)")
            continue
        signals = detect_bos_reversal_signals(
            h4_w, m15_w, h1_bars=h1_w,
            sl_distance=args.sl_distance, tp_distance=args.tp_distance,
        )
        if not signals:
            print(f"{label:<22}{0:>4}{0:>4}{0:>4}{0:>4}{'-':>7}{0.0:>+12.2f}{0.0:>+12.2f}{0.0:>+9.2f}{0.0:>+10.2f}")
            per_window.append({"label": label, "n": 0, "net": 0.0, "sharpe": 0.0, "max_dd": 0.0})
            continue
        report = run_event_backtest(
            signals, m15_w,
            initial_balance=args.initial_balance,
            fixed_lots=fixed_lots,
            contract_size=args.contract_size,
            be_trigger_distance=args.be_trigger,
            cost_per_roundtrip_usd_per_lot=args.cost_rt,
        )
        print(
            f"{label:<22}{report.n_trades_closed:>4}{report.n_tp:>4}{report.n_sl:>4}{report.n_be:>4}"
            f"{report.win_rate*100:>6.1f}%"
            f"{report.total_gross_pnl:>+12.2f}{report.total_net_pnl:>+12.2f}"
            f"{report.sharpe_annual:>+9.2f}{report.max_drawdown:>+10.2f}"
        )
        per_window.append({
            "label": label,
            "n": report.n_trades_closed,
            "net": report.total_net_pnl,
            "sharpe": report.sharpe_annual,
            "max_dd": report.max_drawdown,
        })

    print()
    nets = [w["net"] for w in per_window]
    sharpes = [w["sharpe"] for w in per_window if w["n"] > 0]
    n_pos = sum(1 for n in nets if n > 0)
    n_neg = sum(1 for n in nets if n < 0)
    n_zero = sum(1 for n in nets if n == 0)
    print("=== stability summary ===")
    print(f"  total windows           : {len(per_window)}")
    print(f"  positive net            : {n_pos}")
    print(f"  negative net            : {n_neg}")
    print(f"  zero (no trades)        : {n_zero}")
    print(f"  median net per window   : ${statistics.median(nets):>+10.2f}")
    print(f"  mean net per window     : ${statistics.fmean(nets):>+10.2f}")
    print(f"  net std (across wins)   : ${statistics.stdev(nets) if len(nets) > 1 else 0:>+10.2f}")
    print(f"  sum (all windows)       : ${sum(nets):>+10.2f}")
    if sharpes:
        print(f"  median sharpe (n>0)     : {statistics.median(sharpes):>+6.2f}")
        print(f"  sharpe std (n>0)        : {statistics.stdev(sharpes) if len(sharpes) > 1 else 0:>+6.2f}")

    # OOS-style split: first half train (build conviction), second half test
    half = len(per_window) // 2
    if half >= 2:
        in_sample = per_window[:half]
        out_sample = per_window[half:]
        in_net = sum(w["net"] for w in in_sample)
        out_net = sum(w["net"] for w in out_sample)
        in_pos = sum(1 for w in in_sample if w["net"] > 0)
        out_pos = sum(1 for w in out_sample if w["net"] > 0)
        print()
        print("=== in-sample vs out-of-sample split (first/second half) ===")
        print(f"  in-sample  ({half} wins) : net ${in_net:>+10.2f}   positive {in_pos}/{half}")
        print(f"  out-sample ({len(out_sample)} wins) : net ${out_net:>+10.2f}   positive {out_pos}/{len(out_sample)}")
        if in_net > 0 and out_net > 0:
            print(f"  -> edge persists OOS")
        elif in_net > 0 >= out_net:
            print(f"  -> edge concentrated in in-sample; OOS failure (likely overfit / lucky in-sample)")
        elif out_net > 0 >= in_net:
            print(f"  -> edge appears only in OOS; unusual, look for regime change")
        else:
            print(f"  -> edge unclear / negative in both halves")

    return 0


if __name__ == "__main__":
    sys.exit(main())

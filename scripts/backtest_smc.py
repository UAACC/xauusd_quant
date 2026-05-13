"""scripts/backtest_smc.py — end-to-end SMC BOS-reversal backtest.

Loads H4 + M15 XAUUSD bars from the data lake, detects A-grade BOS-reversal
long signals, runs them through the event-driven backtest engine, prints a
trade-by-trade ledger plus aggregate stats.

Usage::

    python scripts/backtest_smc.py
    python scripts/backtest_smc.py --start 2026-04 --end 2026-05
    python scripts/backtest_smc.py --balance 5000 --risk 0.10
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from quant.backtest.event_engine import run_event_backtest
from quant.data.parquet import read_bars_month
from quant.strategies.bos_reversal import detect_bos_reversal_signals


DATA_ROOT = REPO_ROOT / "data"
DEFAULT_SYMBOL = "XAUUSD"


def _load_months(symbol: str, timeframe: str, start: tuple[int, int], end: tuple[int, int]) -> pd.DataFrame:
    """Load + concat parquet bars across an inclusive month range."""
    frames: list[pd.DataFrame] = []
    y, m = start
    while (y, m) <= end:
        path = DATA_ROOT / "bars" / symbol / timeframe / f"{y:04d}" / f"{y:04d}-{m:02d}.parquet"
        if path.exists():
            frames.append(read_bars_month(DATA_ROOT, symbol, timeframe, y, m))
        m += 1
        if m > 12:
            m, y = 1, y + 1
    if not frames:
        raise FileNotFoundError(f"no {symbol} {timeframe} bars in range {start}..{end}; pull with scripts/pull_bars.py --symbol {symbol}")
    return pd.concat(frames, ignore_index=True).sort_values("time").reset_index(drop=True)


def _parse_month(s: str) -> tuple[int, int]:
    parts = s.split("-")
    if len(parts) != 2:
        raise argparse.ArgumentTypeError(f"expected YYYY-MM, got {s!r}")
    return int(parts[0]), int(parts[1])


def _format_trade_ledger(trades: list) -> str:
    if not trades:
        return "  (no trades)"
    lines = [
        f"  {'#':<3} {'entry_time':<26} {'entry':>8} {'exit_time':<26} "
        f"{'exit':>8} {'reason':<6} {'lots':>6} {'gross':>10} {'cost':>8} {'net':>10}",
    ]
    for i, t in enumerate(trades, 1):
        lines.append(
            f"  {i:<3} {str(t.entry_time):<26} {t.entry_price:>8.2f} "
            f"{str(t.exit_time):<26} {t.exit_price:>8.2f} {t.exit_reason:<6} "
            f"{t.lots:>6.2f} {t.gross_pnl:>+10.2f} {t.cost:>8.2f} {t.net_pnl:>+10.2f}"
        )
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--symbol", default=DEFAULT_SYMBOL,
                    help=f"symbol to load + backtest (default: {DEFAULT_SYMBOL})")
    ap.add_argument("--start", type=_parse_month, default=(2026, 4),
                    help="start month YYYY-MM (inclusive)")
    ap.add_argument("--end", type=_parse_month, default=(2026, 5),
                    help="end month YYYY-MM (inclusive)")
    ap.add_argument("--balance", type=float, default=10_000.0)
    ap.add_argument("--risk", type=float, default=0.20,
                    help="fraction of account risked per trade (0..1)")
    ap.add_argument("--cost-rt", type=float, default=12.0,
                    help="round-trip cost per lot in USD (spread + commission)")
    ap.add_argument("--sl-distance", type=float, default=20.0,
                    help="stop-loss distance in price units (default XAUUSD 20)")
    ap.add_argument("--tp-distance", type=float, default=40.0,
                    help="take-profit distance in price units (default XAUUSD 40)")
    ap.add_argument("--be-trigger", type=float, default=15.0,
                    help="USD profit before SL is moved to breakeven")
    ap.add_argument("--no-ledger", action="store_true",
                    help="suppress per-trade ledger output")
    args = ap.parse_args()

    print(f"loading {args.symbol} H4 + H1 + M15 bars: {args.start} -> {args.end}")
    h4 = _load_months(args.symbol, "H4", args.start, args.end)
    h1 = _load_months(args.symbol, "H1", args.start, args.end)
    m15 = _load_months(args.symbol, "M15", args.start, args.end)
    print(f"  H4 bars  : {len(h4):>6,}  range {h4['time'].min()} -> {h4['time'].max()}")
    print(f"  H1 bars  : {len(h1):>6,}  range {h1['time'].min()} -> {h1['time'].max()}")
    print(f"  M15 bars : {len(m15):>6,}  range {m15['time'].min()} -> {m15['time'].max()}")

    print(f"\ndetecting SMC BOS-reversal signals (SL=${args.sl_distance:.2f}, TP=${args.tp_distance:.2f})...")
    signals = detect_bos_reversal_signals(
        h4, m15, h1_bars=h1,
        sl_distance=args.sl_distance, tp_distance=args.tp_distance,
    )
    print(f"  signals : {len(signals)}")
    for s in signals:
        rr_target = (s.tp_price - s.entry_price) / (s.entry_price - s.sl_price)
        print(
            f"    {s.entry_time}  {s.direction:<5} entry={s.entry_price:.2f} "
            f"SL={s.sl_price:.2f} TP={s.tp_price:.2f} R:R={rr_target:.2f}  "
            f"BOS={s.bos_level:.2f}@{s.bos_time}  capit={s.capitulation_time}"
        )

    print("\nrunning event-driven backtest...")
    rep = run_event_backtest(
        signals=signals, bars=m15,
        initial_balance=args.balance, risk_pct=args.risk,
        cost_per_roundtrip_usd_per_lot=args.cost_rt,
        be_trigger_distance=args.be_trigger,
    )

    print("\n" + rep.summary())

    if not args.no_ledger:
        print("\n=== Trade ledger ===")
        print(_format_trade_ledger(rep.trades))

    return 0


if __name__ == "__main__":
    sys.exit(main())

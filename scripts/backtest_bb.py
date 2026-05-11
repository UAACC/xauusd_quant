"""
scripts/backtest_bb.py — backtest classic Bollinger mean-reversion scalping
on XAUUSD M1 bars, compare gross vs cost-adjusted PnL across cost scenarios.

The goal is teaching, not strategy hunting: demonstrate empirically how the
broker cost stack (spread + commission + slippage) eats a textbook scalping
edge on a liquid CFD market.

Loads M1 bar parquets from ``data/bars/XAUUSD/M1/<YYYY>/*.parquet`` and runs
a small parameter grid for ``BBParams.period`` and ``BBParams.num_std``,
printing gross PnL / net PnL / Sharpe / drawdown for each.

Usage::

    python scripts/backtest_bb.py
    python scripts/backtest_bb.py --year 2026 --months 3 4 5
    python scripts/backtest_bb.py --lot 0.1     # scale costs to 0.1 lot
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from quant.backtest.engine import run_backtest  # noqa: E402
from quant.strategies.bb_mean_revert import BBParams, bb_position  # noqa: E402

DATA_ROOT = REPO_ROOT / "data"
SYMBOL = "XAUUSD"

# Cost scenarios to evaluate (USD per round-trip per lot).
# These reflect XAUUSD on IC Markets Raw Spread, as measured today.
COST_SCENARIOS: dict[str, float] = {
    "gross (no cost)         ": 0.0,
    "spread only (P50 = $9)  ": 9.0,
    "spread + commission     ": 16.0,   # spread $9 + commission $7
    "all-in incl. slippage   ": 21.0,   # +$5 measured slippage
}


def load_m1_bars(symbol: str, year: int, months: list[int]) -> pd.DataFrame:
    dfs = []
    for m in months:
        path = DATA_ROOT / "bars" / symbol / "M1" / str(year) / f"{year}-{m:02d}.parquet"
        if path.exists():
            dfs.append(pd.read_parquet(path))
    if not dfs:
        raise FileNotFoundError(f"No M1 parquets found at {DATA_ROOT / 'bars' / symbol / 'M1' / str(year)}")
    df = pd.concat(dfs, ignore_index=True)
    df = df.sort_values("time").drop_duplicates(subset=["time"]).reset_index(drop=True)
    return df


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--symbol", default=SYMBOL)
    ap.add_argument("--year", type=int, default=2026)
    ap.add_argument("--months", type=int, nargs="+", default=[3, 4, 5])
    ap.add_argument("--lot", type=float, default=0.01)
    ap.add_argument("--time-stop", type=int, default=30)
    ap.add_argument("--atr-stop-mult", type=float, default=1.5)
    return ap.parse_args()


def main() -> int:
    args = parse_args()

    bars = load_m1_bars(args.symbol, args.year, args.months)
    print(f"[OK] loaded {len(bars):,} M1 bars  range: {bars['time'].min()} -> {bars['time'].max()}")
    print(f"     lot size: {args.lot}    contract: 100 oz/lot   bars span: ~{(bars['time'].max()-bars['time'].min()).days} days")

    # Parameter grid — keep it small enough to read at a glance.
    periods = [10, 20, 30]
    num_stds = [1.5, 2.0, 2.5]

    print(f"\n{'='*82}")
    print(f"{'params':<26}{'gross':>10}{'spr only':>11}{'spr+comm':>11}{'all-in':>11}{'trades':>10}{'win%':>7}")
    print(f"{'-'*82}")

    rows = []
    for period in periods:
        for num_std in num_stds:
            params = BBParams(
                period=period, num_std=num_std,
                time_stop_bars=args.time_stop,
                atr_stop_mult=args.atr_stop_mult,
            )
            sig_df = bb_position(bars, params)

            # Run once per cost scenario; reuse the position series.
            results = {}
            for label, cost in COST_SCENARIOS.items():
                results[label] = run_backtest(
                    bars=bars, position=sig_df["position"],
                    lot=args.lot,
                    cost_per_roundtrip_usd_per_lot=cost,
                )

            r0 = next(iter(results.values()))
            print(
                f"{params.label():<26}"
                f"{results['gross (no cost)         '].net_pnl:>+10.2f}"
                f"{results['spread only (P50 = $9)  '].net_pnl:>+11.2f}"
                f"{results['spread + commission     '].net_pnl:>+11.2f}"
                f"{results['all-in incl. slippage   '].net_pnl:>+11.2f}"
                f"{r0.n_trades:>10}"
                f"{r0.win_rate*100:>6.1f}%"
            )
            rows.append({"period": period, "num_std": num_std,
                         "n_trades": r0.n_trades,
                         "win_rate": r0.win_rate,
                         **{k: v.net_pnl for k, v in results.items()}})

    print(f"{'='*82}\n")

    # Detailed summary for the headline param set (BB(20, 2.0))
    headline = BBParams(period=20, num_std=2.0,
                        time_stop_bars=args.time_stop,
                        atr_stop_mult=args.atr_stop_mult)
    sig_df = bb_position(bars, headline)
    print(f"\n=== Detailed report for {headline.label()} ===\n")
    for label, cost in COST_SCENARIOS.items():
        r = run_backtest(
            bars=bars, position=sig_df["position"],
            lot=args.lot,
            cost_per_roundtrip_usd_per_lot=cost,
        )
        print(r.summary(title=label.strip()))
        print()

    return 0


if __name__ == "__main__":
    sys.exit(main())

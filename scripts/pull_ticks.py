"""
scripts/pull_ticks.py — bulk historical XAUUSD tick pull from MT5.

Fetches ``copy_ticks_range`` day-by-day (UTC) and writes one parquet per day::

    data/ticks/<SYMBOL>/<YYYY>/<MM>/<YYYY-MM-DD>.parquet

Idempotent: skips days whose parquet already exists (use ``--force`` to replace).
For each day, prints row count, P50/P95/P99 spread, and gap stats.

Weekend / closed days yield empty parquet (so we don't re-pull).

Usage::

    python scripts/pull_ticks.py --days-back 30
    python scripts/pull_ticks.py --start 2026-04-01 --end 2026-05-08
    python scripts/pull_ticks.py --days-back 7 --force
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from mt5_connect import SYMBOL, init_mt5  # noqa: E402
import MetaTrader5 as mt5  # noqa: E402

from quant.config import get_demo_mt5_path  # noqa: E402
from quant.data.broker_time import BrokerClock, discover_clock  # noqa: E402
from quant.data.parquet import tick_path, write_ticks  # noqa: E402

DATA_ROOT = REPO_ROOT / "data"


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--days-back", type=int, help="Pull last N UTC days through today")
    g.add_argument("--start", type=str, help="Start date YYYY-MM-DD (inclusive)")
    ap.add_argument("--end", type=str, help="End date YYYY-MM-DD (inclusive); defaults to --start")
    ap.add_argument("--force", action="store_true", help="Overwrite existing parquet files")
    ap.add_argument("--symbol", default=SYMBOL)
    return ap.parse_args()


def date_range(start: date, end: date):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def fetch_day(symbol: str, day: date, clock: BrokerClock) -> pd.DataFrame:
    """Pull all ticks for a UTC calendar day, returning UTC time_msc.

    Converts query bounds UTC -> broker-naive (MT5 expects broker local),
    then converts returned time_msc broker -> UTC.
    """
    start_utc = datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc)
    end_utc = start_utc + timedelta(days=1)
    start_broker = clock.utc_dt_to_broker_naive(start_utc)
    end_broker = clock.utc_dt_to_broker_naive(end_utc)
    arr = mt5.copy_ticks_range(symbol, start_broker, end_broker, mt5.COPY_TICKS_ALL)
    if arr is None or len(arr) == 0:
        return pd.DataFrame(columns=["time_msc", "bid", "ask", "last", "volume_real", "flags"])
    df = pd.DataFrame(arr)[["time_msc", "bid", "ask", "last", "volume_real", "flags"]]
    df["time_msc"] = df["time_msc"].apply(clock.broker_msc_to_utc_msc).astype("int64")
    return df


def summarize(df: pd.DataFrame, point: float = 0.01) -> str:
    if df.empty:
        return "0 ticks (closed)"
    spread_pts = ((df["ask"] - df["bid"]) / point).round().astype(int)
    parts = [
        f"{len(df):,} ticks",
        f"spread P50/P95/P99 = "
        f"{spread_pts.quantile(0.5):.0f}/{spread_pts.quantile(0.95):.0f}/{spread_pts.quantile(0.99):.0f} pts",
    ]
    if len(df) > 1:
        gaps_s = df["time_msc"].diff().dropna() / 1000.0
        big = gaps_s[gaps_s > 60]
        if len(big):
            parts.append(f"{len(big)} gaps>60s (max {big.max():.0f}s)")
    return " | ".join(parts)


def main() -> int:
    args = parse_args()

    if args.days_back is not None:
        end_d = datetime.now(timezone.utc).date()
        start_d = end_d - timedelta(days=args.days_back)
    else:
        start_d = date.fromisoformat(args.start)
        end_d = date.fromisoformat(args.end) if args.end else start_d

    if not init_mt5(terminal_path=get_demo_mt5_path()):
        return 1

    try:
        if not mt5.symbol_select(args.symbol, True):
            print(f"[ERROR] symbol_select({args.symbol}) failed: {mt5.last_error()}")
            return 2

        acct = mt5.account_info()
        clock = discover_clock(mt5, acct.server, args.symbol)
        agree = "OK" if clock.agrees else "MISMATCH"
        print(f"[CLOCK] iana_tz={clock.iana_tz}  measured={clock.measured_offset_hours:+.1f}h  "
              f"expected={clock.expected_offset_seconds/3600:+.1f}h  source={clock.source}  "
              f"confidence={clock.confidence}  agreement={agree}")

        n_days = (end_d - start_d).days + 1
        print(f"[OK] pulling {args.symbol} ticks: {start_d} -> {end_d} ({n_days} day(s))")

        rows_total = 0
        for d in date_range(start_d, end_d):
            target = tick_path(DATA_ROOT, args.symbol, d)
            rel = target.relative_to(REPO_ROOT)
            if target.exists() and not args.force:
                print(f"  {d}  SKIP (exists: {rel})")
                continue
            df = fetch_day(args.symbol, d, clock)
            write_ticks(DATA_ROOT, args.symbol, d, df, overwrite=True, clock=clock)
            rows_total += len(df)
            print(f"  {d}  -> {rel}  | {summarize(df)}")
        print(f"\n[OK] total rows pulled this run: {rows_total:,}")
        return 0
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    sys.exit(main())

"""
scripts/pull_bars.py — bulk historical XAUUSD bar pull (multi-timeframe).

Pulls M1 / M5 / M15 / M30 / H1 / H4 / D1 bars from MT5 and writes one parquet
per (timeframe, month)::

    data/bars/<SYMBOL>/<TIMEFRAME>/<YYYY>/<YYYY-MM>.parquet

Always pulls whole months: a partial-month request is expanded to the full
month so each parquet contains a complete month of data. Idempotent: skips
(timeframe, month) pairs whose parquet exists unless ``--force``.

Usage::

    python scripts/pull_bars.py --months-back 6
    python scripts/pull_bars.py --start 2026-01 --end 2026-04
    python scripts/pull_bars.py --months-back 12 --force --timeframes M1,H1,D1
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from mt5_connect import SYMBOL, init_mt5  # noqa: E402
import MetaTrader5 as mt5  # noqa: E402

from quant.config import get_demo_mt5_path  # noqa: E402
from quant.data.broker_time import BrokerClock, discover_clock  # noqa: E402
from quant.data.parquet import bar_path, write_bars_month  # noqa: E402

DATA_ROOT = REPO_ROOT / "data"

# Map our timeframe names <-> MT5 constants. Ordered shortest-first so the
# stdout summary reads naturally.
TIMEFRAMES: dict[str, int] = {
    "M1": mt5.TIMEFRAME_M1,
    "M5": mt5.TIMEFRAME_M5,
    "M15": mt5.TIMEFRAME_M15,
    "M30": mt5.TIMEFRAME_M30,
    "H1": mt5.TIMEFRAME_H1,
    "H4": mt5.TIMEFRAME_H4,
    "D1": mt5.TIMEFRAME_D1,
}


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--months-back", type=int, help="Pull last N whole months through current month")
    g.add_argument("--start", type=str, help="Start month YYYY-MM (inclusive)")
    ap.add_argument("--end", type=str, help="End month YYYY-MM (inclusive); defaults to --start")
    ap.add_argument("--force", action="store_true", help="Overwrite existing parquet files")
    ap.add_argument(
        "--timeframes",
        default=",".join(TIMEFRAMES.keys()),
        help=f"Comma-separated subset of {','.join(TIMEFRAMES.keys())}",
    )
    ap.add_argument("--symbol", default=SYMBOL)
    return ap.parse_args()


def month_iter(y0: int, m0: int, y1: int, m1: int):
    """Yield (year, month) pairs from (y0,m0) to (y1,m1) inclusive."""
    y, m = y0, m0
    while (y, m) <= (y1, m1):
        yield (y, m)
        m += 1
        if m > 12:
            m, y = 1, y + 1


def month_bounds(year: int, month: int) -> tuple[datetime, datetime]:
    """UTC half-open interval [start, next_month_start) covering the given month."""
    start = datetime(year, month, 1, tzinfo=timezone.utc)
    if month == 12:
        end = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
    else:
        end = datetime(year, month + 1, 1, tzinfo=timezone.utc)
    return start, end


def fetch_bars(symbol: str, tf_const: int, start_utc: datetime, end_utc: datetime, clock: BrokerClock) -> pd.DataFrame:
    """Pull bars in UTC range [start_utc, end_utc). Returns ``time`` as true UTC.

    Converts query bounds UTC -> broker-naive (MT5 expects broker local) and
    returned ``time`` (broker-time-as-epoch) -> true UTC datetimes.
    """
    start_broker = clock.utc_dt_to_broker_naive(start_utc)
    end_broker = clock.utc_dt_to_broker_naive(end_utc)
    rates = mt5.copy_rates_range(symbol, tf_const, start_broker, end_broker)
    if rates is None or len(rates) == 0:
        return pd.DataFrame()
    df = pd.DataFrame(rates)
    # Convert broker-time-as-epoch seconds -> true UTC ms -> datetime UTC
    df["time"] = df["time"].apply(
        lambda s: pd.Timestamp(clock.broker_msc_to_utc_msc(int(s) * 1000), unit="ms", tz="UTC")
    )
    return df


def parse_month(s: str) -> tuple[int, int]:
    parts = s.split("-")
    if len(parts) != 2:
        raise ValueError(f"Expected YYYY-MM, got {s!r}")
    return int(parts[0]), int(parts[1])


def main() -> int:
    args = parse_args()

    requested_tfs = [tf.strip() for tf in args.timeframes.split(",") if tf.strip()]
    unknown = [tf for tf in requested_tfs if tf not in TIMEFRAMES]
    if unknown:
        print(f"[ERROR] unknown timeframes: {unknown}. Valid: {list(TIMEFRAMES.keys())}")
        return 2

    today = datetime.now(timezone.utc)
    if args.months_back is not None:
        # Last N whole months through current. months_back=1 = current month only.
        end_y, end_m = today.year, today.month
        start_total = end_y * 12 + (end_m - 1) - (args.months_back - 1)
        start_y, start_m = divmod(start_total, 12)
        start_m += 1
    else:
        start_y, start_m = parse_month(args.start)
        end_y, end_m = parse_month(args.end) if args.end else (start_y, start_m)

    months = list(month_iter(start_y, start_m, end_y, end_m))

    if not init_mt5(terminal_path=get_demo_mt5_path()):
        return 1

    try:
        if not mt5.symbol_select(args.symbol, True):
            print(f"[ERROR] symbol_select({args.symbol}) failed: {mt5.last_error()}")
            return 3

        acct = mt5.account_info()
        clock = discover_clock(mt5, acct.server, args.symbol)
        agree = "OK" if clock.agrees else "MISMATCH"
        print(f"[CLOCK] iana_tz={clock.iana_tz}  measured={clock.measured_offset_hours:+.1f}h  "
              f"expected={clock.expected_offset_seconds/3600:+.1f}h  source={clock.source}  "
              f"confidence={clock.confidence}  agreement={agree}")

        print(f"[OK] pulling {args.symbol} bars: "
              f"{start_y:04d}-{start_m:02d} -> {end_y:04d}-{end_m:02d} "
              f"({len(months)} month(s)) x {len(requested_tfs)} timeframe(s)")

        rows_total = 0
        for tf_name in requested_tfs:
            tf_const = TIMEFRAMES[tf_name]
            for (y, m) in months:
                target = bar_path(DATA_ROOT, args.symbol, tf_name, y, m)
                rel = target.relative_to(REPO_ROOT)
                if target.exists() and not args.force:
                    print(f"  {tf_name:3s} {y}-{m:02d}  SKIP (exists: {rel})")
                    continue
                start_dt, end_dt = month_bounds(y, m)
                df = fetch_bars(args.symbol, tf_const, start_dt, end_dt, clock)
                write_bars_month(DATA_ROOT, args.symbol, tf_name, y, m, df, overwrite=True, clock=clock)
                rows_total += len(df)
                print(f"  {tf_name:3s} {y}-{m:02d}  -> {rel}  | {len(df):,} bars")

        print(f"\n[OK] total bars pulled this run: {rows_total:,}")
        return 0
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    sys.exit(main())

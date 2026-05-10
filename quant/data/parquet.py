"""
Partitioned parquet I/O for tick and bar data.

Layout::

    data/ticks/<SYMBOL>/<YYYY>/<MM>/<YYYY>-<MM>-<DD>.parquet  # daily
    data/bars/<SYMBOL>/<TIMEFRAME>/<YYYY>/<YYYY>-<MM>.parquet  # monthly

Tick schema (matches MT5 ``copy_ticks_range`` output; we drop the redundant
second-precision ``time`` column and keep ``time_msc`` only)::

    time_msc      int64    UTC epoch milliseconds
    bid           float64
    ask           float64
    last          float64
    volume_real   float64
    flags         uint32   MT5 TICK_FLAG_* bitmask

Bar schema (matches MT5 ``copy_rates_range`` output)::

    time          timestamp[s, UTC]   bar open time
    open / high / low / close   float64
    tick_volume   int64    number of ticks in the bar
    spread        int32    median spread during bar (in points)
    real_volume   int64    real exchange volume (CFD: usually 0)
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

if TYPE_CHECKING:
    from quant.data.broker_time import BrokerClock


def _convention_metadata(clock: "BrokerClock | None") -> dict[bytes, bytes]:
    """Schema-level metadata documenting how time fields were stored.

    Convention: when ``clock`` is supplied, the caller has already converted
    time fields to true UTC milliseconds; we record the broker tz for audit.
    Without a clock, we mark the file as legacy (broker time mislabeled UTC),
    so a future read can decide whether to convert.
    """
    if clock is None:
        return {b"time_convention": b"broker_time_as_utc_legacy"}
    return {
        b"time_convention": b"utc",
        b"broker_tz": clock.iana_tz.encode("utf-8"),
        b"measured_at_utc": clock.measured_at_utc.encode("utf-8"),
        b"measured_offset_seconds": str(clock.measured_offset_seconds).encode("ascii"),
    }


TICK_SCHEMA = pa.schema([
    pa.field("time_msc", pa.int64(), nullable=False),
    pa.field("bid", pa.float64(), nullable=True),
    pa.field("ask", pa.float64(), nullable=True),
    pa.field("last", pa.float64(), nullable=True),
    pa.field("volume_real", pa.float64(), nullable=True),
    pa.field("flags", pa.uint32(), nullable=False),
])

_TICK_COLS = ["time_msc", "bid", "ask", "last", "volume_real", "flags"]


def tick_path(data_root: Path, symbol: str, day: date) -> Path:
    """Canonical parquet path for one symbol-day."""
    return (
        data_root / "ticks" / symbol /
        f"{day.year:04d}" / f"{day.month:02d}" /
        f"{day.isoformat()}.parquet"
    )


def write_ticks(
    data_root: Path,
    symbol: str,
    day: date,
    ticks: pd.DataFrame,
    overwrite: bool = False,
    clock: "BrokerClock | None" = None,
) -> Path:
    """Write a day's ticks. Creates parent dirs. Empty df allowed.

    Time semantics: the caller is expected to pre-convert ``time_msc`` to
    true UTC milliseconds when ``clock`` is supplied (this function does
    not convert). The clock is recorded in schema metadata for audit.
    Without a clock, the file is marked legacy (broker time as UTC).
    """
    path = tick_path(data_root, symbol, day)
    if path.exists() and not overwrite:
        raise FileExistsError(f"{path} exists; pass overwrite=True to replace")
    path.parent.mkdir(parents=True, exist_ok=True)

    if ticks.empty:
        df = pd.DataFrame({c: pd.Series(dtype=t) for c, t in [
            ("time_msc", "int64"),
            ("bid", "float64"),
            ("ask", "float64"),
            ("last", "float64"),
            ("volume_real", "float64"),
            ("flags", "uint32"),
        ]})
    else:
        df = ticks.copy()
        df["time_msc"] = df["time_msc"].astype("int64")
        for col in ("bid", "ask", "last", "volume_real"):
            df[col] = df[col].astype("float64")
        df["flags"] = df["flags"].astype("uint32")
        df = df[_TICK_COLS]

    schema = TICK_SCHEMA.with_metadata(_convention_metadata(clock))
    table = pa.Table.from_pandas(df, schema=schema, preserve_index=False)
    pq.write_table(table, path, compression="snappy")
    return path


def read_ticks(data_root: Path, symbol: str, day: date) -> pd.DataFrame:
    return pd.read_parquet(tick_path(data_root, symbol, day))


# ---------------------------------------------------------------------------
# Bars
# ---------------------------------------------------------------------------

BAR_SCHEMA = pa.schema([
    pa.field("time", pa.timestamp("s", tz="UTC"), nullable=False),
    pa.field("open", pa.float64(), nullable=False),
    pa.field("high", pa.float64(), nullable=False),
    pa.field("low", pa.float64(), nullable=False),
    pa.field("close", pa.float64(), nullable=False),
    pa.field("tick_volume", pa.int64(), nullable=False),
    pa.field("spread", pa.int32(), nullable=False),
    pa.field("real_volume", pa.int64(), nullable=False),
])

_BAR_COLS = ["time", "open", "high", "low", "close", "tick_volume", "spread", "real_volume"]


def bar_path(data_root: Path, symbol: str, timeframe: str, year: int, month: int) -> Path:
    """Canonical parquet path for one symbol-timeframe-month."""
    return (
        data_root / "bars" / symbol / timeframe /
        f"{year:04d}" / f"{year:04d}-{month:02d}.parquet"
    )


def write_bars_month(
    data_root: Path,
    symbol: str,
    timeframe: str,
    year: int,
    month: int,
    bars: pd.DataFrame,
    overwrite: bool = False,
    clock: "BrokerClock | None" = None,
) -> Path:
    """Write a (symbol, timeframe, month)'s bars to parquet.

    Time semantics: caller must pre-convert ``time`` to true UTC when
    ``clock`` is supplied (we record the metadata only; we do not convert).
    """
    path = bar_path(data_root, symbol, timeframe, year, month)
    if path.exists() and not overwrite:
        raise FileExistsError(f"{path} exists; pass overwrite=True to replace")
    path.parent.mkdir(parents=True, exist_ok=True)

    if bars.empty:
        df = pd.DataFrame({c: pd.Series(dtype=t) for c, t in [
            ("time", "datetime64[s, UTC]"),
            ("open", "float64"),
            ("high", "float64"),
            ("low", "float64"),
            ("close", "float64"),
            ("tick_volume", "int64"),
            ("spread", "int32"),
            ("real_volume", "int64"),
        ]})
    else:
        df = bars.copy()
        if df["time"].dtype == "int64" or df["time"].dtype == "uint64":
            df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
        # Truncate to second precision to match schema
        df["time"] = df["time"].dt.tz_convert("UTC").dt.floor("s")
        df["open"] = df["open"].astype("float64")
        df["high"] = df["high"].astype("float64")
        df["low"] = df["low"].astype("float64")
        df["close"] = df["close"].astype("float64")
        df["tick_volume"] = df["tick_volume"].astype("int64")
        df["spread"] = df["spread"].astype("int32")
        df["real_volume"] = df["real_volume"].astype("int64")
        df = df[_BAR_COLS].sort_values("time")

    schema = BAR_SCHEMA.with_metadata(_convention_metadata(clock))
    table = pa.Table.from_pandas(df, schema=schema, preserve_index=False)
    pq.write_table(table, path, compression="snappy")
    return path


def read_bars_month(
    data_root: Path, symbol: str, timeframe: str, year: int, month: int,
) -> pd.DataFrame:
    return pd.read_parquet(bar_path(data_root, symbol, timeframe, year, month))

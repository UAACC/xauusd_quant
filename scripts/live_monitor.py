"""
scripts/live_monitor.py — real-time XAUUSD tick monitor + alert engine.

Polls MT5 for new ticks at ~10 Hz, computes rolling stats, prints a
summary line every second, fires alerts on configurable thresholds, and
appends ticks to today's daily parquet (resumable across restarts).

NOT a strategy or order engine — read-only monitoring. Latency-sensitive
execution lives in MQL5 EA per the architectural decision.

Architecture (single thread):

    poll loop @ 10 Hz:
        copy_ticks_from(last_seen_msc) -> append to buffer + rolling deque

    every 1s:
        compute rolling metrics from deque (spread P50/P95, tick rate, RV)
        print summary line
        evaluate alerts -> stdout + data/alerts.jsonl

    every 5s:
        flush buffer -> rewrite today's daily parquet (read existing + dedupe)

    on Ctrl+C / SIGTERM / --duration expiry:
        final flush + clean shutdown

Persistence: appends to ``data/ticks/<symbol>/<YYYY>/<MM>/<YYYY-MM-DD>.parquet``,
the same path ``pull_ticks.py`` writes. Idempotent: deduped by ``time_msc``.

Usage::

    python scripts/live_monitor.py
    python scripts/live_monitor.py --duration 600          # auto-stop 10 min
    python scripts/live_monitor.py --no-persist            # don't write parquet
    python scripts/live_monitor.py --alert-spread-pts 30   # custom threshold
"""

from __future__ import annotations

import argparse
import json
import signal
import sys
import time
from collections import deque
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from mt5_connect import SYMBOL, init_mt5  # noqa: E402
import MetaTrader5 as mt5  # noqa: E402
import pyarrow.parquet as pq  # noqa: E402

from quant.config import get_demo_mt5_path  # noqa: E402
from quant.data.broker_time import BrokerClock, discover_clock  # noqa: E402
from quant.data.parquet import tick_path, write_ticks  # noqa: E402

DATA_ROOT = REPO_ROOT / "data"
ALERT_LOG = DATA_ROOT / "alerts.jsonl"

POINT = 0.01            # XAUUSD point size
POLL_INTERVAL_S = 0.1   # 10 Hz
SUMMARY_EVERY_S = 1.0
FLUSH_EVERY_S = 5.0
RING_MAX_TICKS = 200_000  # ~5 min at peak XAUUSD tick rate

DEFAULT_ALERT_SPREAD_PTS = 30
DEFAULT_ALERT_RATE_DROP_FACTOR = 0.20  # rate < 20% of trailing baseline
DEFAULT_ALERT_STALE_S = 5.0            # no tick for >5s (during liquid hours)
DEFAULT_ALERT_COOLDOWN_S = 30.0        # don't re-fire same-type alert within N seconds


# ---------------------------------------------------------------------------
# Buffer
# ---------------------------------------------------------------------------
class TickBuffer:
    """Rolling deque for stats + pending list for parquet flush."""

    def __init__(self, max_ticks: int = RING_MAX_TICKS) -> None:
        self.ring: deque[dict] = deque(maxlen=max_ticks)
        self.pending: list[dict] = []

    def push(self, df: pd.DataFrame) -> None:
        if df.empty:
            return
        records = df.to_dict("records")
        for r in records:
            self.ring.append(r)
            self.pending.append(r)

    def take_pending(self) -> pd.DataFrame:
        if not self.pending:
            return pd.DataFrame(columns=["time_msc", "bid", "ask", "last", "volume_real", "flags"])
        df = pd.DataFrame(self.pending)
        self.pending = []
        return df

    def latest(self) -> dict | None:
        return self.ring[-1] if self.ring else None

    def window_df(self, last_n_seconds: float | None = None) -> pd.DataFrame:
        if not self.ring:
            return pd.DataFrame()
        df = pd.DataFrame(list(self.ring))
        if last_n_seconds is not None and not df.empty:
            cutoff = df["time_msc"].max() - int(last_n_seconds * 1000)
            df = df[df["time_msc"] >= cutoff]
        return df


# ---------------------------------------------------------------------------
# Metrics + alerts
# ---------------------------------------------------------------------------
def compute_metrics(buf: TickBuffer) -> dict[str, float | None]:
    df60 = buf.window_df(60)
    if df60.empty:
        return {"spread_p50_60s": None, "spread_p95_60s": None,
                "tick_rate_per_min": None, "rv_5m_bp": None}

    spread_pts = (df60["ask"] - df60["bid"]) / POINT
    p50 = float(spread_pts.quantile(0.5))
    p95 = float(spread_pts.quantile(0.95))

    span_s = max((df60["time_msc"].max() - df60["time_msc"].min()) / 1000.0, 1.0)
    rate_per_min = float(len(df60) / span_s * 60.0)

    df5m = buf.window_df(300)
    rv_5m_bp: float | None = None
    if len(df5m) > 100:
        mid = (df5m["bid"].values + df5m["ask"].values) / 2.0
        log_ret = np.diff(np.log(mid))
        if log_ret.size:
            rv_5m_bp = float(np.sqrt((log_ret ** 2).sum()) * 1e4)

    return {
        "spread_p50_60s": p50,
        "spread_p95_60s": p95,
        "tick_rate_per_min": rate_per_min,
        "rv_5m_bp": rv_5m_bp,
    }


def evaluate_alerts(
    buf: TickBuffer,
    metrics: dict[str, float | None],
    baseline: dict[str, Any],
    args: argparse.Namespace,
    last_alert_t: dict[str, float],
    now_wall: float,
) -> list[dict[str, Any]]:
    """Return list of alerts that should fire NOW (post-cooldown)."""
    fired: list[dict[str, Any]] = []
    latest = buf.latest()
    if not latest:
        return fired

    def maybe_fire(kind: str, payload: dict[str, Any]) -> None:
        last_t = last_alert_t.get(kind, 0.0)
        if now_wall - last_t < args.alert_cooldown_s:
            return
        last_alert_t[kind] = now_wall
        fired.append({"type": kind, **payload})

    # Spread blowout
    spread_pts = (latest["ask"] - latest["bid"]) / POINT
    if spread_pts >= args.alert_spread_pts:
        maybe_fire("spread_blowout", {
            "value_pts": float(spread_pts),
            "baseline_p50_60s": metrics.get("spread_p50_60s"),
        })

    # Rate collapse vs trailing baseline
    rate = metrics.get("tick_rate_per_min")
    base_rate = baseline.get("rate_p50_recent")
    if (rate is not None and base_rate is not None
            and base_rate > 30  # avoid div-by-noise during warmup
            and rate < base_rate * args.alert_rate_drop_factor):
        maybe_fire("rate_collapse", {
            "current_per_min": rate,
            "baseline_per_min": base_rate,
            "drop_factor_threshold": args.alert_rate_drop_factor,
        })

    # Stale ticks (no new ticks for >threshold seconds)
    age_s = (now_wall - latest["time_msc"] / 1000.0)
    if age_s > args.alert_stale_s:
        maybe_fire("stale_ticks", {"age_s": float(age_s)})

    return fired


def log_alert(alert: dict[str, Any]) -> None:
    ALERT_LOG.parent.mkdir(parents=True, exist_ok=True)
    record = {"ts_utc": datetime.now(timezone.utc).isoformat(), **alert}
    with open(ALERT_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"[ALERT {ts}] {alert['type']}: "
          + ", ".join(f"{k}={v}" for k, v in alert.items() if k != "type"),
          flush=True)


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------
def _safe_fmt(v: float | None, suffix: str = "") -> str:
    return f"{v:.0f}{suffix}" if v is not None else "-"


def print_summary(buf: TickBuffer, metrics: dict[str, float | None]) -> None:
    """Summary line; safe against None metrics during warmup."""
    latest = buf.latest()
    if not latest:
        print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] warming up - no ticks yet", flush=True)
        return
    ts = datetime.fromtimestamp(latest["time_msc"] / 1000.0, tz=timezone.utc).strftime("%H:%M:%S")
    spread_pts = (latest["ask"] - latest["bid"]) / POINT
    print(
        f"[{ts}] ask={latest['ask']:.2f} bid={latest['bid']:.2f} "
        f"spr={spread_pts:.0f}pt | "
        f"60s P50={_safe_fmt(metrics.get('spread_p50_60s'))} "
        f"P95={_safe_fmt(metrics.get('spread_p95_60s'))} | "
        f"rate={_safe_fmt(metrics.get('tick_rate_per_min'))}/min | "
        f"rv5m={_safe_fmt(metrics.get('rv_5m_bp'), 'bp')}",
        flush=True,
    )


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------
def flush_to_parquet(symbol: str, day: date, buf: TickBuffer, persist: bool, clock: BrokerClock) -> int:
    """Drain pending into today's daily parquet (canonical UTC time_msc).

    If existing file is legacy (broker time mislabeled UTC), it is converted
    inline so the rewrite ends up cleanly UTC-canonical.
    """
    pending = buf.take_pending()
    if not persist or pending.empty:
        return 0
    target = tick_path(DATA_ROOT, symbol, day)
    if target.exists():
        existing = _read_existing_ticks_as_utc(target, clock)
        combined = pd.concat([existing, pending], ignore_index=True)
        combined = combined.drop_duplicates(subset=["time_msc"], keep="last").sort_values("time_msc")
    else:
        combined = pending.sort_values("time_msc")
    write_ticks(DATA_ROOT, symbol, day, combined, overwrite=True, clock=clock)
    return len(pending)


# ---------------------------------------------------------------------------
# CLI / main
# ---------------------------------------------------------------------------
def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--symbol", default=SYMBOL)
    ap.add_argument("--duration", type=int, default=None,
                    help="Auto-stop after N seconds (default: run forever)")
    ap.add_argument("--no-persist", action="store_true",
                    help="Don't append ticks to parquet (in-memory only)")
    ap.add_argument("--alert-spread-pts", type=float, default=DEFAULT_ALERT_SPREAD_PTS)
    ap.add_argument("--alert-rate-drop-factor", type=float, default=DEFAULT_ALERT_RATE_DROP_FACTOR)
    ap.add_argument("--alert-stale-s", type=float, default=DEFAULT_ALERT_STALE_S)
    ap.add_argument("--alert-cooldown-s", type=float, default=DEFAULT_ALERT_COOLDOWN_S,
                    help="Minimum seconds between same-type alerts")
    return ap.parse_args()


def fetch_new_ticks(symbol: str, last_utc_msc: int | None, clock: BrokerClock) -> pd.DataFrame:
    """Pull all ticks strictly newer than ``last_utc_msc`` (true UTC ms).

    Converts query bound UTC -> broker-naive datetime for the MT5 API,
    then converts returned ``time_msc`` from broker-time-as-epoch back to
    true UTC milliseconds. After this function, time_msc is canonical UTC
    and downstream code never sees broker time.
    """
    if last_utc_msc is None:
        from_utc = datetime.now(timezone.utc) - timedelta(seconds=60)
    else:
        # +1 ms ensures strict-greater-than semantics
        from_utc = datetime.fromtimestamp((last_utc_msc + 1) / 1000.0, tz=timezone.utc)
    from_broker_naive = clock.utc_dt_to_broker_naive(from_utc)
    arr = mt5.copy_ticks_from(symbol, from_broker_naive, 100_000, mt5.COPY_TICKS_ALL)
    if arr is None or len(arr) == 0:
        return pd.DataFrame(columns=["time_msc", "bid", "ask", "last", "volume_real", "flags"])
    df = pd.DataFrame(arr)[["time_msc", "bid", "ask", "last", "volume_real", "flags"]]
    # Convert broker time_msc -> UTC time_msc (vectorized via apply; ~1us per row, fine for batch sizes)
    df["time_msc"] = df["time_msc"].apply(clock.broker_msc_to_utc_msc).astype("int64")
    if last_utc_msc is not None:
        df = df[df["time_msc"] > last_utc_msc]
    return df


def _read_existing_ticks_as_utc(path: Path, clock: BrokerClock) -> pd.DataFrame:
    """Read an existing parquet, converting legacy broker-time files to UTC if needed.

    Uses parquet schema metadata to detect convention written by ``write_ticks``.
    Files written before the clock-aware refactor have no metadata or carry
    ``broker_time_as_utc_legacy`` and need conversion.
    """
    pf = pq.ParquetFile(path)
    md = pf.schema_arrow.metadata or {}
    convention = md.get(b"time_convention", b"unknown")
    df = pf.read().to_pandas()
    if df.empty:
        return df
    if convention == b"utc":
        return df
    # Legacy or unknown: assume broker-time-as-utc, convert
    df["time_msc"] = df["time_msc"].apply(clock.broker_msc_to_utc_msc).astype("int64")
    return df


def resume_last_utc_msc(symbol: str, day: date, clock: BrokerClock) -> int | None:
    """If today's parquet exists and has data, return its max time_msc as true UTC ms."""
    target = tick_path(DATA_ROOT, symbol, day)
    if not target.exists():
        return None
    df = _read_existing_ticks_as_utc(target, clock)
    if df.empty:
        return None
    return int(df["time_msc"].max())


def main() -> int:
    args = parse_args()
    persist = not args.no_persist

    if not init_mt5(terminal_path=get_demo_mt5_path()):
        return 1

    try:
        if not mt5.symbol_select(args.symbol, True):
            print(f"[ERROR] symbol_select({args.symbol}): {mt5.last_error()}")
            return 2

        # Discover broker timezone via runtime measurement + IANA assumption
        acct = mt5.account_info()
        clock = discover_clock(mt5, acct.server, args.symbol)
        agree = "OK" if clock.agrees else "MISMATCH"
        print(f"[CLOCK] iana_tz={clock.iana_tz}  measured={clock.measured_offset_hours:+.1f}h  "
              f"expected={clock.expected_offset_seconds/3600:+.1f}h  source={clock.source}  "
              f"confidence={clock.confidence}  agreement={agree}", flush=True)
        if not clock.agrees and clock.confidence != "low":
            print(f"[WARN] measured broker offset differs from IANA assumption — investigate before trusting time analysis", flush=True)

        buf = TickBuffer()
        baseline: dict[str, Any] = {}
        last_alert_t: dict[str, float] = {}

        today = datetime.now(timezone.utc).date()
        last_utc_msc = resume_last_utc_msc(args.symbol, today, clock) if persist else None
        if last_utc_msc is not None:
            ts = datetime.fromtimestamp(last_utc_msc / 1000.0, tz=timezone.utc)
            print(f"[OK] resuming from existing parquet: last tick @ {ts.isoformat()} UTC (msc={last_utc_msc})", flush=True)

        # Seed buffer with the last 60s so rolling stats are usable immediately
        seed_df = fetch_new_ticks(args.symbol, last_utc_msc, clock)
        if not seed_df.empty:
            buf.push(seed_df)
            last_utc_msc = int(seed_df["time_msc"].max())
            print(f"[OK] seeded buffer with {len(seed_df)} ticks", flush=True)
        else:
            print(f"[INFO] seed pull returned 0 ticks (likely market closed or stale)", flush=True)

        running = {"go": True}
        def _stop(*_: Any) -> None:
            running["go"] = False
        signal.signal(signal.SIGINT, _stop)
        try:
            signal.signal(signal.SIGTERM, _stop)
        except (AttributeError, ValueError):
            pass  # Windows / restricted env

        print(f"[OK] live_monitor running. Ctrl+C to stop. "
              f"persist={persist} symbol={args.symbol} duration={args.duration}", flush=True)

        start_t = time.monotonic()
        last_summary_t = 0.0
        last_flush_t = start_t

        while running["go"]:
            now_t = time.monotonic()
            if args.duration is not None and (now_t - start_t) >= args.duration:
                print(f"[OK] duration {args.duration}s reached, stopping.", flush=True)
                break

            new_df = fetch_new_ticks(args.symbol, last_utc_msc, clock)
            if not new_df.empty:
                buf.push(new_df)
                last_utc_msc = int(new_df["time_msc"].max())

            if now_t - last_summary_t >= SUMMARY_EVERY_S:
                metrics = compute_metrics(buf)
                print_summary(buf, metrics)
                rate = metrics.get("tick_rate_per_min")
                if rate is not None:
                    hist = baseline.setdefault("rate_history", deque(maxlen=300))
                    hist.append(rate)
                    if len(hist) >= 30:
                        baseline["rate_p50_recent"] = float(np.median(hist))
                for a in evaluate_alerts(buf, metrics, baseline, args, last_alert_t, time.time()):
                    log_alert(a)
                last_summary_t = now_t

            if now_t - last_flush_t >= FLUSH_EVERY_S:
                n = flush_to_parquet(args.symbol, today, buf, persist, clock)
                if n:
                    print(f"[FLUSH] +{n} rows -> daily parquet", flush=True)
                last_flush_t = now_t

            time.sleep(POLL_INTERVAL_S)

        print("[OK] final flush...", flush=True)
        n = flush_to_parquet(args.symbol, today, buf, persist, clock)
        print(f"[OK] live_monitor stopped cleanly. final flush: +{n} rows", flush=True)
        return 0

    finally:
        mt5.shutdown()


if __name__ == "__main__":
    sys.exit(main())

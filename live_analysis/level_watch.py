"""Price-level cross alerter — fires when a symbol's mid price crosses a level.

Poll MT5 for a symbol's tick at a configurable rate. When mid price crosses
the target level in the specified direction, write an alert to
``data/alerts.jsonl``, print to stdout, play a Windows system beep, and
keep watching (cooldown-throttled so a chop near the level doesn't spam).

NOT a strategy or execution tool -- read-only watching, just an alert
trigger. Designed to be run alongside ``scripts/live_monitor.py``:

  Window 1: python scripts/live_monitor.py --symbol XAGUSD
  Window 2: python -m live_analysis.level_watch --symbol XAGUSD --level 86.40 --side below

When the alert fires in Window 2, the user pings the analyst (me) with
"alert fired" and the analyst does a full snapshot + entry verdict.

Reuses ``scripts/mt5_connect.init_mt5`` so the demo-only ``trade_mode == 2``
abort guard applies here too.
"""
from __future__ import annotations

import argparse
import json
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

ALERT_LOG = REPO_ROOT / "data" / "alerts.jsonl"


def _try_beep(freq: int = 880, dur_ms: int = 200, count: int = 3) -> None:
    """Play short beeps via winsound; silently no-op on non-Windows."""
    try:
        import winsound  # noqa: PLC0415
        for _ in range(count):
            winsound.Beep(freq, dur_ms)
    except Exception:  # noqa: BLE001  -- audible alerts are best-effort
        pass


def main(argv: Optional[list[str]] = None) -> int:
    from mt5_connect import init_mt5, init_mt5_live  # noqa: E402
    import MetaTrader5 as mt5  # noqa: E402

    from quant.config import get_demo_mt5_path, get_live_mt5_path  # noqa: E402

    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--symbol", required=True)
    p.add_argument("--level", type=float, required=True,
                   help="price level to watch")
    p.add_argument("--side", required=True, choices=("above", "below"),
                   help="alert direction: 'below' fires when price drops through level")
    p.add_argument("--interval", type=float, default=1.0,
                   help="poll interval in seconds (default 1.0 -- 1Hz is plenty for a level cross)")
    p.add_argument("--cooldown", type=float, default=60.0,
                   help="min seconds between repeat alerts (default 60)")
    p.add_argument("--status-every", type=float, default=30.0,
                   help="print 'still watching' status every N seconds (default 30)")
    p.add_argument("--no-beep", dest="beep", action="store_false",
                   help="suppress Windows system beep on alert")
    p.add_argument("--live", action="store_true",
                   help="connect to LIVE MT5 (default: demo). Read-only either way.")
    p.set_defaults(beep=True)
    args = p.parse_args(argv)

    if args.live:
        connected = init_mt5_live(terminal_path=get_live_mt5_path(), allow_orders=False)
        if not connected:
            print("[ERROR] init_mt5_live failed; ensure live terminal logged in")
            return 1
    else:
        if not init_mt5(terminal_path=get_demo_mt5_path()):
            print("[ERROR] MT5 init failed; ensure demo terminal is running + logged in")
            return 1

    if not mt5.symbol_select(args.symbol, True):
        print(f"[ERROR] symbol_select({args.symbol}) failed: {mt5.last_error()}")
        mt5.shutdown()
        return 2

    info = mt5.symbol_info(args.symbol)
    if info is None:
        print(f"[ERROR] symbol_info({args.symbol}) returned None")
        mt5.shutdown()
        return 2

    ALERT_LOG.parent.mkdir(parents=True, exist_ok=True)

    print(f"[WATCH] {args.symbol} alert when mid {args.side} {args.level} "
          f"(point={info.point}, poll={args.interval}s, cooldown={args.cooldown}s)")
    print(f"[WATCH] alerts -> {ALERT_LOG}")
    print(f"[WATCH] Ctrl+C to stop")

    stop = {"flag": False}

    def handle_sig(signum: int, frame) -> None:  # noqa: ARG001
        stop["flag"] = True
    signal.signal(signal.SIGINT, handle_sig)

    last_alert_t = 0.0
    last_state: Optional[str] = None  # "above" / "below" / "at"
    last_status_t = time.time()

    while not stop["flag"]:
        tick = mt5.symbol_info_tick(args.symbol)
        if tick is None:
            time.sleep(args.interval)
            continue
        mid = (tick.bid + tick.ask) / 2
        now_s = time.time()

        if mid > args.level:
            cur_state = "above"
        elif mid < args.level:
            cur_state = "below"
        else:
            cur_state = "at"

        # Cross detection: previous state was on the opposite side, now we're
        # at or on the alert side. First read where we already start on the
        # alert side also fires (so a script started post-cross still alerts).
        crossed_now = False
        if args.side == "below":
            if last_state in ("above",) and cur_state in ("below", "at"):
                crossed_now = True
            elif last_state is None and cur_state in ("below", "at"):
                crossed_now = True
        else:  # above
            if last_state in ("below",) and cur_state in ("above", "at"):
                crossed_now = True
            elif last_state is None and cur_state in ("above", "at"):
                crossed_now = True

        if crossed_now and (now_s - last_alert_t) >= args.cooldown:
            spread_pts = (tick.ask - tick.bid) / info.point
            record = {
                "ts_utc": datetime.now(timezone.utc).isoformat(),
                "type": "level_cross",
                "symbol": args.symbol,
                "side": args.side,
                "level": args.level,
                "price_bid": float(tick.bid),
                "price_ask": float(tick.ask),
                "price_mid": float(mid),
                "spread_pts": float(spread_pts),
            }
            with ALERT_LOG.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record) + "\n")
            print(f"[ALERT {record['ts_utc']}] {args.symbol} crossed {args.side} {args.level}: "
                  f"mid={mid:.4f} bid={tick.bid:.4f} ask={tick.ask:.4f} spread={spread_pts:.0f}pt")
            if args.beep:
                _try_beep()
            last_alert_t = now_s

        last_state = cur_state

        if now_s - last_status_t >= args.status_every:
            dist_pts = abs(mid - args.level) / info.point
            print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] "
                  f"{args.symbol} mid={mid:.4f} target={args.level} "
                  f"({cur_state} by {dist_pts:.0f}pt)")
            last_status_t = now_s

        time.sleep(args.interval)

    mt5.shutdown()
    print("[WATCH] stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())

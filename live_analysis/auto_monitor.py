"""Periodic live-market + position state checker.

Designed to be invoked by a Claude Code cron job every 1 minute. Output is a
JSON envelope on stdout that the cron-driver Claude reads to decide whether
to send a PushNotification to the user.

Hard rules:
- READ-ONLY (uses init_mt5_live with allow_orders=False). No order_send.
- Stateful via live_analysis/data/monitor_state.json (tracks last-seen state
  to detect events like position close, threshold crosses, P&L deltas).
- Anti-spam cooldown: same event type won't push within COOLDOWN_S seconds.

Triggers (when push_needed=true):
- Position closed (SL or TP hit, or manual close)
- Price crosses configured thresholds (above/below)
- Combined P&L worsens by > $100 CAD since last check
- Spread anomaly (>30pt for XAUUSD, >50pt for XAGUSD)

Otherwise: silent (push_needed=false, exit 0).
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

STATE_FILE = REPO_ROOT / "live_analysis" / "data" / "monitor_state.json"
COOLDOWN_S = 300  # 5 min same-event cooldown
CAD_PER_USD = 1.37  # rough; for risk display

# Critical levels to monitor (updated 2026-05-14 22:54 after signal SL moved to 4620.66)
CRITICAL_LEVELS_XAUUSD: dict[str, dict] = {
    "tp_4705":       {"price": 4705.67, "side": "above", "label": "TP zone (signal TP)"},
    "bounce_4680":   {"price": 4680.0,  "side": "above", "label": "Bounce confirm above signal entries"},
    "warn_4640":     {"price": 4640.0,  "side": "below", "label": "Halfway to new SL 4620.66"},
    "warn_4625":     {"price": 4625.0,  "side": "below", "label": "SL approaching"},
    "sl_4620":       {"price": 4620.66, "side": "below", "label": "Signal SL imminent (4620.66)"},
}
SPREAD_WARN_XAUUSD = 30  # pts
SPREAD_WARN_XAGUSD = 60  # pts (XAG normally wider)

# Tickets to track (updated -- now signal + 4 followers)
TRACKED_TICKETS: set[int] = {
    4426580102,  # 0.11 lot signal source
    4426580306,  # 0.2 lot follower #1
    4426580637,  # 0.2 lot follower #2
    4426580704,  # 0.2 lot follower #3
    4426613344,  # 0.2 lot follower #4 (later add)
}

# Signal-follower mapping: when SIGNAL ticket closes (disappears from
# positions_get), auto-close all FOLLOWER tickets at market. Used to sync
# follower positions with an external signal provider's exit timing.
SIGNAL_FOLLOWERS: dict[int, list[int]] = {
    4426580102: [4426580306, 4426580637, 4426580704, 4426613344],  # Telegram signal LONG -> 4 followers
}


def load_state() -> dict:
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def can_fire(state: dict, event_key: str, now: datetime) -> bool:
    """Cooldown check: True if event hasn't fired in COOLDOWN_S seconds."""
    last = state.get("last_fired", {}).get(event_key)
    if last is None:
        return True
    last_dt = datetime.fromisoformat(last)
    return (now - last_dt).total_seconds() >= COOLDOWN_S


def mark_fired(state: dict, event_key: str, now: datetime) -> None:
    state.setdefault("last_fired", {})[event_key] = now.isoformat()


def main() -> int:
    from mt5_connect import init_mt5_live
    import MetaTrader5 as mt5
    from quant.config import get_live_mt5_path

    now = datetime.now(tz=timezone.utc)
    state = load_state()
    events: list[dict] = []

    # allow_orders=True ONLY because we may need to close follower positions
    # when their signal closes (signal-follower sync logic below).
    # No new positions opened from this script -- only closes.
    if not init_mt5_live(terminal_path=get_live_mt5_path(), allow_orders=True):
        print(json.dumps({
            "push_needed": False, "error": "init_mt5_live failed",
            "ts": now.isoformat(),
        }))
        return 1

    try:
        # 1. Check positions — detect closes
        positions = mt5.positions_get() or []
        live_tickets = {p.ticket for p in positions}
        last_known_tickets = set(state.get("known_tickets", []))

        # Position newly closed (was in last_known but not in live)
        closed = last_known_tickets - live_tickets
        for ticket in closed:
            if ticket in TRACKED_TICKETS:
                # Try to pull deal history to know how it closed
                deals_end = now
                deals_start = deals_end - timedelta(minutes=5)
                deals = mt5.history_deals_get(deals_start, deals_end) or []
                closing_deal = None
                for d in reversed(deals):
                    if d.position_id == ticket and d.entry == 1:  # entry=1 means close
                        closing_deal = d
                        break
                if closing_deal:
                    pnl = closing_deal.profit + closing_deal.commission + closing_deal.swap
                    msg = (
                        f"Position #{ticket} CLOSED at {closing_deal.price:.2f}, "
                        f"P&L {pnl:+.2f} CAD"
                    )
                else:
                    msg = f"Position #{ticket} CLOSED (deal lookup pending)"
                event_key = f"closed_{ticket}"
                if can_fire(state, event_key, now):
                    events.append({"type": "position_closed", "ticket": ticket, "msg": msg})
                    mark_fired(state, event_key, now)

        # 1b. Signal-follower sync: if SIGNAL ticket disappeared but followers
        # still open, close the followers at market to mirror signal exit.
        for signal_ticket, follower_tickets in SIGNAL_FOLLOWERS.items():
            signal_alive = signal_ticket in live_tickets
            alive_followers = [t for t in follower_tickets if t in live_tickets]

            if (not signal_alive) and alive_followers:
                # Signal closed, followers still open — close them all
                event_key = f"signal_follow_close_{signal_ticket}"
                if can_fire(state, event_key, now):
                    closed_results = []
                    for ft in alive_followers:
                        try:
                            positions_ft = mt5.positions_get(ticket=ft)
                            if not positions_ft:
                                continue
                            pos = positions_ft[0]
                            symbol = pos.symbol
                            tick_ft = mt5.symbol_info_tick(symbol)
                            info_ft = mt5.symbol_info(symbol)
                            if tick_ft is None or info_ft is None:
                                continue

                            # Close direction is opposite of position type
                            if pos.type == mt5.POSITION_TYPE_BUY:
                                close_type = mt5.ORDER_TYPE_SELL
                                price_close = tick_ft.bid
                            else:
                                close_type = mt5.ORDER_TYPE_BUY
                                price_close = tick_ft.ask

                            req = {
                                "action": mt5.TRADE_ACTION_DEAL,
                                "symbol": symbol,
                                "volume": pos.volume,
                                "type": close_type,
                                "position": int(ft),
                                "price": price_close,
                                "deviation": 50,
                                "magic": pos.magic,
                                "comment": f"CC-SIGFOLLOW-CLOSE"[:31],
                                "type_time": mt5.ORDER_TIME_GTC,
                                "type_filling": (
                                    mt5.ORDER_FILLING_FOK if info_ft.filling_mode == 2
                                    else mt5.ORDER_FILLING_IOC if info_ft.filling_mode == 1
                                    else mt5.ORDER_FILLING_RETURN
                                ),
                            }
                            res = mt5.order_send(req)
                            if res is not None and res.retcode == mt5.TRADE_RETCODE_DONE:
                                closed_results.append(f"#{ft}@{res.price:.2f}")
                            else:
                                rc = res.retcode if res else "None"
                                closed_results.append(f"#{ft}FAIL(rc={rc})")
                        except Exception as exc:  # noqa: BLE001
                            closed_results.append(f"#{ft}EXC({exc})")
                    events.append({
                        "type": "signal_follow_close",
                        "msg": f"Signal #{signal_ticket} closed -> closed followers: {', '.join(closed_results)}",
                    })
                    mark_fired(state, event_key, now)

        # 2. Current XAUUSD tick + threshold cross detection
        mt5.symbol_select("XAUUSD", True)
        tick = mt5.symbol_info_tick("XAUUSD")
        info = mt5.symbol_info("XAUUSD")
        if tick is not None and info is not None:
            mid = (tick.bid + tick.ask) / 2
            spread_pt = (tick.ask - tick.bid) / info.point
            prev_mid = state.get("last_xauusd_mid")

            # Threshold crossings
            for key, lvl in CRITICAL_LEVELS_XAUUSD.items():
                price = lvl["price"]
                side = lvl["side"]
                label = lvl["label"]
                # Side = "above" means alert when current goes > price
                # Side = "below" means alert when current goes < price
                if prev_mid is not None:
                    if side == "above" and prev_mid <= price < mid:
                        if can_fire(state, key, now):
                            events.append({
                                "type": "cross_above", "key": key,
                                "msg": f"XAU crossed ABOVE {price} ({label}). bid={tick.bid:.2f}",
                            })
                            mark_fired(state, key, now)
                    elif side == "below" and prev_mid >= price > mid:
                        if can_fire(state, key, now):
                            events.append({
                                "type": "cross_below", "key": key,
                                "msg": f"XAU crossed BELOW {price} ({label}). bid={tick.bid:.2f}",
                            })
                            mark_fired(state, key, now)
                else:
                    # First run -- if already on alert side, fire once
                    if side == "above" and mid > price:
                        if can_fire(state, key, now):
                            events.append({
                                "type": "cross_above", "key": key,
                                "msg": f"XAU starts ABOVE {price} ({label}). bid={tick.bid:.2f}",
                            })
                            mark_fired(state, key, now)
                    elif side == "below" and mid < price:
                        if can_fire(state, key, now):
                            events.append({
                                "type": "cross_below", "key": key,
                                "msg": f"XAU starts BELOW {price} ({label}). bid={tick.bid:.2f}",
                            })
                            mark_fired(state, key, now)

            # Spread anomaly
            if spread_pt > SPREAD_WARN_XAUUSD:
                event_key = "spread_xau_high"
                if can_fire(state, event_key, now):
                    events.append({
                        "type": "spread_high",
                        "msg": f"XAU spread {spread_pt:.0f}pt > {SPREAD_WARN_XAUUSD}pt. Liquidity thin.",
                    })
                    mark_fired(state, event_key, now)

            state["last_xauusd_mid"] = mid
            state["last_xauusd_spread"] = spread_pt

        # 3. Combined P&L delta on tracked positions
        tracked_open = [p for p in positions if p.ticket in TRACKED_TICKETS]
        combined_pnl = sum(p.profit + p.swap for p in tracked_open)
        prev_pnl = state.get("last_combined_pnl")
        if prev_pnl is not None and tracked_open:
            delta = combined_pnl - prev_pnl
            # Use CAD threshold (account currency is CAD)
            if delta <= -100:  # worsened by >$100 CAD since last check
                event_key = "pnl_deteriorate"
                if can_fire(state, event_key, now):
                    events.append({
                        "type": "pnl_drop",
                        "msg": f"XAU combined P&L dropped {delta:+.0f} CAD to {combined_pnl:+.0f} in 1min",
                    })
                    mark_fired(state, event_key, now)
            elif delta >= +100:
                event_key = "pnl_improve"
                if can_fire(state, event_key, now):
                    events.append({
                        "type": "pnl_jump",
                        "msg": f"XAU combined P&L improved {delta:+.0f} CAD to {combined_pnl:+.0f} in 1min",
                    })
                    mark_fired(state, event_key, now)
        state["last_combined_pnl"] = combined_pnl
        state["known_tickets"] = list(live_tickets)

        # Always include current state for context (the driver Claude can use)
        snapshot = {
            "ts": now.isoformat(),
            "xauusd_mid": mid if tick else None,
            "xauusd_spread_pt": spread_pt if tick else None,
            "combined_pnl_cad": combined_pnl,
            "tracked_positions_open": len(tracked_open),
            "events": events,
            "push_needed": len(events) > 0,
            "push_message": " | ".join(e["msg"] for e in events) if events else None,
        }

        save_state(state)
        print(json.dumps(snapshot, default=str))
        return 0

    finally:
        mt5.shutdown()


if __name__ == "__main__":
    sys.exit(main())

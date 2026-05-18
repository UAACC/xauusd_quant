"""Live order execution — single entry point with hardcoded 0.02 lot ceiling.

User authorization scope (2026-05-14, see feedback-execute-authorization memory):
- Live account orders permitted via this module ONLY
- HARDCODED volume = 0.02 lot per order (no caller can override)
- Per-trade user confirmation required (orchestrated by caller)
- Auto-journals to live_analysis.journal on fill
- Magic number = USER_MAGIC_BASE + date offset for tracking

This is the ONLY code path in the project that calls ``mt5.order_send`` against
a live account. Any direct ``mt5.order_send`` call in other modules is a
contract violation and should fail review.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent

# Hardcoded 0.02 lot — user's risk control (see feedback-execute-authorization)
FIXED_LOT = 0.02
MAGIC_BASE = 20260514  # user authorized 2026-05-14; date as magic seed

Direction = Literal["long", "short"]
OrderType = Literal["market", "limit", "stop"]


def _validate(symbol: str, direction: Direction, entry: float, sl: float, tp: float,
              order_type: OrderType) -> None:
    """Pre-flight sanity. Raise on any violation. NOT a risk overlay -- just
    structural correctness (avoid sending obviously-broken orders to broker)."""
    if direction not in ("long", "short"):
        raise ValueError(f"direction must be long|short, got {direction!r}")
    if order_type not in ("market", "limit", "stop"):
        raise ValueError(f"order_type must be market|limit|stop, got {order_type!r}")
    if entry <= 0 or sl <= 0 or tp <= 0:
        raise ValueError(f"prices must be positive: entry={entry} sl={sl} tp={tp}")
    # Direction-aware ordering: long needs sl < entry < tp; short needs tp < entry < sl
    if direction == "long" and not (sl < entry < tp):
        raise ValueError(
            f"long requires sl < entry < tp; got sl={sl} entry={entry} tp={tp}"
        )
    if direction == "short" and not (tp < entry < sl):
        raise ValueError(
            f"short requires tp < entry < sl; got sl={sl} entry={entry} tp={tp}"
        )


def _next_magic() -> int:
    """Generate a unique-ish magic number for this order (date-based + minute)."""
    now = datetime.now(timezone.utc)
    # MAGIC_BASE + sequence-of-the-day (minute-of-day) — unique within a day
    minute_of_day = now.hour * 60 + now.minute
    return MAGIC_BASE * 10000 + minute_of_day


def place_order_safe(
    *,
    symbol: str,
    direction: Direction,
    entry: float,
    sl: float,
    tp: float,
    order_type: OrderType = "market",
    rationale: str,
    setup_grade: str = "A",  # "A" / "B-discretionary" / etc., goes to journal
    terminal_path: Optional[str] = None,
    dry_run: bool = False,
) -> dict:
    """Place ONE order at the fixed 0.02 lot. Single entry point.

    Args:
        symbol: e.g. "XAUUSD", "XAGUSD"
        direction: "long" or "short"
        entry: trigger price (used directly for limit/stop; ignored for market
            -- broker fills at current bid/ask)
        sl: stop loss price (above entry for short, below for long)
        tp: take profit price (below entry for short, above for long)
        order_type: "market" / "limit" / "stop"
            For long: limit = BUY_LIMIT (below current), stop = BUY_STOP (above)
            For short: limit = SELL_LIMIT (above current), stop = SELL_STOP (below)
        rationale: free-text describing why this trade. Goes to journal.
        setup_grade: A / B-discretionary / etc. — journal field.
        terminal_path: path to MT5 terminal64.exe. Default = get_live_mt5_path().
        dry_run: if True, build the request and validate but do NOT send.

    Returns:
        dict with: ``status`` ("filled" / "pending" / "rejected" / "dry_run"),
        ``ticket``, ``fill_price``, ``actual_volume``, ``request_id``,
        ``magic``, plus echo of inputs.

    Raises:
        ValueError: on invalid inputs (caught before any MT5 call).
        RuntimeError: on MT5 connection / send failure.
    """
    _validate(symbol, direction, entry, sl, tp, order_type)

    # Hardcoded volume — no kwarg available to override
    volume = FIXED_LOT
    if volume != 0.02:  # paranoid double-check (should be impossible)
        raise RuntimeError(f"FIXED_LOT corruption: expected 0.02, got {volume}")

    magic = _next_magic()
    comment_prefix = "CC-EXEC"  # Claude-Code execute
    comment = f"{comment_prefix}-{setup_grade}"[:31]  # MT5 max 31 chars

    # Lazy import — keeps module importable on non-Windows
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    from mt5_connect import init_mt5_live  # noqa: E402
    import MetaTrader5 as mt5  # noqa: E402
    from quant.config import get_live_mt5_path  # noqa: E402

    path = terminal_path or get_live_mt5_path()

    # Map direction + order_type to MT5 constants
    if direction == "long":
        if order_type == "market":
            mt5_type = mt5.ORDER_TYPE_BUY
            action = mt5.TRADE_ACTION_DEAL
        elif order_type == "limit":
            mt5_type = mt5.ORDER_TYPE_BUY_LIMIT
            action = mt5.TRADE_ACTION_PENDING
        else:  # stop
            mt5_type = mt5.ORDER_TYPE_BUY_STOP
            action = mt5.TRADE_ACTION_PENDING
    else:  # short
        if order_type == "market":
            mt5_type = mt5.ORDER_TYPE_SELL
            action = mt5.TRADE_ACTION_DEAL
        elif order_type == "limit":
            mt5_type = mt5.ORDER_TYPE_SELL_LIMIT
            action = mt5.TRADE_ACTION_PENDING
        else:  # stop
            mt5_type = mt5.ORDER_TYPE_SELL_STOP
            action = mt5.TRADE_ACTION_PENDING

    if dry_run:
        # Build the request dict but do NOT call order_send / initialize
        request = {
            "action": action, "symbol": symbol, "volume": volume,
            "type": mt5_type, "price": entry, "sl": sl, "tp": tp,
            "deviation": 20, "magic": magic, "comment": comment,
            "type_time": mt5.ORDER_TIME_GTC if hasattr(mt5, "ORDER_TIME_GTC") else 0,
        }
        return {
            "status": "dry_run",
            "request": request,
            "would_send_to_path": path,
            "magic": magic,
            "volume_validated": volume,
            "symbol": symbol, "direction": direction,
            "entry": entry, "sl": sl, "tp": tp,
            "order_type": order_type, "rationale": rationale,
            "setup_grade": setup_grade,
        }

    if not init_mt5_live(terminal_path=path, allow_orders=True):
        raise RuntimeError("MT5 init_mt5_live failed; cannot send order")

    try:
        if not mt5.symbol_select(symbol, True):
            raise RuntimeError(f"symbol_select({symbol}) failed: {mt5.last_error()}")
        info = mt5.symbol_info(symbol)
        if info is None:
            raise RuntimeError(f"symbol_info({symbol}) None")

        # For market orders, use current ask (for BUY) or bid (for SELL)
        if order_type == "market":
            tick = mt5.symbol_info_tick(symbol)
            if tick is None:
                raise RuntimeError("symbol_info_tick None")
            actual_price = tick.ask if direction == "long" else tick.bid
        else:
            actual_price = entry

        request = {
            "action": action,
            "symbol": symbol,
            "volume": volume,
            "type": mt5_type,
            "price": actual_price,
            "sl": sl,
            "tp": tp,
            "deviation": 20,
            "magic": magic,
            "comment": comment,
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": (
                mt5.ORDER_FILLING_FOK if info.filling_mode == 2
                else mt5.ORDER_FILLING_IOC if info.filling_mode == 1
                else mt5.ORDER_FILLING_RETURN
            ),
        }

        result = mt5.order_send(request)
        if result is None:
            raise RuntimeError(f"order_send returned None: {mt5.last_error()}")

        out = {
            "status": "filled" if order_type == "market" and result.retcode == mt5.TRADE_RETCODE_DONE else
                       "pending" if order_type != "market" and result.retcode == mt5.TRADE_RETCODE_DONE else
                       "rejected",
            "retcode": result.retcode,
            "ticket": int(getattr(result, "order", 0)) or int(getattr(result, "deal", 0)),
            "fill_price": float(getattr(result, "price", actual_price)),
            "actual_volume": float(getattr(result, "volume", volume)),
            "request_id": int(getattr(result, "request_id", 0)),
            "magic": magic,
            "comment": result.comment,
            "symbol": symbol, "direction": direction,
            "entry": entry, "sl": sl, "tp": tp,
            "order_type": order_type, "rationale": rationale,
            "setup_grade": setup_grade,
        }

        # Auto-journal on successful fill (market) or pending placement
        if out["status"] in ("filled", "pending"):
            try:
                from live_analysis import journal
                journal.log_entry(
                    symbol=symbol,
                    direction=direction,
                    lots=volume,
                    entry=out["fill_price"],
                    sl=sl,
                    tp=tp,
                    rationale=f"[{setup_grade}] {rationale} | ticket={out['ticket']} | "
                              f"order_type={order_type} | retcode={result.retcode}",
                )
            except Exception as exc:  # noqa: BLE001
                out["journal_error"] = str(exc)

        return out
    finally:
        mt5.shutdown()


def cancel_pending(*, ticket: int, terminal_path: Optional[str] = None) -> dict:
    """Cancel a pending order by ticket number. Returns {status, retcode}.

    Note: this is a write operation but doesn't open a new position. Allowed
    under the execute authorization scope as part of trade management.
    """
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    from mt5_connect import init_mt5_live  # noqa: E402
    import MetaTrader5 as mt5  # noqa: E402
    from quant.config import get_live_mt5_path  # noqa: E402

    path = terminal_path or get_live_mt5_path()
    if not init_mt5_live(terminal_path=path, allow_orders=True):
        raise RuntimeError("MT5 init_mt5_live failed; cannot cancel")
    try:
        request = {
            "action": mt5.TRADE_ACTION_REMOVE,
            "order": int(ticket),
        }
        result = mt5.order_send(request)
        if result is None:
            raise RuntimeError(f"order_send (REMOVE) None: {mt5.last_error()}")
        return {
            "status": "cancelled" if result.retcode == mt5.TRADE_RETCODE_DONE else "rejected",
            "retcode": result.retcode,
            "ticket": ticket,
            "comment": result.comment,
        }
    finally:
        mt5.shutdown()


def modify_position_sltp(
    *,
    ticket: int,
    sl: Optional[float] = None,
    tp: Optional[float] = None,
    terminal_path: Optional[str] = None,
) -> dict:
    """Modify SL and/or TP on an existing position.

    This is a position MANAGEMENT operation, not opening a new trade. Does NOT
    enforce the 0.02 lot ceiling (existing position can be any size the user
    placed manually). Validates that the new SL/TP are on the correct side
    relative to the position direction and current price.

    Pass None for sl or tp to leave that field unchanged (uses current value).
    """
    if sl is None and tp is None:
        raise ValueError("must specify at least one of sl or tp")

    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    from mt5_connect import init_mt5_live  # noqa: E402
    import MetaTrader5 as mt5  # noqa: E402
    from quant.config import get_live_mt5_path  # noqa: E402

    path = terminal_path or get_live_mt5_path()
    if not init_mt5_live(terminal_path=path, allow_orders=True):
        raise RuntimeError("MT5 init_mt5_live failed; cannot modify")
    try:
        positions = mt5.positions_get(ticket=int(ticket))
        if not positions:
            raise RuntimeError(f"position {ticket} not found")
        pos = positions[0]

        # Use position current SL/TP if caller didn't override
        new_sl = float(sl) if sl is not None else float(pos.sl)
        new_tp = float(tp) if tp is not None else float(pos.tp)

        # Validate direction-aware: long requires SL < entry < TP; short inverse
        # Use the position's CURRENT price for sanity (not entry) since position
        # may already be in profit/loss.
        if pos.type == mt5.POSITION_TYPE_BUY:
            if new_sl > 0 and new_sl >= pos.price_current:
                raise ValueError(
                    f"LONG SL must be below current price ({pos.price_current:.5f}); "
                    f"got {new_sl}"
                )
            if new_tp > 0 and new_tp <= pos.price_current:
                raise ValueError(
                    f"LONG TP must be above current price ({pos.price_current:.5f}); "
                    f"got {new_tp}"
                )
        else:  # SELL
            if new_sl > 0 and new_sl <= pos.price_current:
                raise ValueError(
                    f"SHORT SL must be above current price ({pos.price_current:.5f}); "
                    f"got {new_sl}"
                )
            if new_tp > 0 and new_tp >= pos.price_current:
                raise ValueError(
                    f"SHORT TP must be below current price ({pos.price_current:.5f}); "
                    f"got {new_tp}"
                )

        request = {
            "action": mt5.TRADE_ACTION_SLTP,
            "symbol": pos.symbol,
            "position": int(ticket),
            "sl": new_sl,
            "tp": new_tp,
        }
        result = mt5.order_send(request)
        if result is None:
            raise RuntimeError(f"order_send (SLTP) None: {mt5.last_error()}")

        return {
            "status": "modified" if result.retcode == mt5.TRADE_RETCODE_DONE else "rejected",
            "retcode": result.retcode,
            "ticket": ticket,
            "old_sl": float(pos.sl),
            "new_sl": new_sl,
            "old_tp": float(pos.tp),
            "new_tp": new_tp,
            "comment": result.comment,
            "symbol": pos.symbol,
            "volume": float(pos.volume),
            "entry_price": float(pos.price_open),
        }
    finally:
        mt5.shutdown()


def close_position(*, ticket: int, terminal_path: Optional[str] = None) -> dict:
    """Close an open position by ticket. Hardcoded 0.02 lot wouldn't help
    here since we close the EXISTING position's volume (which should be 0.02
    if we opened it via place_order_safe)."""
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    from mt5_connect import init_mt5_live  # noqa: E402
    import MetaTrader5 as mt5  # noqa: E402
    from quant.config import get_live_mt5_path  # noqa: E402

    path = terminal_path or get_live_mt5_path()
    if not init_mt5_live(terminal_path=path, allow_orders=True):
        raise RuntimeError("MT5 init_mt5_live failed")
    try:
        positions = mt5.positions_get(ticket=int(ticket))
        if not positions:
            raise RuntimeError(f"position {ticket} not found")
        pos = positions[0]
        if pos.volume > FIXED_LOT * 1.5:
            # Sanity: don't accidentally close a position larger than what we'd open
            raise RuntimeError(
                f"position volume {pos.volume} exceeds expected 0.02 -- refusing to close"
            )

        symbol = pos.symbol
        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            raise RuntimeError("symbol_info_tick None")

        # Close direction is opposite of position type
        if pos.type == mt5.POSITION_TYPE_BUY:
            close_type = mt5.ORDER_TYPE_SELL
            price = tick.bid
        else:
            close_type = mt5.ORDER_TYPE_BUY
            price = tick.ask

        info = mt5.symbol_info(symbol)
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": pos.volume,
            "type": close_type,
            "position": int(ticket),
            "price": price,
            "deviation": 20,
            "magic": pos.magic,
            "comment": f"CC-CLOSE-{ticket}"[:31],
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": (
                mt5.ORDER_FILLING_FOK if info.filling_mode == 2
                else mt5.ORDER_FILLING_IOC if info.filling_mode == 1
                else mt5.ORDER_FILLING_RETURN
            ),
        }
        result = mt5.order_send(request)
        if result is None:
            raise RuntimeError(f"order_send (close) None: {mt5.last_error()}")

        out = {
            "status": "closed" if result.retcode == mt5.TRADE_RETCODE_DONE else "rejected",
            "retcode": result.retcode,
            "ticket": ticket,
            "close_price": float(getattr(result, "price", price)),
            "comment": result.comment,
        }

        # Auto-journal exit
        if out["status"] == "closed":
            try:
                from live_analysis import journal
                journal.log_exit(
                    trade_id=str(ticket),
                    symbol=symbol,
                    exit_price=out["close_price"],
                    reason="manual",
                    net_pnl=float(pos.profit),
                )
            except Exception as exc:  # noqa: BLE001
                out["journal_error"] = str(exc)

        return out
    finally:
        mt5.shutdown()

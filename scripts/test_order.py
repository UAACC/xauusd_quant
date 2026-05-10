"""
test_order.py — XAUUSD demo programmatic round-trip slippage test.

Purpose
-------
End-to-end validation of the Python -> MT5 -> broker -> fill -> deal path on
demo. Opens a 0.01 lot BUY, holds a few seconds, closes via SELL with the
position field set, and reports slippage on both legs plus net PnL.

This is a stage-0 plumbing test, not a strategy. Once we've confirmed the
pipeline works and our CostModel predicts costs accurately, we move on to
data / research (stage 1+) without ever sending live orders from Python --
production execution lives in MQL5 per the architecture decision.

Safety
------
- Default = DRY RUN. Connects, snapshots ask/bid, prints predicted cost.
  Does NOT call order_send. Add --execute to actually trade.
- init_mt5() aborts hard on trade_mode == 2 (REAL).
- TERMINAL_PATH pins the connection to the demo terminal regardless of
  which MT5 was launched most recently.
- Magic number 20260510 identifies orders sent by this script in deal
  history.

Usage
-----
  python scripts/test_order.py             # dry run (no orders sent)
  python scripts/test_order.py --execute   # actually sends round-trip
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

# Reuse connection / CostModel from mt5_connect.py in the same dir.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from mt5_connect import (  # noqa: E402  (path injection above)
    SYMBOL,
    build_cost_model,
    get_symbol_info,
    init_mt5,
)

import MetaTrader5 as mt5  # noqa: E402


VOLUME = 0.01                 # 0.01 lot = 1 oz; smallest tradable size
MAGIC = 20260510              # arbitrary identifier for this script's orders
COMMENT = "stage0-slippage"
DEVIATION_POINTS = 50         # max slippage tolerance before broker rejects (~$50/lot worst case)
HOLD_SECONDS = 5
TERMINAL_PATH = r"F:\demo-mt5\terminal64.exe"
COMMISSION_ROUNDTRIP_USD = 7.0


def _send(direction: int, position_id: int | None = None) -> dict:
    """Send a 0.01 lot market order; return request/fill snapshot.

    direction = mt5.ORDER_TYPE_BUY or mt5.ORDER_TYPE_SELL.
    position_id is set on closing legs (Hedge mode) to specify which
    position to flatten.
    """
    tick = mt5.symbol_info_tick(SYMBOL)
    if tick is None:
        raise RuntimeError(f"symbol_info_tick failed: {mt5.last_error()}")
    request_price = tick.ask if direction == mt5.ORDER_TYPE_BUY else tick.bid

    req: dict = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": SYMBOL,
        "volume": VOLUME,
        "type": direction,
        "price": request_price,
        "deviation": DEVIATION_POINTS,
        "magic": MAGIC,
        "comment": COMMENT,
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,  # matches spec
    }
    if position_id is not None:
        req["position"] = position_id

    result = mt5.order_send(req)
    if result is None:
        raise RuntimeError(f"order_send returned None: {mt5.last_error()}")
    if result.retcode != mt5.TRADE_RETCODE_DONE:
        raise RuntimeError(
            f"order_send failed retcode={result.retcode} "
            f"comment={result.comment!r} request={req}"
        )
    return {
        "request_price": request_price,
        "fill_price": result.price,
        "deal_id": result.deal,
        "order_id": result.order,
        "snapshot_bid": tick.bid,
        "snapshot_ask": tick.ask,
    }


def _slippage_pts(direction: int, request: float, fill: float, point: float) -> int:
    """Signed slippage in points. Positive = filled worse than requested.

    BUY: worse = filled higher than the ask we saw.
    SELL: worse = filled lower than the bid we saw.
    """
    raw = fill - request if direction == mt5.ORDER_TYPE_BUY else request - fill
    return round(raw / point)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--execute",
        action="store_true",
        help="Actually send orders. Without this flag, dry-run only.",
    )
    args = ap.parse_args()

    if not init_mt5(terminal_path=TERMINAL_PATH):
        return 1

    try:
        info = get_symbol_info(SYMBOL)
        if info is None:
            return 2

        cost = build_cost_model(info, commission_roundtrip_usd=COMMISSION_ROUNDTRIP_USD)
        tick = mt5.symbol_info_tick(SYMBOL)
        predicted_cost_for_vol = cost.total_roundtrip_usd_per_lot() * VOLUME

        print(f"\n=== Pre-trade snapshot ===")
        print(f"  ask                 : {tick.ask}")
        print(f"  bid                 : {tick.bid}")
        print(f"  spread (live)       : {cost.spread_points} pts (${cost.spread_cost_usd_per_lot():.2f}/lot)")
        print(f"  volume              : {VOLUME} lot")
        print(f"  predicted r/t cost  : ${predicted_cost_for_vol:.4f}")
        print(f"                        (${cost.total_roundtrip_usd_per_lot():.2f}/lot * {VOLUME} lot)")
        print(f"  filling mode        : IOC ({mt5.ORDER_FILLING_IOC})")
        print(f"  max deviation       : {DEVIATION_POINTS} pts")

        if not args.execute:
            print(f"\n[DRY RUN] No orders sent. Add --execute to send a real round-trip.")
            return 0

        print(f"\n=== Opening BUY 0.01 lot ===")
        op = _send(mt5.ORDER_TYPE_BUY)
        op_slip_pts = _slippage_pts(mt5.ORDER_TYPE_BUY, op["request_price"], op["fill_price"], cost.point)
        op_slip_usd = op_slip_pts * cost.tick_value * VOLUME
        print(f"  request_ask         : {op['request_price']}")
        print(f"  fill_price          : {op['fill_price']}")
        print(f"  slippage            : {op_slip_pts:+d} pts (${op_slip_usd:+.4f})  [+ = worse fill]")
        print(f"  deal_id / order_id  : {op['deal_id']} / {op['order_id']}")

        position_id = op["order_id"]  # Hedge mode: position ticket = opening order ticket

        print(f"\n=== Holding {HOLD_SECONDS}s ===")
        time.sleep(HOLD_SECONDS)

        print(f"\n=== Closing position (SELL 0.01 lot) ===")
        cl = _send(mt5.ORDER_TYPE_SELL, position_id=position_id)
        cl_slip_pts = _slippage_pts(mt5.ORDER_TYPE_SELL, cl["request_price"], cl["fill_price"], cost.point)
        cl_slip_usd = cl_slip_pts * cost.tick_value * VOLUME
        print(f"  request_bid         : {cl['request_price']}")
        print(f"  fill_price          : {cl['fill_price']}")
        print(f"  slippage            : {cl_slip_pts:+d} pts (${cl_slip_usd:+.4f})  [+ = worse fill]")
        print(f"  deal_id / order_id  : {cl['deal_id']} / {cl['order_id']}")

        gross_pnl = (cl["fill_price"] - op["fill_price"]) * info.trade_contract_size * VOLUME
        total_slip_usd = op_slip_usd + cl_slip_usd

        print(f"\n=== Round-trip summary ===")
        print(f"  gross PnL (fill - fill)    : ${gross_pnl:+.4f}")
        print(f"  total slippage cost        : ${total_slip_usd:+.4f}")
        print(f"  predicted r/t cost (model) : ${predicted_cost_for_vol:.4f}")
        print(f"  commission (broker-side)   : not in gross_pnl; check Toolbox > History for the deduction")
        print(f"  account balance delta will be approx: gross_pnl - 2*${cost.commission_roundtrip_usd/2 * VOLUME:.4f}")

        return 0
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    sys.exit(main())

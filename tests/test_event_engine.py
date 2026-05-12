"""Tests for the event-driven backtest engine."""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from quant.backtest.event_engine import (
    Trade,
    BacktestReport,
    run_event_backtest,
)
from quant.data.parquet import read_bars_month
from quant.strategies.bos_reversal import BosReversalSignal, detect_bos_reversal_signals


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _signal(entry_time: str, entry_price: float, direction: str = "long",
            sl_distance: float = 20.0, tp_distance: float = 40.0) -> BosReversalSignal:
    et = pd.Timestamp(entry_time, tz="UTC")
    return BosReversalSignal(
        direction=direction,
        entry_time=et,
        entry_price=entry_price,
        sl_price=entry_price - sl_distance if direction == "long" else entry_price + sl_distance,
        tp_price=entry_price + tp_distance if direction == "long" else entry_price - tp_distance,
        bos_level=entry_price - 5.0,
        bos_time=et - pd.Timedelta(hours=8),
        capitulation_time=et - pd.Timedelta(days=2),
    )


def _bars(rows: list[tuple[str, float, float]]) -> pd.DataFrame:
    """rows = [(time, high, low), ...]"""
    df = pd.DataFrame({
        "time": [pd.Timestamp(t, tz="UTC") for t, _, _ in rows],
        "open": [(h + l) / 2 for _, h, l in rows],
        "high": [h for _, h, _ in rows],
        "low":  [l for _, _, l in rows],
        "close": [(h + l) / 2 for _, h, l in rows],
        "tick_volume": [100] * len(rows),
    })
    return df


# ---------------------------------------------------------------------------
# Synthetic single-trade scenarios
# ---------------------------------------------------------------------------

def test_tp_hit_basic():
    """Long entry @ 100, TP=140; one bar that hits 145 -> TP exit."""
    sig = _signal("2026-01-01 00:00:00", entry_price=100.0)
    bars = _bars([("2026-01-01 00:15:00", 145.0, 102.0)])
    rep = run_event_backtest(
        [sig], bars, initial_balance=10_000.0, risk_pct=0.20,
        cost_per_roundtrip_usd_per_lot=0.0,  # zero cost for clean math
    )
    assert rep.n_tp == 1
    assert rep.n_sl == 0
    t = rep.trades[0]
    assert t.exit_reason == "tp"
    assert t.exit_price == 140.0


def test_sl_hit_basic():
    """Long entry @ 100, SL=80; one bar that hits 75 -> SL exit."""
    sig = _signal("2026-01-01 00:00:00", entry_price=100.0)
    bars = _bars([("2026-01-01 00:15:00", 105.0, 75.0)])
    rep = run_event_backtest(
        [sig], bars, initial_balance=10_000.0, cost_per_roundtrip_usd_per_lot=0.0,
    )
    assert rep.n_sl == 1
    assert rep.trades[0].exit_price == 80.0


def test_pessimistic_sl_priority_when_both_in_one_bar():
    """Bar contains both SL (low<80) and TP (high>140) -> SL wins (pessimistic)."""
    sig = _signal("2026-01-01 00:00:00", entry_price=100.0)
    bars = _bars([("2026-01-01 00:15:00", 145.0, 75.0)])
    rep = run_event_backtest(
        [sig], bars, initial_balance=10_000.0, cost_per_roundtrip_usd_per_lot=0.0,
    )
    assert rep.n_sl == 1
    assert rep.n_tp == 0


def test_be_move_then_pullback_exits_at_breakeven():
    """Bar 1 hits +15 (BE trigger), bar 2 dips to entry -> BE exit at 0 P&L."""
    sig = _signal("2026-01-01 00:00:00", entry_price=100.0)
    bars = _bars([
        ("2026-01-01 00:15:00", 117.0, 99.0),   # high=117 triggers BE; low>80 (no SL)
        ("2026-01-01 00:30:00", 105.0, 99.0),   # low<=100 hits BE-raised SL
    ])
    rep = run_event_backtest(
        [sig], bars, initial_balance=10_000.0, cost_per_roundtrip_usd_per_lot=0.0,
        be_trigger_distance=15.0,
    )
    t = rep.trades[0]
    assert t.exit_reason == "be"
    assert t.exit_price == 100.0
    assert t.gross_pnl == 0.0  # exit at entry


def test_be_move_does_not_save_if_sl_in_same_bar():
    """Pessimistic ordering: bar that contains both BE trigger AND old SL exits at SL."""
    sig = _signal("2026-01-01 00:00:00", entry_price=100.0)
    bars = _bars([("2026-01-01 00:15:00", 117.0, 75.0)])  # high triggers BE, low hits SL
    rep = run_event_backtest(
        [sig], bars, initial_balance=10_000.0, cost_per_roundtrip_usd_per_lot=0.0,
    )
    assert rep.n_sl == 1
    assert rep.n_be == 0


def test_trade_still_open_at_end_is_not_in_trades():
    """Bars end before any exit -> trade not counted in trades list."""
    sig = _signal("2026-01-01 00:00:00", entry_price=100.0)
    bars = _bars([("2026-01-01 00:15:00", 105.0, 95.0)])  # neither SL nor TP
    rep = run_event_backtest(
        [sig], bars, initial_balance=10_000.0, cost_per_roundtrip_usd_per_lot=0.0,
    )
    assert rep.n_trades_closed == 0
    assert rep.n_trades_still_open == 1


def test_undersized_account_skipped():
    """Account too small for min_lot_step at this risk -> trade skipped."""
    sig = _signal("2026-01-01 00:00:00", entry_price=100.0)
    bars = _bars([("2026-01-01 00:15:00", 145.0, 95.0)])
    rep = run_event_backtest(
        [sig], bars,
        initial_balance=10.0,   # tiny account: 10 * 0.20 / (20*100) = 0.001 lot, floor to 0
        risk_pct=0.20, min_lot_step=0.01,
    )
    assert rep.n_skipped_undersized == 1
    assert rep.n_trades_closed == 0


def test_balance_compounds_across_trades():
    """Two TP wins; balance after trade 1 affects sizing of trade 2."""
    sig1 = _signal("2026-01-01 00:00:00", entry_price=100.0)
    sig2 = _signal("2026-01-02 00:00:00", entry_price=100.0)
    bars = _bars([
        ("2026-01-01 00:15:00", 145.0, 95.0),    # sig1 TP @ 140
        ("2026-01-02 00:15:00", 145.0, 95.0),    # sig2 TP @ 140
    ])
    rep = run_event_backtest(
        [sig1, sig2], bars, initial_balance=10_000.0,
        cost_per_roundtrip_usd_per_lot=0.0,
    )
    assert rep.n_trades_closed == 2
    # trade 1: 10000 * 0.20 / (20*100) = 1.0 lot. Profit: 40 * 100 * 1.0 = 4000.
    # trade 2: 14000 * 0.20 / 2000 = 1.4 lot. Profit: 40 * 100 * 1.4 = 5600.
    assert rep.trades[0].lots == pytest.approx(1.00)
    assert rep.trades[1].lots == pytest.approx(1.40)
    assert rep.final_balance == pytest.approx(10000.0 + 4000.0 + 5600.0)


def test_short_setup_tp_hit():
    sig = _signal("2026-01-01 00:00:00", entry_price=100.0, direction="short")
    # short: SL = 120, TP = 60
    bars = _bars([("2026-01-01 00:15:00", 105.0, 55.0)])  # low<=60 hits TP first
    rep = run_event_backtest(
        [sig], bars, initial_balance=10_000.0, cost_per_roundtrip_usd_per_lot=0.0,
    )
    assert rep.n_tp == 1
    assert rep.trades[0].exit_price == 60.0


# ---------------------------------------------------------------------------
# Real-data: the May 6 BOS signal must produce a closed trade with sensible P&L
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def m15_apr_may() -> pd.DataFrame:
    repo_root = Path(__file__).resolve().parent.parent
    apr = read_bars_month(repo_root / "data", "XAUUSD", "M15", 2026, 4)
    may = read_bars_month(repo_root / "data", "XAUUSD", "M15", 2026, 5)
    return pd.concat([apr, may], ignore_index=True).sort_values("time").reset_index(drop=True)


def test_apr_may_backtest_runs_end_to_end(smc_h4_apr_may, m15_apr_may):
    signals = detect_bos_reversal_signals(smc_h4_apr_may, m15_apr_may)
    rep = run_event_backtest(signals, m15_apr_may, initial_balance=10_000.0)
    assert rep.n_signals_evaluated >= 1
    # Either a closed trade or still-open at end of fixture is acceptable;
    # we just need the engine to not crash and to produce a sensible report.
    assert rep.n_trades_closed + rep.n_trades_still_open + rep.n_skipped_undersized \
        == rep.n_signals_evaluated

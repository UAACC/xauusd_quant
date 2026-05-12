"""Event-driven backtester with intrabar SL/TP triggering and BE management.

Why this exists alongside ``engine.py``:
The vectorised close-only engine is fine for indicator-style strategies
where exits depend on close prices. SMC-style strategies use **fixed-
distance** SL/TP that fire intrabar — a $20 SL on XAUUSD is routinely
violated by a single M15 wick whose bar still closes well inside the SL.
A close-only check would systematically *under*-report SL hits and
*over*-report TP hits, painting a flattering but false picture.

Per-bar conservative ordering
-----------------------------
Within a single bar, exit priority is:
    1. SL  (uses bar.low for long, bar.high for short)
    2. TP  (uses bar.high for long, bar.low for short)
    3. BE trigger (raises SL to entry; takes effect from the NEXT bar)

This is the standard pessimistic convention: when both SL and TP fall
within a single bar's range, we assume SL fired first. Likewise the BE
move is recognised only AFTER the bar's exit checks, so a bar that hit
both BE-trigger price and the original SL still exits via the original
SL — the BE move never had a chance to activate.

The realised stats will therefore underestimate "BE saves" and
overestimate "stopped out from a winner". Live trading with limit-style
TPs and stop-orders ought to do somewhat better; the gap is the cost of
backtester pessimism.

Balance / sizing
----------------
Each trade's lots are sized from the balance at its entry time using
``quant.risk.sizing.lots_for_risk``. Trades are processed in entry-time
order and the balance compounds. Overlapping signals are sized off the
balance available BEFORE prior trades closed; this is a known
simplification of v1 (matters only if signals overlap, which the May 4
fixture does not).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

import pandas as pd

from quant.risk.sizing import lots_for_risk
from quant.strategies.bos_reversal import BosReversalSignal


ExitReason = Literal["tp", "sl", "be", "open"]


@dataclass(frozen=True)
class Trade:
    signal: BosReversalSignal
    lots: float
    entry_time: pd.Timestamp
    entry_price: float
    exit_time: pd.Timestamp
    exit_price: float
    exit_reason: ExitReason
    gross_pnl: float          # quote-currency P&L on the price move
    cost: float               # round-trip cost
    net_pnl: float            # gross - cost
    holding_bars: int
    balance_before: float
    balance_after: float


@dataclass(frozen=True)
class BacktestReport:
    trades: list[Trade]
    n_signals_evaluated: int
    n_trades_closed: int
    n_trades_still_open: int
    n_skipped_undersized: int
    n_tp: int
    n_sl: int
    n_be: int
    total_gross_pnl: float
    total_cost: float
    total_net_pnl: float
    win_rate: float
    avg_trade_net: float
    max_drawdown: float
    initial_balance: float
    final_balance: float
    ideal_rr: float           # tp_distance / sl_distance per signal — should equal 2 for default config
    realised_rr: float        # avg-winner / abs(avg-loser); accounts for BE exits and intrabar slippage
    sharpe_annual: float      # annualised Sharpe of per-trade net P&L stream
    trades_per_year: float    # observed trade frequency

    def summary(self) -> str:
        lines = [
            f"=== SMC BOS-Reversal Backtest ===",
            f"  signals evaluated  : {self.n_signals_evaluated}",
            f"  trades closed      : {self.n_trades_closed}  (open at end: {self.n_trades_still_open}, "
            f"skipped under-sized: {self.n_skipped_undersized})",
            f"  exits TP / SL / BE : {self.n_tp} / {self.n_sl} / {self.n_be}",
            f"  win rate (net>0)   : {self.win_rate*100:>5.1f}%",
            f"  gross P&L          : ${self.total_gross_pnl:>+12.2f}",
            f"  total cost         : ${self.total_cost:>+12.2f}",
            f"  net P&L            : ${self.total_net_pnl:>+12.2f}",
            f"  avg trade net      : ${self.avg_trade_net:>+10.2f}",
            f"  max drawdown       : ${self.max_drawdown:>+12.2f}",
            f"  R:R ideal/realised : {self.ideal_rr:.2f} / {self.realised_rr:.2f}",
            f"  Sharpe (annual)    : {self.sharpe_annual:>+5.2f}  (n={self.n_trades_closed}, "
            f"freq={self.trades_per_year:.1f}/yr)",
            f"  balance start->end : ${self.initial_balance:,.2f} -> ${self.final_balance:,.2f}",
        ]
        return "\n".join(lines)


def _simulate_long(
    signal: BosReversalSignal,
    bars: pd.DataFrame,
    be_trigger_distance: float,
) -> tuple[pd.Timestamp, float, ExitReason, int] | None:
    """Walk forward bar-by-bar; return (exit_time, exit_price, reason, n_bars).

    Returns None if no exit happens before bars end (trade still open).
    """
    sl = signal.sl_price
    tp = signal.tp_price
    entry_px = signal.entry_price
    be_active = False

    after = bars[bars["time"] > signal.entry_time]
    for n_bars, (_, bar) in enumerate(after.iterrows(), start=1):
        # Pessimistic SL priority within a bar
        if bar["low"] <= sl:
            return (pd.Timestamp(bar["time"]), float(sl),
                    "be" if be_active else "sl", n_bars)
        if bar["high"] >= tp:
            return (pd.Timestamp(bar["time"]), float(tp), "tp", n_bars)
        # BE move only takes effect AFTER both exit checks fail this bar
        if not be_active and (bar["high"] - entry_px) >= be_trigger_distance:
            be_active = True
            sl = entry_px
    return None


def _simulate_short(
    signal: BosReversalSignal,
    bars: pd.DataFrame,
    be_trigger_distance: float,
) -> tuple[pd.Timestamp, float, ExitReason, int] | None:
    sl = signal.sl_price        # for shorts, sl > entry_price
    tp = signal.tp_price        # for shorts, tp < entry_price
    entry_px = signal.entry_price
    be_active = False

    after = bars[bars["time"] > signal.entry_time]
    for n_bars, (_, bar) in enumerate(after.iterrows(), start=1):
        if bar["high"] >= sl:
            return (pd.Timestamp(bar["time"]), float(sl),
                    "be" if be_active else "sl", n_bars)
        if bar["low"] <= tp:
            return (pd.Timestamp(bar["time"]), float(tp), "tp", n_bars)
        if not be_active and (entry_px - bar["low"]) >= be_trigger_distance:
            be_active = True
            sl = entry_px
    return None


def run_event_backtest(
    signals: list[BosReversalSignal],
    bars: pd.DataFrame,
    *,
    initial_balance: float = 10_000.0,
    risk_pct: float = 0.20,
    contract_size: float = 100.0,
    cost_per_roundtrip_usd_per_lot: float = 12.0,
    be_trigger_distance: float = 15.0,
    min_lot_step: float = 0.01,
    fixed_lots: float | None = None,
) -> BacktestReport:
    """Run all signals through the trade-lifecycle simulator; aggregate stats.

    Sizing mode:
      - default (``fixed_lots=None``): risk-based — each trade sized so an
        SL hit costs ``risk_pct`` of the running balance. Balance compounds.
      - ``fixed_lots=X``: every trade uses exactly ``X`` lots regardless of
        balance. Use for measuring the strategy's raw per-trade edge without
        compounding. Balance still tracked (can go negative for analysis).
    """
    trades: list[Trade] = []
    balance = initial_balance
    n_open = 0
    n_skipped = 0

    # Process in chronological order (signals already sorted by entry_time)
    for sig in signals:
        sl_distance_price = sig.entry_price - sig.sl_price if sig.direction == "long" \
            else sig.sl_price - sig.entry_price
        if fixed_lots is not None:
            lots = fixed_lots
        else:
            lots = lots_for_risk(
                account_balance=balance, risk_pct=risk_pct,
                sl_distance_price=sl_distance_price, contract_size=contract_size,
                min_lot_step=min_lot_step,
            )
        if lots == 0:
            n_skipped += 1
            continue

        if sig.direction == "long":
            sim_out = _simulate_long(sig, bars, be_trigger_distance)
        else:
            sim_out = _simulate_short(sig, bars, be_trigger_distance)

        if sim_out is None:
            n_open += 1
            continue

        exit_time, exit_price, exit_reason, holding = sim_out
        if sig.direction == "long":
            gross = (exit_price - sig.entry_price) * contract_size * lots
        else:
            gross = (sig.entry_price - exit_price) * contract_size * lots
        cost = cost_per_roundtrip_usd_per_lot * lots
        net = gross - cost
        balance_before = balance
        balance += net

        trades.append(Trade(
            signal=sig, lots=lots,
            entry_time=sig.entry_time, entry_price=sig.entry_price,
            exit_time=exit_time, exit_price=exit_price, exit_reason=exit_reason,
            gross_pnl=gross, cost=cost, net_pnl=net,
            holding_bars=holding,
            balance_before=balance_before, balance_after=balance,
        ))

    # Aggregate
    n_tp = sum(1 for t in trades if t.exit_reason == "tp")
    n_sl = sum(1 for t in trades if t.exit_reason == "sl")
    n_be = sum(1 for t in trades if t.exit_reason == "be")
    total_gross = sum(t.gross_pnl for t in trades)
    total_cost = sum(t.cost for t in trades)
    total_net = sum(t.net_pnl for t in trades)
    n_closed = len(trades)
    win_rate = sum(1 for t in trades if t.net_pnl > 0) / n_closed if n_closed else 0.0
    avg_net = total_net / n_closed if n_closed else 0.0

    # Equity-curve drawdown
    if trades:
        eq = [initial_balance]
        for t in trades:
            eq.append(t.balance_after)
        running_max = eq[0]
        max_dd = 0.0
        for v in eq:
            running_max = max(running_max, v)
            dd = v - running_max
            if dd < max_dd:
                max_dd = dd
    else:
        max_dd = 0.0

    # Ideal R:R from signal spec; realised R:R from actual P&Ls
    if signals:
        ref = signals[0]
        ideal_rr = (ref.tp_price - ref.entry_price) / (ref.entry_price - ref.sl_price) \
            if ref.direction == "long" else \
            (ref.entry_price - ref.tp_price) / (ref.sl_price - ref.entry_price)
    else:
        ideal_rr = 0.0

    winners = [t.net_pnl for t in trades if t.net_pnl > 0]
    losers_abs = [abs(t.net_pnl) for t in trades if t.net_pnl < 0]
    if winners and losers_abs:
        realised_rr = (sum(winners) / len(winners)) / (sum(losers_abs) / len(losers_abs))
    else:
        realised_rr = 0.0

    # Annualised Sharpe of trade-level net P&L stream.
    # We use trade frequency (trades/year) as the annualisation factor's
    # square-root — i.e. a strategy's per-trade Sharpe scales by sqrt(N/yr)
    # when extrapolated to annual returns.
    if n_closed >= 2:
        import statistics
        pnls = [t.net_pnl for t in trades]
        mean_pnl = statistics.mean(pnls)
        std_pnl = statistics.stdev(pnls)  # sample std (n-1)
        per_trade_sharpe = mean_pnl / std_pnl if std_pnl > 0 else 0.0
        days_span = (trades[-1].exit_time - trades[0].entry_time).total_seconds() / 86400
        years_span = max(days_span / 365.25, 1e-9)
        trades_per_year = n_closed / years_span
        sharpe_annual = per_trade_sharpe * (trades_per_year ** 0.5)
    else:
        sharpe_annual = 0.0
        trades_per_year = 0.0

    return BacktestReport(
        trades=trades,
        n_signals_evaluated=len(signals),
        n_trades_closed=n_closed,
        n_trades_still_open=n_open,
        n_skipped_undersized=n_skipped,
        n_tp=n_tp, n_sl=n_sl, n_be=n_be,
        total_gross_pnl=total_gross, total_cost=total_cost, total_net_pnl=total_net,
        win_rate=win_rate, avg_trade_net=avg_net, max_drawdown=max_dd,
        initial_balance=initial_balance, final_balance=balance,
        ideal_rr=ideal_rr, realised_rr=realised_rr,
        sharpe_annual=sharpe_annual, trades_per_year=trades_per_year,
    )

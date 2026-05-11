"""
Bar-level vectorized backtester.

Inputs
------
- ``bars``: DataFrame with at least ``close`` column, datetime-aligned.
- ``position``: Int-typed Series aligned with ``bars`` (-1 short, 0 flat,
  +1 long). The position is interpreted as "held at the close of this
  bar"; bar t's PnL uses ``position[t-1]`` to avoid look-ahead bias.

Costs
-----
``cost_per_roundtrip_usd_per_lot`` is split into two equal legs, charged
on every entry and every exit (i.e. whenever the position changes from 0
to nonzero, or vice versa). Reversals (long -> short with no flat bar)
charge twice (close + reopen) which is correct for hedge-mode accounts.

PnL conventions
---------------
- ``contract_size`` * ``lot`` is the conversion from $/unit price moves
  to $ account moves. For XAUUSD: contract_size=100 oz, lot=0.01 -> 1 oz.
- Annualized Sharpe uses ``annualization_factor`` which the caller passes
  in based on bar frequency and market hours (default tuned for M1 of a
  ~23h/5d market: ~6900 bars/week * 52 = ~358800).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class BacktestResult:
    bars: pd.DataFrame             # bars + position + per-bar PnL + equity
    gross_pnl: float               # $, sum of bar_pnl_gross
    net_pnl: float                 # $, sum of bar_pnl_net (after costs)
    cost_total: float              # $, total transaction cost paid
    n_trades: int                  # number of completed round-trip trades
    n_entries: int                 # number of entry-side position changes
    sharpe_annualized: float       # of bar_pnl_net stream
    max_drawdown: float            # $, most-negative running drawdown
    win_rate: float                # fraction of completed trades with net PnL > 0
    avg_trade_pnl_gross: float
    avg_trade_pnl_net: float

    def summary(self, title: str = "") -> str:
        lines = []
        if title:
            lines.append(f"==== {title} ====")
        lines.append(f"  gross PnL          : ${self.gross_pnl:>+10.2f}")
        lines.append(f"  total cost paid    : ${self.cost_total:>+10.2f}")
        lines.append(f"  net   PnL          : ${self.net_pnl:>+10.2f}")
        lines.append(f"  trades / entries   : {self.n_trades} / {self.n_entries}")
        lines.append(f"  sharpe (ann.)      : {self.sharpe_annualized:>+6.2f}")
        lines.append(f"  max drawdown       : ${self.max_drawdown:>+10.2f}")
        lines.append(f"  win rate           : {self.win_rate*100:>5.1f}%")
        lines.append(f"  avg trade PnL g/n  : ${self.avg_trade_pnl_gross:>+7.4f} / ${self.avg_trade_pnl_net:>+7.4f}")
        return "\n".join(lines)


def run_backtest(
    bars: pd.DataFrame,
    position: pd.Series,
    *,
    lot: float = 0.01,
    contract_size: float = 100.0,
    cost_per_roundtrip_usd_per_lot: float = 0.0,
    annualization_factor: int = 6900 * 52,  # M1, 23h/d * 5d/wk * 52
) -> BacktestResult:
    """Compute PnL series + summary statistics for a position over bars."""
    if not bars.index.equals(position.index):
        # Allow misalignment: assume same length and reindex on bars' index.
        if len(bars) != len(position):
            raise ValueError(f"bars ({len(bars)}) and position ({len(position)}) length mismatch")
        position = pd.Series(position.values, index=bars.index)

    close = bars["close"].astype("float64")
    price_diff = close.diff().fillna(0.0)
    pos_lag = position.shift(1).fillna(0).astype("float64")
    bar_pnl_gross = pos_lag * price_diff * contract_size * lot

    pos_change = position.astype("int8").diff().fillna(0).astype("int8")
    # Each leg = one nonzero change. Hedge-mode reversal (e.g. +1 -> -1)
    # counts as 2 legs (close + reopen), which (pos_change != 0) handles
    # naturally because |pos_change|=2 doesn't matter — we only count
    # whether SOMETHING changed.
    is_leg = (pos_change != 0).astype("int64")
    # Actually for reversals we WANT 2 legs counted; use abs change instead
    # (each unit of |delta| = one leg of fill).
    legs_count = pos_change.abs().astype("int64")
    cost_per_leg = (cost_per_roundtrip_usd_per_lot / 2.0) * lot
    bar_cost = legs_count.astype("float64") * cost_per_leg
    bar_pnl_net = bar_pnl_gross - bar_cost

    equity = bar_pnl_net.cumsum()
    running_max = equity.cummax()
    drawdown = equity - running_max
    max_dd = float(drawdown.min()) if len(drawdown) else 0.0

    # Trade-level stats: group by trade id (each in-position run = one trade)
    in_pos = (position != 0).to_numpy()
    is_entry = in_pos & ~np.concatenate([[False], in_pos[:-1]])
    trade_id = is_entry.cumsum()
    trade_id_series = pd.Series(trade_id, index=position.index)
    trade_id_when_flat = trade_id_series.where(position != 0, 0)

    n_entries = int(is_entry.sum())
    if n_entries > 0:
        trade_pnls_gross = bar_pnl_gross.groupby(trade_id_when_flat).sum()
        trade_pnls_net = bar_pnl_net.groupby(trade_id_when_flat).sum()
        # Drop bucket 0 (flat bars)
        trade_pnls_gross = trade_pnls_gross.drop(0, errors="ignore")
        trade_pnls_net = trade_pnls_net.drop(0, errors="ignore")
        avg_trade_gross = float(trade_pnls_gross.mean())
        avg_trade_net = float(trade_pnls_net.mean())
        win_rate = float((trade_pnls_net > 0).mean())
    else:
        avg_trade_gross = avg_trade_net = win_rate = 0.0

    # Annualized Sharpe of the net bar-PnL stream
    mu = bar_pnl_net.mean()
    sd = bar_pnl_net.std(ddof=0)
    sharpe = float(mu / sd * np.sqrt(annualization_factor)) if sd > 0 else 0.0

    out = bars.copy()
    out["position"] = position
    out["pnl_gross"] = bar_pnl_gross
    out["pnl_net"] = bar_pnl_net
    out["cost"] = bar_cost
    out["equity_net"] = equity

    return BacktestResult(
        bars=out,
        gross_pnl=float(bar_pnl_gross.sum()),
        net_pnl=float(bar_pnl_net.sum()),
        cost_total=float(bar_cost.sum()),
        n_trades=n_entries,  # one round-trip per entry
        n_entries=n_entries,
        sharpe_annualized=sharpe,
        max_drawdown=max_dd,
        win_rate=win_rate,
        avg_trade_pnl_gross=avg_trade_gross,
        avg_trade_pnl_net=avg_trade_net,
    )

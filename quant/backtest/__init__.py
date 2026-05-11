"""Backtest engine and result containers.

Current scope: bar-level vectorized PnL with per-trade-leg cost deduction.
Walk-forward / OOS harnessing to be added in subsequent iterations.
"""

from quant.backtest.engine import BacktestResult, run_backtest

__all__ = ["BacktestResult", "run_backtest"]

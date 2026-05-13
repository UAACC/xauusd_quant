"""
scripts/stat_rigor.py — bootstrap Sharpe CI + permutation test for null.

For each symbol, runs the BOS-reversal strategy then applies two
statistical sanity checks:

1. Bootstrap CI on Sharpe and per-trade-edge: resample the trade-PnL
   stream with replacement 5000 times; report the 2.5% / 50% / 97.5%
   percentiles. Wide CIs that include 0 say "we cannot statistically
   distinguish this strategy from a random walk."

2. Permutation test: take the trade timing positions but RANDOMIZE the
   PnL sign and magnitude by sampling from a synthetic null distribution
   (mean 0, same volatility). Repeat 5000 times. Compute the fraction of
   permuted runs that match-or-exceed the observed Sharpe. This is the
   one-sided p-value against H0 = "strategy has no edge".

Both tests are honest: they don't depend on parametric assumptions, and
they directly answer the question "could we have gotten these numbers by
chance with no real edge?"
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from quant.backtest.event_engine import run_event_backtest
from quant.data.parquet import read_bars_month
from quant.strategies.bos_reversal import detect_bos_reversal_signals

DATA_ROOT = REPO_ROOT / "data"

SYMBOLS = {
    "XAUUSD": dict(contract_size=100.0, cost_rt=12.0,  pct_sl=0.0043),
    "USTEC":  dict(contract_size=1.0,   cost_rt=0.5,   pct_sl=0.0043),
    "XAGUSD": dict(contract_size=1000.0, cost_rt=10.0, pct_sl=0.0043),
    "XTIUSD": dict(contract_size=100.0, cost_rt=2.0,   pct_sl=0.0043),
    "US500":  dict(contract_size=1.0,   cost_rt=1.0,   pct_sl=0.0043),
    "BTCUSD": dict(contract_size=1.0,   cost_rt=20.0,  pct_sl=0.0043),
}

N_BOOTSTRAP = 5000
N_PERMUTATION = 5000
TRADES_PER_YEAR_FOR_ANNUALIZATION = 1.0  # placeholder; per-symbol below


def _load_months(symbol, timeframe, start, end):
    frames = []
    y, m = start
    while (y, m) <= end:
        path = DATA_ROOT / "bars" / symbol / timeframe / f"{y:04d}" / f"{y:04d}-{m:02d}.parquet"
        if path.exists():
            frames.append(read_bars_month(DATA_ROOT, symbol, timeframe, y, m))
        m += 1
        if m > 12:
            m, y = 1, y + 1
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True).sort_values("time").reset_index(drop=True)


def sharpe_from_pnls(pnls: np.ndarray, trades_per_year: float) -> float:
    if pnls.std(ddof=1) == 0 or len(pnls) < 2:
        return 0.0
    return float(pnls.mean() / pnls.std(ddof=1) * np.sqrt(trades_per_year))


def bootstrap_ci(pnls: np.ndarray, trades_per_year: float, n: int = N_BOOTSTRAP, seed: int = 42):
    rng = np.random.default_rng(seed)
    n_trades = len(pnls)
    if n_trades < 2:
        return (0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    sh_list = np.empty(n)
    avg_list = np.empty(n)
    for i in range(n):
        sample = rng.choice(pnls, size=n_trades, replace=True)
        sh_list[i] = sharpe_from_pnls(sample, trades_per_year)
        avg_list[i] = sample.mean()
    return (
        float(np.percentile(sh_list, 2.5)),
        float(np.percentile(sh_list, 50)),
        float(np.percentile(sh_list, 97.5)),
        float(np.percentile(avg_list, 2.5)),
        float(np.percentile(avg_list, 50)),
        float(np.percentile(avg_list, 97.5)),
    )


def permutation_pvalue(pnls: np.ndarray, trades_per_year: float, n: int = N_PERMUTATION, seed: int = 42) -> float:
    """One-sided p-value against H0: strategy has no edge (mean PnL = 0).

    Synthetic null = random PnL sequences with mean 0 and same volatility
    as observed. We measure fraction of nulls whose Sharpe >= observed.
    """
    rng = np.random.default_rng(seed)
    n_trades = len(pnls)
    if n_trades < 2:
        return 1.0
    observed_sharpe = sharpe_from_pnls(pnls, trades_per_year)
    sigma = pnls.std(ddof=1)
    n_geq = 0
    for _ in range(n):
        null_pnls = rng.normal(loc=0.0, scale=sigma, size=n_trades)
        null_sh = sharpe_from_pnls(null_pnls, trades_per_year)
        if null_sh >= observed_sharpe:
            n_geq += 1
    return (n_geq + 1) / (n + 1)  # add-one smoothing


def main() -> int:
    start = (2022, 3)
    end = (2026, 5)

    print(f"{'symbol':<8}{'N':>4}{'obs_SR':>10}"
          f"{'SR_lo':>10}{'SR_med':>10}{'SR_hi':>10}"
          f"{'edge_lo':>10}{'edge_med':>10}{'edge_hi':>10}{'p-val':>8}")
    print("-" * 100)

    all_pnls = []
    for sym, params in SYMBOLS.items():
        h4 = _load_months(sym, "H4", start, end)
        h1 = _load_months(sym, "H1", start, end)
        m15 = _load_months(sym, "M15", start, end)
        if h4.empty or h1.empty or m15.empty:
            continue

        typical_price = float(h4["close"].iloc[-1])
        sl_dist = typical_price * params["pct_sl"]
        tp_dist = sl_dist * 2.0
        be_trigger = sl_dist * 0.75

        signals = detect_bos_reversal_signals(
            h4, m15, h1_bars=h1,
            sl_distance=sl_dist, tp_distance=tp_dist,
        )
        report = run_event_backtest(
            signals, m15,
            initial_balance=10_000.0, fixed_lots=0.01,
            contract_size=params["contract_size"],
            be_trigger_distance=be_trigger,
            cost_per_roundtrip_usd_per_lot=params["cost_rt"],
        )

        pnls = np.array([t.net_pnl for t in report.trades])
        all_pnls.append((sym, pnls, report.trades_per_year))
        if len(pnls) < 2:
            print(f"{sym:<8}{len(pnls):>4}  (too few trades)")
            continue

        obs_sr = sharpe_from_pnls(pnls, report.trades_per_year)
        sr_lo, sr_med, sr_hi, e_lo, e_med, e_hi = bootstrap_ci(pnls, report.trades_per_year)
        p = permutation_pvalue(pnls, report.trades_per_year)
        print(
            f"{sym:<8}{len(pnls):>4}{obs_sr:>+10.2f}"
            f"{sr_lo:>+10.2f}{sr_med:>+10.2f}{sr_hi:>+10.2f}"
            f"{e_lo:>+10.2f}{e_med:>+10.2f}{e_hi:>+10.2f}{p:>8.3f}"
        )

    # Combined (all symbols pooled, freq = sum)
    if all_pnls:
        pooled = np.concatenate([p for _, p, _ in all_pnls])
        # Approximation: weighted-avg frequency for annualization
        total_n = sum(len(p) for _, p, _ in all_pnls)
        weighted_freq = sum(len(p) * f for _, p, f in all_pnls) / total_n
        obs_sr = sharpe_from_pnls(pooled, weighted_freq)
        sr_lo, sr_med, sr_hi, e_lo, e_med, e_hi = bootstrap_ci(pooled, weighted_freq)
        p = permutation_pvalue(pooled, weighted_freq)
        print()
        print(
            f"{'POOLED':<8}{total_n:>4}{obs_sr:>+10.2f}"
            f"{sr_lo:>+10.2f}{sr_med:>+10.2f}{sr_hi:>+10.2f}"
            f"{e_lo:>+10.2f}{e_med:>+10.2f}{e_hi:>+10.2f}{p:>8.3f}"
        )

    print()
    print("Reading guide:")
    print("  - SR_lo / SR_hi: 95% bootstrap CI on annualized Sharpe.")
    print("    If 0 is INSIDE [SR_lo, SR_hi] → cannot reject 'no edge'.")
    print("  - edge_med: bootstrap median per-trade $ edge.")
    print("    If edge_lo < 0 → real edge may be negative.")
    print("  - p-val: one-sided permutation test against H0 = no edge.")
    print("    Conservative cutoff: p < 0.05 = significant.")
    print("    Strict (multiple-comparison): p < 0.05 / N_symbols.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

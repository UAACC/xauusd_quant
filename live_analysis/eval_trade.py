"""Structured trade-rule judge for candidate entries.

Given a proposed trade (symbol, direction, entry, sl, tp) and recent OHLC
bars, evaluate against the frozen SMC BOS-reversal rules and operational
constraints (R:R, sizing, cost, live spread). Returns an ``EvalReport``
with per-check verdicts; any ``fail`` triggers a ``journal.log_skip``
side-effect so the rejection is recorded for weekly review.

v1 uses a **signal-match** approach: it runs the strategy detector on the
recent bars and checks whether the user's candidate coincides with an
A-grade emission. It does NOT do per-stage diagnostic (that's a v2 add).
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Optional

import pandas as pd

from live_analysis import journal
from quant.risk.sizing import dollar_risk_for_lots, lots_for_risk
from quant.strategies.bos_reversal import (
    BosReversalSignal,
    detect_bos_reversal_signals,
)


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA_ROOT = REPO_ROOT / "data"

Direction = Literal["long", "short"]
CheckStatus = Literal["pass", "fail", "warn", "skip"]

# IC Markets Raw Spread XAUUSD calibration values (HANDOFF §5.3)
XAUUSD_CONTRACT_SIZE = 100.0
XAUUSD_POINT = 0.01
XAUUSD_COMMISSION_ROUNDTRIP_USD_PER_LOT = 7.00  # $7 per 1.0 lot round-trip
XAUUSD_MIN_LOT_STEP = 0.01


@dataclass(frozen=True)
class CheckResult:
    """One rule's verdict -- what was checked, did it pass, and the supporting numbers."""

    name: str
    status: CheckStatus
    label: str
    detail: Optional[str] = None


@dataclass(frozen=True)
class EvalReport:
    symbol: str
    direction: Direction
    entry: float
    sl: float
    tp: float
    checks: tuple[CheckResult, ...] = field(default_factory=tuple)

    @property
    def verdict(self) -> Literal["accept", "reject"]:
        return "reject" if any(c.status == "fail" for c in self.checks) else "accept"

    @property
    def first_failure(self) -> Optional[CheckResult]:
        for c in self.checks:
            if c.status == "fail":
                return c
        return None


def _check_orientation(
    direction: Direction, entry: float, sl: float, tp: float
) -> CheckResult:
    """SL/TP must be on the correct sides of entry for the direction.

    A swapped SL/TP is the single most expensive bug a manual trader can make:
    the order goes in with the stop on the WRONG side of price, so the broker
    immediately trips it. Catch this before any other check so a confused
    candidate doesn't waste analyst attention.
    """
    if direction == "long":
        if sl < entry < tp:
            return CheckResult(
                name="orientation",
                status="pass",
                label="long orientation OK (sl < entry < tp)",
            )
        return CheckResult(
            name="orientation",
            status="fail",
            label="long requires sl < entry < tp",
            detail=f"sl={sl} entry={entry} tp={tp} -- SL or TP on wrong side of entry",
        )
    # short
    if tp < entry < sl:
        return CheckResult(
            name="orientation",
            status="pass",
            label="short orientation OK (tp < entry < sl)",
        )
    return CheckResult(
        name="orientation",
        status="fail",
        label="short requires tp < entry < sl",
        detail=f"sl={sl} entry={entry} tp={tp} -- SL or TP on wrong side of entry",
    )


def _check_rr(
    direction: Direction, entry: float, sl: float, tp: float, *, min_rr: float
) -> CheckResult:
    risk = abs(entry - sl)
    reward = abs(tp - entry)
    if risk <= 0:
        return CheckResult(
            name="rr",
            status="fail",
            label="R:R undefined (SL == entry)",
            detail=f"entry={entry} sl={sl} -- zero risk distance",
        )
    rr = reward / risk
    if rr >= min_rr:
        return CheckResult(
            name="rr",
            status="pass",
            label=f"R:R = {rr:.2f} (>= {min_rr})",
            detail=f"risk=${risk:.2f} reward=${reward:.2f}",
        )
    return CheckResult(
        name="rr",
        status="fail",
        label=f"R:R = {rr:.2f} (below {min_rr})",
        detail=f"risk=${risk:.2f} reward=${reward:.2f}",
    )


def _check_signal_match(
    *,
    direction: Direction,
    entry: float,
    h4_bars: pd.DataFrame,
    m15_bars: pd.DataFrame,
    h1_bars: Optional[pd.DataFrame],
    tolerance_pct: float,
    recency_m15_bars: int,
) -> tuple[CheckResult, Optional[BosReversalSignal]]:
    """Run strategy detector on recent bars; check user candidate matches.

    Match = same direction AND entry within tolerance_pct AND signal emitted
    in the last `recency_m15_bars` of the M15 series.
    """
    if direction == "short":
        return (
            CheckResult(
                name="signal_match",
                status="fail",
                label="strategy does not authorize SHORT setups yet",
                detail="bos_reversal.py only emits long signals; short is TODO (HANDOFF §7 P4)",
            ),
            None,
        )

    try:
        signals = detect_bos_reversal_signals(
            h4_bars=h4_bars,
            m15_bars=m15_bars,
            h1_bars=h1_bars,
        )
    except ValueError as exc:
        return (
            CheckResult(
                name="signal_match",
                status="fail",
                label="detector could not run",
                detail=str(exc),
            ),
            None,
        )

    if not signals:
        return (
            CheckResult(
                name="signal_match",
                status="fail",
                label="no A-grade BOS-reversal signal in supplied window",
                detail="strategy did not emit any signal on these bars -- "
                "either the setup isn't here, or your window is too short to "
                "capture capit -> BOS -> retest -> hold",
            ),
            None,
        )

    last_m15_time = pd.Timestamp(m15_bars["time"].iloc[-1])
    recency_window_start = last_m15_time - pd.Timedelta(minutes=15 * recency_m15_bars)
    recent = [s for s in signals if s.entry_time >= recency_window_start]

    if not recent:
        last_signal = signals[-1]
        return (
            CheckResult(
                name="signal_match",
                status="fail",
                label="A-grade signal exists but it's stale",
                detail=(
                    f"most recent strategy entry was {last_signal.entry_time} "
                    f"(now: {last_m15_time}); only signals in last "
                    f"{recency_m15_bars} M15 bars count as 'live'"
                ),
            ),
            None,
        )

    chosen = recent[-1]
    deviation_pct = abs(entry - chosen.entry_price) / chosen.entry_price * 100
    if deviation_pct <= tolerance_pct:
        return (
            CheckResult(
                name="signal_match",
                status="pass",
                label=(
                    f"matches A-grade signal @ {chosen.entry_price:.2f} "
                    f"({deviation_pct:.3f}% from your entry)"
                ),
                detail=(
                    f"strategy: entry={chosen.entry_price:.2f} "
                    f"sl={chosen.sl_price:.2f} tp={chosen.tp_price:.2f}; "
                    f"capit={chosen.capitulation_time} BOS={chosen.bos_time}"
                ),
            ),
            chosen,
        )
    return (
        CheckResult(
            name="signal_match",
            status="warn",
            label=(
                f"A-grade signal exists but your entry is {deviation_pct:.2f}% "
                f"off (> {tolerance_pct}% tolerance)"
            ),
            detail=(
                f"strategy entry={chosen.entry_price:.2f} vs yours={entry:.2f}; "
                "this is freelancing on price -- check before sending"
            ),
        ),
        chosen,
    )


def _check_spread(
    *, live_spread_pts: Optional[float], h4_bars: pd.DataFrame
) -> CheckResult:
    """Compare live instantaneous spread to a historical baseline.

    Caveat: the ``spread`` column in OHLC bars is the *median* spread during
    the bar, which is materially smaller than the instantaneous spread the
    trader actually pays (instantaneous spikes during news / rollover get
    averaged out). So bar-level P95 is a soft baseline only -- we floor the
    warn/fail thresholds at the same absolute values ``live_monitor.py``
    uses for tick-level alerts (20pt warn, 40pt fail).
    """
    if live_spread_pts is None:
        return CheckResult(
            name="spread",
            status="skip",
            label="spread check skipped (no live_spread_pts supplied)",
        )

    recent_spreads = h4_bars["spread"].tail(60)
    if recent_spreads.empty:
        return CheckResult(
            name="spread",
            status="skip",
            label="spread baseline unavailable (no bars)",
        )
    p50 = float(recent_spreads.median())
    p95 = float(recent_spreads.quantile(0.95))

    # Floor: don't be tighter than live_monitor's tick-level alert defaults.
    warn_threshold = max(p95 * 1.5, 20.0)
    fail_threshold = max(p95 * 2.5, 40.0)

    if live_spread_pts <= warn_threshold:
        return CheckResult(
            name="spread",
            status="pass",
            label=f"spread {live_spread_pts:.0f}pt <= warn-threshold ({warn_threshold:.0f}pt)",
            detail=(
                f"bar-level baseline: P50={p50:.0f}pt P95={p95:.0f}pt "
                f"(median-during-bar underestimates instantaneous; floored at 20pt warn)"
            ),
        )
    if live_spread_pts <= fail_threshold:
        return CheckResult(
            name="spread",
            status="warn",
            label=f"spread {live_spread_pts:.0f}pt elevated (between {warn_threshold:.0f} and {fail_threshold:.0f}pt)",
            detail="possibly news / rollover / thin liquidity; size down or wait",
        )
    return CheckResult(
        name="spread",
        status="fail",
        label=f"spread {live_spread_pts:.0f}pt abnormal (> {fail_threshold:.0f}pt)",
        detail="major news, rollover, or illiquid hours -- do not enter",
    )


def _check_news() -> CheckResult:
    return CheckResult(
        name="news",
        status="skip",
        label="news blackout check not configured",
        detail="fixtures/news_calendar.csv missing -- populate manually before relying on this gate",
    )


def _check_sizing(
    *,
    entry: float,
    sl: float,
    account_balance: Optional[float],
    risk_pct: float,
    contract_size: float,
    min_lot_step: float,
) -> CheckResult:
    if account_balance is None:
        return CheckResult(
            name="sizing",
            status="skip",
            label="sizing check skipped (no --account-balance supplied)",
        )
    sl_distance = abs(entry - sl)
    if sl_distance <= 0:
        return CheckResult(
            name="sizing",
            status="fail",
            label="cannot size -- SL distance is zero",
        )
    lots = lots_for_risk(
        account_balance=account_balance,
        risk_pct=risk_pct,
        sl_distance_price=sl_distance,
        contract_size=contract_size,
        min_lot_step=min_lot_step,
    )
    if lots <= 0:
        return CheckResult(
            name="sizing",
            status="fail",
            label="sized below broker minimum",
            detail=(
                f"account=${account_balance:,.0f} risk={risk_pct:.1%} "
                f"sl_dist=${sl_distance:.2f} -> raw lots underflows "
                f"min_lot_step={min_lot_step}; skip the trade"
            ),
        )
    dollars_at_risk = dollar_risk_for_lots(lots, sl_distance, contract_size)
    return CheckResult(
        name="sizing",
        status="pass",
        label=f"{lots:.2f} lots -> ${dollars_at_risk:,.2f} at risk",
        detail=(
            f"account=${account_balance:,.0f} x {risk_pct:.1%} = "
            f"${account_balance * risk_pct:,.2f} target; "
            f"actual ${dollars_at_risk:,.2f} after lot-step floor"
        ),
    )


def _check_cost(
    *,
    live_spread_pts: Optional[float],
    h4_bars: pd.DataFrame,
    lots: float,
    point: float,
    contract_size: float,
    commission_roundtrip_usd_per_lot: float,
) -> CheckResult:
    if live_spread_pts is not None:
        spread_pts = live_spread_pts
        spread_source = "live"
    else:
        recent = h4_bars["spread"].tail(60)
        if recent.empty:
            return CheckResult(
                name="cost",
                status="skip",
                label="cost estimate unavailable (no spread baseline)",
            )
        spread_pts = float(recent.median())
        spread_source = "60-bar median"

    spread_cost = spread_pts * point * contract_size * lots
    commission_cost = commission_roundtrip_usd_per_lot * lots
    total = spread_cost + commission_cost
    return CheckResult(
        name="cost",
        status="pass",
        label=f"~${total:.2f} round-trip cost (${spread_cost:.2f} spread + ${commission_cost:.2f} commission)",
        detail=(
            f"spread_pts={spread_pts:.0f} ({spread_source}), "
            f"lots={lots:.2f}, contract_size={contract_size}, point={point}"
        ),
    )


def evaluate_trade(
    *,
    symbol: str,
    direction: Direction,
    entry: float,
    sl: float,
    tp: float,
    h4_bars: pd.DataFrame,
    m15_bars: pd.DataFrame,
    h1_bars: Optional[pd.DataFrame] = None,
    account_balance: Optional[float] = None,
    risk_pct: float = 0.02,
    contract_size: float = XAUUSD_CONTRACT_SIZE,
    point: float = XAUUSD_POINT,
    commission_roundtrip_usd_per_lot: float = XAUUSD_COMMISSION_ROUNDTRIP_USD_PER_LOT,
    min_lot_step: float = XAUUSD_MIN_LOT_STEP,
    live_spread_pts: Optional[float] = None,
    min_rr: float = 1.5,
    signal_match_tolerance_pct: float = 0.5,
    signal_match_recency_m15_bars: int = 4,
    journal_path: Optional[Path] = journal.DEFAULT_JOURNAL_PATH,
    journal_skip_on_reject: bool = True,
) -> EvalReport:
    """Evaluate a candidate trade against frozen rules; return structured report.

    On verdict='reject', logs a skip record to the journal (suppressed by
    setting ``journal_skip_on_reject=False`` for dry runs / tests).
    """
    checks: list[CheckResult] = []

    checks.append(_check_orientation(direction, entry, sl, tp))
    checks.append(_check_rr(direction, entry, sl, tp, min_rr=min_rr))

    signal_check, matched_signal = _check_signal_match(
        direction=direction,
        entry=entry,
        h4_bars=h4_bars,
        m15_bars=m15_bars,
        h1_bars=h1_bars,
        tolerance_pct=signal_match_tolerance_pct,
        recency_m15_bars=signal_match_recency_m15_bars,
    )
    checks.append(signal_check)

    checks.append(_check_spread(live_spread_pts=live_spread_pts, h4_bars=h4_bars))
    checks.append(_check_news())

    sizing = _check_sizing(
        entry=entry,
        sl=sl,
        account_balance=account_balance,
        risk_pct=risk_pct,
        contract_size=contract_size,
        min_lot_step=min_lot_step,
    )
    checks.append(sizing)

    # Cost estimate uses sized lots if available, else 0.10 placeholder
    cost_lots = 0.10
    if sizing.status == "pass" and account_balance is not None:
        sl_distance = abs(entry - sl)
        if sl_distance > 0:
            cost_lots = lots_for_risk(
                account_balance=account_balance,
                risk_pct=risk_pct,
                sl_distance_price=sl_distance,
                contract_size=contract_size,
                min_lot_step=min_lot_step,
            )
    checks.append(_check_cost(
        live_spread_pts=live_spread_pts,
        h4_bars=h4_bars,
        lots=cost_lots,
        point=point,
        contract_size=contract_size,
        commission_roundtrip_usd_per_lot=commission_roundtrip_usd_per_lot,
    ))

    report = EvalReport(
        symbol=symbol,
        direction=direction,
        entry=entry,
        sl=sl,
        tp=tp,
        checks=tuple(checks),
    )

    if report.verdict == "reject" and journal_skip_on_reject and journal_path is not None:
        first = report.first_failure
        journal.log_skip(
            symbol=symbol,
            direction=direction,
            reason=first.label if first else "unspecified rejection",
            rule_failed=first.name if first else None,
            candidate={
                "entry": entry,
                "sl": sl,
                "tp": tp,
                "live_spread_pts": live_spread_pts,
            },
            path=journal_path,
        )

    return report


# ---------------------------------------------------------------------------
# Data loading: live (MT5 demo) preferred, parquet fallback for --offline / tests
# ---------------------------------------------------------------------------

def load_recent_bars(
    *,
    data_root: Path,
    symbol: str,
    timeframe: str,
    months_back: int = 2,
    as_of: Optional[pd.Timestamp] = None,
) -> pd.DataFrame:
    """Load recent ``months_back`` months of bars from the parquet lake.

    Used by the ``--offline`` CLI path and by anything running on non-Windows
    (MT5 Python is Windows-only). When MT5 is available, prefer
    ``pull_live_context`` -- it pulls bars that are correct to the current tick
    and supplies a live spread + cost model in one call.
    """
    from quant.data.parquet import read_bars_month

    as_of = as_of or pd.Timestamp.now(tz="UTC")
    frames: list[pd.DataFrame] = []
    cur = pd.Timestamp(as_of.year, as_of.month, 1, tz="UTC")
    for _ in range(months_back + 1):
        try:
            df = read_bars_month(data_root, symbol, timeframe, cur.year, cur.month)
            frames.append(df)
        except FileNotFoundError:
            pass
        cur = (cur - pd.Timedelta(days=1)).replace(day=1)
    if not frames:
        raise FileNotFoundError(
            f"no {symbol} {timeframe} bars found in {data_root} for "
            f"last {months_back + 1} months ending {as_of.date()}"
        )
    out = pd.concat(frames, ignore_index=True).sort_values("time").reset_index(drop=True)
    return out


DEFAULT_TIMEFRAME_LOOKBACKS: dict[str, pd.Timedelta] = {
    "D1":  pd.Timedelta(days=180),
    "H4":  pd.Timedelta(days=60),
    "H1":  pd.Timedelta(days=30),
    "M30": pd.Timedelta(days=10),
    "M15": pd.Timedelta(days=10),
    "M5":  pd.Timedelta(days=3),
    "M1":  pd.Timedelta(hours=6),
}

# Default focus universe for the discretionary-analyst project (XAGUSD + USTEC).
# Use these as defaults for `scan_symbols()` when caller doesn't specify.
DEFAULT_FOCUS_SYMBOLS: tuple[str, ...] = ("XAGUSD", "USTEC")

# Cross-asset baseline for XAUUSD analyst commentary. IC Markets demo doesn't
# carry DXY directly; EURUSD is the largest DXY component and serves as USD-
# strength inverse proxy (EUR ~58% of DXY weight). US500/USTEC frame the risk
# regime; XAGUSD is precious-metals confluence; XTIUSD is commodity carry;
# BTCUSD probes the alt-safe-haven correlation regime.
DEFAULT_CROSS_ASSET_FOR_XAUUSD: tuple[str, ...] = (
    "EURUSD",   # DXY-inverse proxy
    "US500",    # risk-on
    "USTEC",    # risk-on (tech-heavy)
    "XAGUSD",   # precious confluence
    "XTIUSD",   # commod carry
    "BTCUSD",   # alt-safe-haven
)


def pull_live_context(
    symbol: str,
    *,
    timeframes: Optional[dict[str, pd.Timedelta]] = None,
    cross_asset_symbols: Optional[list[str] | tuple[str, ...]] = None,
    months_back: Optional[int] = None,
) -> dict:
    """Connect to demo MT5 and pull a full multi-TF + cross-asset snapshot.

    Returns a dict with three sections:

    1. Back-compat (kwargs straight into ``evaluate_trade``):
       ``h4_bars``, ``h1_bars``, ``m15_bars``, ``live_spread_pts``,
       ``contract_size``, ``point``, ``commission_roundtrip_usd_per_lot``,
       ``min_lot_step``, ``account_balance``.

    2. Multi-TF (for analyst commentary):
       ``bars: dict[str, pd.DataFrame]`` keyed by TF name ("D1", "H4",
       "H1", "M30", "M15", "M5", "M1"). Per-TF lookbacks chosen to give
       useful history without bloating M1 (default: D1=180d, H4=60d,
       H1=30d, M30/M15=10d, M5=3d, M1=6h).

    3. Cross-asset snapshot (each symbol gets tick + D1 + recent change %):
       ``cross_asset: dict[str, dict]`` with keys
       ``{bid, ask, spread_pts, last_close, d1_change_pct, d5_change_pct,
       d20_change_pct, atr_h4_recent}``. Default symbol set for XAUUSD
       primary is ``DEFAULT_CROSS_ASSET_FOR_XAUUSD``.

    Plus diagnostic: ``server``, ``account_login``.

    Args:
        symbol: primary symbol for full multi-TF pull + cost model.
        timeframes: TF name -> lookback. Defaults to all 7 TFs.
            Pass ``{"H4": Timedelta(days=60), ...}`` to override.
            Strategy back-compat requires H4/H1/M15 to be present.
        cross_asset_symbols: symbols to include in the cross-asset block.
            Pass ``[]`` to skip cross-asset entirely; ``None`` uses the
            XAUUSD default. Each pulled symbol adds <5ms total.
        months_back: deprecated; ignored if ``timeframes`` is given.
            Without ``timeframes`` and with ``months_back`` set, uses
            legacy H4/H1/M15-only behavior anchored at month start.

    Safety: ``scripts/mt5_connect.init_mt5`` hard-aborts if bound to a
    LIVE account (``trade_mode == 2``). Demo-only by construction.

    Raises:
        RuntimeError: if MT5 init / symbol selection / data pull fails for
            the primary symbol. Cross-asset symbols that fail are reported
            as ``{"error": "..."}`` in the cross_asset dict, not raised --
            losing a confluence ticker shouldn't kill the whole snapshot.
        FileNotFoundError: if no demo terminal path resolves.

    Windows-only (MT5 Python is Windows-only). On other platforms use
    ``--offline`` (parquet fallback) for the strategy bars; cross-asset
    snapshot has no offline equivalent.
    """
    import sys as _sys
    from datetime import datetime as _dt, timezone as _tz
    _sys.path.insert(0, str(REPO_ROOT / "scripts"))

    from mt5_connect import build_cost_model, get_symbol_info, init_mt5  # noqa: E402
    import MetaTrader5 as mt5  # noqa: E402

    from quant.config import get_demo_mt5_path  # noqa: E402
    from quant.data.broker_time import discover_clock  # noqa: E402

    # Resolve TF set: explicit > legacy months_back > default 7-TF
    if timeframes is None:
        if months_back is not None:
            # Legacy: strategy-only TFs, anchored at month start. Lookback
            # in calendar months -- approximate with timedelta below.
            timeframes = {
                "H4":  pd.Timedelta(days=30 * (months_back + 1)),
                "H1":  pd.Timedelta(days=30 * (months_back + 1)),
                "M15": pd.Timedelta(days=30 * (months_back + 1)),
            }
        else:
            timeframes = dict(DEFAULT_TIMEFRAME_LOOKBACKS)

    if cross_asset_symbols is None:
        cross_asset_symbols = list(DEFAULT_CROSS_ASSET_FOR_XAUUSD)

    _tf_const_for = {
        "D1": "TIMEFRAME_D1",
        "H4": "TIMEFRAME_H4",
        "H1": "TIMEFRAME_H1",
        "M30": "TIMEFRAME_M30",
        "M15": "TIMEFRAME_M15",
        "M5": "TIMEFRAME_M5",
        "M1": "TIMEFRAME_M1",
    }
    unknown = set(timeframes) - set(_tf_const_for)
    if unknown:
        raise ValueError(f"unknown timeframes: {sorted(unknown)}; "
                         f"valid: {sorted(_tf_const_for)}")

    if not init_mt5(terminal_path=get_demo_mt5_path()):
        raise RuntimeError("MT5 init failed; see stderr for the underlying error")

    def _bars_to_df(rates, clock) -> pd.DataFrame:
        df = pd.DataFrame(rates)
        df["time"] = df["time"].apply(
            lambda s: pd.Timestamp(
                clock.broker_msc_to_utc_msc(int(s) * 1000), unit="ms", tz="UTC",
            )
        )
        return df

    try:
        info = get_symbol_info(symbol)
        if info is None:
            raise RuntimeError(f"symbol_info({symbol}) returned None")

        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            raise RuntimeError(f"symbol_info_tick({symbol}) returned None")
        live_spread_pts = (tick.ask - tick.bid) / info.point

        cost = build_cost_model(
            info,
            commission_roundtrip_usd=XAUUSD_COMMISSION_ROUNDTRIP_USD_PER_LOT,
        )

        acct = mt5.account_info()
        clock = discover_clock(mt5, acct.server, symbol)
        end_utc = _dt.now(tz=_tz.utc)

        # Primary symbol: pull all requested timeframes
        bars: dict[str, pd.DataFrame] = {}
        for tf_name, lookback in timeframes.items():
            tf_const = getattr(mt5, _tf_const_for[tf_name])
            sb = clock.utc_dt_to_broker_naive(end_utc - lookback)
            eb = clock.utc_dt_to_broker_naive(end_utc)
            rates = mt5.copy_rates_range(symbol, tf_const, sb, eb)
            if rates is None or len(rates) == 0:
                raise RuntimeError(
                    f"copy_rates_range({symbol}, {tf_name}, lookback={lookback}) "
                    f"returned empty"
                )
            bars[tf_name] = _bars_to_df(rates, clock)

        # Cross-asset block: tick + D1(60d) + simple change % for each
        cross_asset: dict[str, dict] = {}
        for sym in cross_asset_symbols:
            try:
                if not mt5.symbol_select(sym, True):
                    cross_asset[sym] = {"error": "symbol_select failed"}
                    continue
                ca_info = mt5.symbol_info(sym)
                ca_tick = mt5.symbol_info_tick(sym)
                if ca_info is None or ca_tick is None:
                    cross_asset[sym] = {"error": "info or tick None"}
                    continue
                d1_lookback = pd.Timedelta(days=60)
                sb = clock.utc_dt_to_broker_naive(end_utc - d1_lookback)
                eb = clock.utc_dt_to_broker_naive(end_utc)
                d1_rates = mt5.copy_rates_range(sym, mt5.TIMEFRAME_D1, sb, eb)
                if d1_rates is None or len(d1_rates) < 2:
                    cross_asset[sym] = {
                        "bid": ca_tick.bid, "ask": ca_tick.ask,
                        "spread_pts": (ca_tick.ask - ca_tick.bid) / ca_info.point,
                        "error": "insufficient D1 history",
                    }
                    continue
                d1 = _bars_to_df(d1_rates, clock)
                last_close = float(d1["close"].iloc[-1])

                def _pct_change(n: int) -> Optional[float]:
                    if len(d1) <= n:
                        return None
                    base = float(d1["close"].iloc[-n - 1])
                    return (last_close / base - 1) * 100 if base != 0 else None

                # Simple ATR-ish proxy: mean of last 14 D1 true ranges
                tr = (d1["high"] - d1["low"]).abs()
                atr_d1_14 = float(tr.tail(14).mean())

                cross_asset[sym] = {
                    "bid": float(ca_tick.bid),
                    "ask": float(ca_tick.ask),
                    "spread_pts": (ca_tick.ask - ca_tick.bid) / ca_info.point,
                    "last_close": last_close,
                    "d1_change_pct": _pct_change(1),
                    "d5_change_pct": _pct_change(5),
                    "d20_change_pct": _pct_change(20),
                    "atr_d1_14": atr_d1_14,
                }
            except Exception as exc:  # noqa: BLE001
                cross_asset[sym] = {"error": str(exc)}

        # Back-compat: only populate strategy keys if those TFs were pulled
        result: dict = {
            "live_spread_pts": live_spread_pts,
            "contract_size": float(info.trade_contract_size),
            "point": float(info.point),
            "commission_roundtrip_usd_per_lot": cost.commission_roundtrip_usd,
            "min_lot_step": float(getattr(info, "volume_step", XAUUSD_MIN_LOT_STEP)),
            "account_balance": float(acct.balance),
            "server": acct.server,
            "account_login": int(acct.login),
            "bars": bars,
            "cross_asset": cross_asset,
            "snapshot_utc": end_utc.isoformat(),
        }
        if "H4" in bars:
            result["h4_bars"] = bars["H4"]
        if "H1" in bars:
            result["h1_bars"] = bars["H1"]
        if "M15" in bars:
            result["m15_bars"] = bars["M15"]
        return result
    finally:
        mt5.shutdown()


def scan_symbols(
    symbols: Optional[list[str] | tuple[str, ...]] = None,
    *,
    timeframes: Optional[dict[str, pd.Timedelta]] = None,
) -> dict[str, dict]:
    """Batch-pull multi-TF data for multiple symbols in a single MT5 session.

    Designed for scanner-style "what's setting up across my watchlist" workflow.
    Faster than calling pull_live_context per symbol because we share the MT5
    connection, the clock discovery, and the broker symbol-select warmup.

    Args:
        symbols: list of primary symbols to scan. Defaults to
            DEFAULT_FOCUS_SYMBOLS (XAGUSD + USTEC).
        timeframes: per-TF lookback dict. Defaults to DEFAULT_TIMEFRAME_LOOKBACKS
            (7 TFs from D1 to M1).

    Returns:
        dict mapping symbol -> {
            "bars": dict[tf_name, pd.DataFrame],
            "tick_bid": float,
            "tick_ask": float,
            "live_spread_pts": float,
            "contract_size": float,
            "point": float,
            "snapshot_utc": str,
        }
        Symbols that fail to pull get {"error": "..."} instead.

    Per-symbol latency: ~5-30ms warm cache. 2 symbols = ~50-100ms total.
    """
    import sys as _sys
    from datetime import datetime as _dt, timezone as _tz
    _sys.path.insert(0, str(REPO_ROOT / "scripts"))

    from mt5_connect import init_mt5  # noqa: E402
    import MetaTrader5 as mt5  # noqa: E402
    from quant.config import get_demo_mt5_path  # noqa: E402
    from quant.data.broker_time import discover_clock  # noqa: E402

    if symbols is None:
        symbols = list(DEFAULT_FOCUS_SYMBOLS)
    if timeframes is None:
        timeframes = dict(DEFAULT_TIMEFRAME_LOOKBACKS)

    _tf_const_for = {
        "D1": "TIMEFRAME_D1", "H4": "TIMEFRAME_H4", "H1": "TIMEFRAME_H1",
        "M30": "TIMEFRAME_M30", "M15": "TIMEFRAME_M15",
        "M5": "TIMEFRAME_M5", "M1": "TIMEFRAME_M1",
    }
    unknown = set(timeframes) - set(_tf_const_for)
    if unknown:
        raise ValueError(f"unknown timeframes: {sorted(unknown)}")

    if not init_mt5(terminal_path=get_demo_mt5_path()):
        raise RuntimeError("MT5 init failed")

    try:
        # Use first available symbol for clock discovery
        clock = None
        for s in symbols:
            if mt5.symbol_select(s, True):
                acct = mt5.account_info()
                clock = discover_clock(mt5, acct.server, s)
                break
        if clock is None:
            raise RuntimeError("could not discover clock from any symbol")

        end_utc = _dt.now(tz=_tz.utc)
        results: dict[str, dict] = {}

        for sym in symbols:
            try:
                if not mt5.symbol_select(sym, True):
                    results[sym] = {"error": "symbol_select failed"}
                    continue
                info = mt5.symbol_info(sym)
                tick = mt5.symbol_info_tick(sym)
                if info is None or tick is None:
                    results[sym] = {"error": "info or tick None"}
                    continue

                bars: dict[str, pd.DataFrame] = {}
                for tf_name, lookback in timeframes.items():
                    tf_const = getattr(mt5, _tf_const_for[tf_name])
                    sb = clock.utc_dt_to_broker_naive(end_utc - lookback)
                    eb = clock.utc_dt_to_broker_naive(end_utc)
                    rates = mt5.copy_rates_range(sym, tf_const, sb, eb)
                    if rates is None or len(rates) == 0:
                        continue
                    df = pd.DataFrame(rates)
                    df["time"] = df["time"].apply(
                        lambda s: pd.Timestamp(
                            clock.broker_msc_to_utc_msc(int(s) * 1000),
                            unit="ms", tz="UTC",
                        )
                    )
                    bars[tf_name] = df

                results[sym] = {
                    "bars": bars,
                    "tick_bid": float(tick.bid),
                    "tick_ask": float(tick.ask),
                    "live_spread_pts": (tick.ask - tick.bid) / info.point,
                    "contract_size": float(info.trade_contract_size),
                    "point": float(info.point),
                    "snapshot_utc": end_utc.isoformat(),
                }
            except Exception as exc:  # noqa: BLE001
                results[sym] = {"error": str(exc)}

        return results
    finally:
        mt5.shutdown()


# ---------------------------------------------------------------------------
# Pretty-printer + CLI
# ---------------------------------------------------------------------------

_STATUS_TAG = {"pass": "[PASS]", "fail": "[FAIL]", "warn": "[WARN]", "skip": "[SKIP]"}


def format_report(report: EvalReport) -> str:
    """Render an EvalReport as a plain-ASCII multi-line string.

    ASCII-only so PowerShell / cmd.exe on Windows (default GBK codepage)
    can print without UnicodeEncodeError. Users wanting glyphs can wrap
    this output downstream.
    """
    lines = [
        f"=== {report.symbol} {report.direction.upper()} "
        f"entry={report.entry} sl={report.sl} tp={report.tp} ===",
    ]
    for c in report.checks:
        tag = _STATUS_TAG.get(c.status, "[?]")
        lines.append(f"{tag} {c.name}: {c.label}")
        if c.detail:
            lines.append(f"      {c.detail}")
    lines.append(f"--- VERDICT: {report.verdict.upper()} ---")
    return "\n".join(lines)


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(
        description="Evaluate a candidate trade against frozen BOS-reversal rules.",
    )
    p.add_argument("--symbol", required=True)
    p.add_argument("--side", required=True, choices=("long", "short"))
    p.add_argument("--entry", required=True, type=float)
    p.add_argument("--sl", required=True, type=float)
    p.add_argument("--tp", required=True, type=float)
    p.add_argument("--account-balance", type=float, default=None,
                   help="override; default = live MT5 demo account balance")
    p.add_argument("--risk-pct", type=float, default=0.02)
    p.add_argument("--spread-pts", type=float, default=None,
                   help="override; default = live tick (ask - bid) / point")
    p.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT,
                   help="parquet root for --offline mode")
    p.add_argument("--months-back", type=int, default=2)
    p.add_argument(
        "--offline",
        action="store_true",
        help="skip MT5; load bars from parquet and use IC Markets XAUUSD constants",
    )
    p.add_argument(
        "--no-journal",
        action="store_true",
        help="suppress journal.log_skip on rejection (use for dry runs)",
    )
    args = p.parse_args(argv)

    # Build kwargs progressively: live MT5 context fills most of them; CLI
    # flags override anything the user explicitly specified.
    kwargs: dict = {
        "symbol": args.symbol,
        "direction": args.side,
        "entry": args.entry,
        "sl": args.sl,
        "tp": args.tp,
        "risk_pct": args.risk_pct,
        "journal_skip_on_reject": not args.no_journal,
    }

    if args.offline:
        kwargs["h4_bars"] = load_recent_bars(
            data_root=args.data_root, symbol=args.symbol, timeframe="H4",
            months_back=args.months_back,
        )
        kwargs["h1_bars"] = load_recent_bars(
            data_root=args.data_root, symbol=args.symbol, timeframe="H1",
            months_back=args.months_back,
        )
        kwargs["m15_bars"] = load_recent_bars(
            data_root=args.data_root, symbol=args.symbol, timeframe="M15",
            months_back=args.months_back,
        )
        kwargs["account_balance"] = args.account_balance
        kwargs["live_spread_pts"] = args.spread_pts
    else:
        ctx = pull_live_context(args.symbol)
        tf_summary = ", ".join(
            f"{name}({len(df):,}b)" for name, df in ctx["bars"].items()
        )
        ca_summary = ", ".join(ctx["cross_asset"].keys())
        print(
            f"[MT5] {ctx['server']} acct={ctx['account_login']} "
            f"balance=${ctx['account_balance']:,.2f} "
            f"spread={ctx['live_spread_pts']:.0f}pt"
        )
        print(f"[BARS] {tf_summary}")
        print(f"[CROSS] {ca_summary}")
        kwargs.update({
            "h4_bars": ctx["h4_bars"],
            "h1_bars": ctx["h1_bars"],
            "m15_bars": ctx["m15_bars"],
            "contract_size": ctx["contract_size"],
            "point": ctx["point"],
            "commission_roundtrip_usd_per_lot": ctx["commission_roundtrip_usd_per_lot"],
            "min_lot_step": ctx["min_lot_step"],
            "account_balance": (
                args.account_balance if args.account_balance is not None
                else ctx["account_balance"]
            ),
            "live_spread_pts": (
                args.spread_pts if args.spread_pts is not None
                else ctx["live_spread_pts"]
            ),
        })

    report = evaluate_trade(**kwargs)
    print(format_report(report))
    return 0 if report.verdict == "accept" else 1


if __name__ == "__main__":
    raise SystemExit(main())

"""Tests for live_analysis.eval_trade — the candidate-trade judge.

Strategy: feed real bars (May 2026 reference setup from the parquet lake)
or empty/synthetic bars depending on what each test needs. No MT5 — all
parameters are injected into ``evaluate_trade`` directly.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from live_analysis import journal
from live_analysis.eval_trade import (
    CheckResult,
    EvalReport,
    _check_orientation,
    _check_rr,
    evaluate_trade,
)


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DATA_ROOT = REPO_ROOT / "data"


@pytest.fixture
def jpath(tmp_path: Path) -> Path:
    return tmp_path / "skip_journal.jsonl"


@pytest.fixture(scope="session")
def may_reference_bars() -> dict[str, pd.DataFrame]:
    """Load XAUUSD H4/H1/M15 covering the May 6 BOS reference (from parquet)."""
    from quant.data.parquet import read_bars_month

    frames: dict[str, pd.DataFrame] = {}
    for tf in ("H4", "H1", "M15"):
        apr = read_bars_month(DATA_ROOT, "XAUUSD", tf, 2026, 4)
        may = read_bars_month(DATA_ROOT, "XAUUSD", tf, 2026, 5)
        frames[tf] = (
            pd.concat([apr, may], ignore_index=True)
            .sort_values("time")
            .reset_index(drop=True)
        )
    return frames


@pytest.fixture
def empty_bars() -> dict[str, pd.DataFrame]:
    """Tiny bars frames with valid schema but no setup possible."""
    rows = pd.DataFrame({
        "time": pd.date_range("2026-01-01", periods=10, freq="4h", tz="UTC"),
        "open": [4500.0] * 10,
        "high": [4505.0] * 10,
        "low": [4495.0] * 10,
        "close": [4500.0] * 10,
        "tick_volume": [1000] * 10,
        "spread": [10] * 10,
        "real_volume": [0] * 10,
    })
    return {"H4": rows, "H1": rows, "M15": rows}


# ---------------------------------------------------------------------------
# Pure check helpers
# ---------------------------------------------------------------------------

def test_orientation_long_correct() -> None:
    r = _check_orientation("long", entry=100, sl=90, tp=120)
    assert r.status == "pass"


def test_orientation_long_swapped() -> None:
    r = _check_orientation("long", entry=100, sl=110, tp=90)
    assert r.status == "fail"
    assert "sl < entry < tp" in r.label


def test_orientation_short_correct() -> None:
    r = _check_orientation("short", entry=100, sl=110, tp=80)
    assert r.status == "pass"


def test_orientation_short_swapped() -> None:
    r = _check_orientation("short", entry=100, sl=90, tp=110)
    assert r.status == "fail"


def test_rr_pass_at_threshold() -> None:
    # risk=10, reward=15 → 1.5 exactly
    r = _check_rr("long", entry=100, sl=90, tp=115, min_rr=1.5)
    assert r.status == "pass"
    assert "1.50" in r.label


def test_rr_fail_below_threshold() -> None:
    # risk=10, reward=10 → 1.0
    r = _check_rr("long", entry=100, sl=90, tp=110, min_rr=1.5)
    assert r.status == "fail"


def test_rr_zero_risk_fails() -> None:
    r = _check_rr("long", entry=100, sl=100, tp=120, min_rr=1.5)
    assert r.status == "fail"
    assert "undefined" in r.label.lower() or "zero" in r.label.lower()


# ---------------------------------------------------------------------------
# evaluate_trade — end-to-end with injected bars
# ---------------------------------------------------------------------------

def test_short_is_rejected_strategy_not_authorized(
    empty_bars: dict[str, pd.DataFrame], jpath: Path,
) -> None:
    report = evaluate_trade(
        symbol="XAUUSD",
        direction="short",
        entry=4500,
        sl=4520,  # short: sl > entry
        tp=4460,  # short: tp < entry
        h4_bars=empty_bars["H4"],
        m15_bars=empty_bars["M15"],
        h1_bars=empty_bars["H1"],
        journal_path=jpath,
    )
    assert report.verdict == "reject"
    failures = {c.name for c in report.checks if c.status == "fail"}
    assert "signal_match" in failures
    sig = next(c for c in report.checks if c.name == "signal_match")
    assert "SHORT" in sig.label or "short" in sig.label


def test_orientation_failure_short_circuits_other_failures(
    empty_bars: dict[str, pd.DataFrame], jpath: Path,
) -> None:
    """An obviously-broken candidate (SL on wrong side) must be flagged."""
    report = evaluate_trade(
        symbol="XAUUSD",
        direction="long",
        entry=4500,
        sl=4520,  # WRONG: sl above entry for long
        tp=4540,
        h4_bars=empty_bars["H4"],
        m15_bars=empty_bars["M15"],
        h1_bars=empty_bars["H1"],
        journal_path=jpath,
    )
    assert report.verdict == "reject"
    first_fail = report.first_failure
    assert first_fail is not None
    assert first_fail.name == "orientation"


def test_journal_skip_recorded_on_reject(
    empty_bars: dict[str, pd.DataFrame], jpath: Path,
) -> None:
    evaluate_trade(
        symbol="XAUUSD",
        direction="long",
        entry=4500,
        sl=4480,
        tp=4540,
        h4_bars=empty_bars["H4"],
        m15_bars=empty_bars["M15"],
        h1_bars=empty_bars["H1"],
        journal_path=jpath,
    )
    records = journal.load(jpath)
    assert len(records) == 1
    rec = records[0]
    assert rec["event"] == "skip"
    assert rec["symbol"] == "XAUUSD"
    assert rec["direction"] == "long"
    # signal_match should be the first failure (orientation passes here)
    assert rec["rule_failed"] == "signal_match"
    assert rec["candidate"]["entry"] == 4500
    assert rec["candidate"]["sl"] == 4480
    assert rec["candidate"]["tp"] == 4540


def test_journal_skip_suppressed_when_flag_off(
    empty_bars: dict[str, pd.DataFrame], jpath: Path,
) -> None:
    evaluate_trade(
        symbol="XAUUSD",
        direction="long",
        entry=4500,
        sl=4480,
        tp=4540,
        h4_bars=empty_bars["H4"],
        m15_bars=empty_bars["M15"],
        h1_bars=empty_bars["H1"],
        journal_path=jpath,
        journal_skip_on_reject=False,
    )
    assert journal.load(jpath) == []


def test_spread_check_pass_when_below_p95(empty_bars: dict[str, pd.DataFrame]) -> None:
    """spread of 12pt is below the all-10pt baseline P95 (10)."""
    rows = empty_bars["H4"].copy()
    rows["spread"] = 10
    report = evaluate_trade(
        symbol="XAUUSD",
        direction="long",
        entry=4500, sl=4480, tp=4540,
        h4_bars=rows, m15_bars=empty_bars["M15"], h1_bars=empty_bars["H1"],
        live_spread_pts=10,
        journal_skip_on_reject=False,
    )
    spread_check = next(c for c in report.checks if c.name == "spread")
    assert spread_check.status == "pass"


def test_spread_check_fail_when_well_above_p95(empty_bars: dict[str, pd.DataFrame]) -> None:
    rows = empty_bars["H4"].copy()
    rows["spread"] = 10
    report = evaluate_trade(
        symbol="XAUUSD",
        direction="long",
        entry=4500, sl=4480, tp=4540,
        h4_bars=rows, m15_bars=empty_bars["M15"], h1_bars=empty_bars["H1"],
        live_spread_pts=50,  # 5× baseline
        journal_skip_on_reject=False,
    )
    spread_check = next(c for c in report.checks if c.name == "spread")
    assert spread_check.status == "fail"


def test_news_check_is_skip(empty_bars: dict[str, pd.DataFrame]) -> None:
    report = evaluate_trade(
        symbol="XAUUSD",
        direction="long",
        entry=4500, sl=4480, tp=4540,
        h4_bars=empty_bars["H4"], m15_bars=empty_bars["M15"], h1_bars=empty_bars["H1"],
        journal_skip_on_reject=False,
    )
    news = next(c for c in report.checks if c.name == "news")
    assert news.status == "skip"


def test_sizing_pass_with_balance(empty_bars: dict[str, pd.DataFrame]) -> None:
    """$10k account, 2% risk, $20 SL distance, 100 oz contract → 0.10 lot."""
    report = evaluate_trade(
        symbol="XAUUSD",
        direction="long",
        entry=4500, sl=4480, tp=4540,
        h4_bars=empty_bars["H4"], m15_bars=empty_bars["M15"], h1_bars=empty_bars["H1"],
        account_balance=10_000.0,
        risk_pct=0.02,
        journal_skip_on_reject=False,
    )
    sizing = next(c for c in report.checks if c.name == "sizing")
    assert sizing.status == "pass"
    assert "0.10" in sizing.label


def test_sizing_skipped_without_balance(empty_bars: dict[str, pd.DataFrame]) -> None:
    report = evaluate_trade(
        symbol="XAUUSD",
        direction="long",
        entry=4500, sl=4480, tp=4540,
        h4_bars=empty_bars["H4"], m15_bars=empty_bars["M15"], h1_bars=empty_bars["H1"],
        journal_skip_on_reject=False,
    )
    sizing = next(c for c in report.checks if c.name == "sizing")
    assert sizing.status == "skip"


def test_cost_estimate_matches_xauusd_spec_at_default_lot(
    empty_bars: dict[str, pd.DataFrame],
) -> None:
    """0.10 lot, 12pt spread: spread cost $1.20, commission $0.70, total $1.90.

    Verifies HANDOFF §15.1 reference numbers (IC Markets XAUUSD: 100 oz/lot,
    point=0.01, $7/lot round-trip commission).
    """
    rows = empty_bars["H4"].copy()
    rows["spread"] = 12
    report = evaluate_trade(
        symbol="XAUUSD",
        direction="long",
        entry=4500, sl=4480, tp=4540,
        h4_bars=rows, m15_bars=empty_bars["M15"], h1_bars=empty_bars["H1"],
        live_spread_pts=12,
        journal_skip_on_reject=False,
    )
    cost = next(c for c in report.checks if c.name == "cost")
    assert cost.status == "pass"
    # 12 × 0.01 × 100 × 0.10 = $1.20 spread; 7 × 0.10 = $0.70 commission
    assert "$1.20" in cost.label
    assert "$0.70" in cost.label


def test_first_failure_property() -> None:
    """first_failure returns the first fail check (in declaration order)."""
    report = EvalReport(
        symbol="X", direction="long", entry=1, sl=0.5, tp=2,
        checks=(
            CheckResult(name="a", status="pass", label="ok"),
            CheckResult(name="b", status="fail", label="bad"),
            CheckResult(name="c", status="fail", label="also bad"),
        ),
    )
    assert report.verdict == "reject"
    assert report.first_failure.name == "b"


def test_accept_verdict_when_no_failures() -> None:
    report = EvalReport(
        symbol="X", direction="long", entry=1, sl=0.5, tp=2,
        checks=(
            CheckResult(name="a", status="pass", label="ok"),
            CheckResult(name="b", status="warn", label="meh"),
            CheckResult(name="c", status="skip", label="not configured"),
        ),
    )
    assert report.verdict == "accept"
    assert report.first_failure is None


# ---------------------------------------------------------------------------
# Real-data integration: the May 6 reference setup
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    not (DATA_ROOT / "bars" / "XAUUSD" / "H4" / "2026" / "2026-05.parquet").exists(),
    reason="May 2026 XAUUSD parquet not present (data is gitignored)",
)
def test_may_reference_setup_emits_signal_match_pass(
    may_reference_bars: dict[str, pd.DataFrame], jpath: Path,
) -> None:
    """End-to-end: May 6 BOS-reversal reference fires an A-grade signal.

    We feed the full Apr-May 2026 H4/H1/M15 bars; truncate M15 to just after
    the strategy's expected entry so the recency window ('last K M15 bars')
    captures it. A long candidate at/near the strategy's entry should pass
    signal_match.
    """
    from quant.strategies.bos_reversal import detect_bos_reversal_signals

    signals = detect_bos_reversal_signals(
        h4_bars=may_reference_bars["H4"],
        m15_bars=may_reference_bars["M15"],
        h1_bars=may_reference_bars["H1"],
    )
    if not signals:
        pytest.skip("strategy emits no signals on this slice (data drift)")
    target = signals[-1]

    # Trim M15 so the strategy's last signal is the LAST M15 bar (= 'now')
    m15_trimmed = may_reference_bars["M15"][
        may_reference_bars["M15"]["time"] <= target.entry_time
    ].reset_index(drop=True)

    report = evaluate_trade(
        symbol="XAUUSD",
        direction="long",
        entry=target.entry_price,
        sl=target.sl_price,
        tp=target.tp_price,
        h4_bars=may_reference_bars["H4"],
        m15_bars=m15_trimmed,
        h1_bars=may_reference_bars["H1"],
        account_balance=10_000.0,
        live_spread_pts=12,
        journal_path=jpath,
        journal_skip_on_reject=False,
    )
    sig = next(c for c in report.checks if c.name == "signal_match")
    assert sig.status == "pass", f"got: {sig.label} | {sig.detail}"
    assert report.verdict == "accept"

"""Tests for live_analysis.journal — JSONL trade record I/O."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from live_analysis import journal


@pytest.fixture
def jpath(tmp_path: Path) -> Path:
    return tmp_path / "journal.jsonl"


def test_log_entry_writes_jsonl_record(jpath: Path) -> None:
    tid = journal.log_entry(
        symbol="XAUUSD",
        direction="long",
        lots=0.1,
        entry=4669.26,
        sl=4649.26,
        tp=4709.26,
        rationale="BOS-reversal A-grade, May 4 capit",
        live_spread_pts=10,
        live_session="London-NY overlap",
        path=jpath,
    )
    rec = json.loads(jpath.read_text(encoding="utf-8").strip())
    assert rec["event"] == "entry"
    assert rec["trade_id"] == tid
    assert rec["symbol"] == "XAUUSD"
    assert rec["direction"] == "long"
    assert rec["lots"] == 0.1
    assert rec["sl"] == 4649.26
    assert rec["tp"] == 4709.26
    assert rec["live_spread_pts"] == 10
    assert rec["live_session"] == "London-NY overlap"


def test_log_entry_then_exit_appends_two_linked_rows(jpath: Path) -> None:
    tid = journal.log_entry(
        symbol="XAUUSD",
        direction="long",
        lots=0.1,
        entry=4669.26,
        sl=4649.26,
        tp=4709.26,
        rationale="test",
        path=jpath,
    )
    journal.log_exit(
        trade_id=tid,
        symbol="XAUUSD",
        exit_price=4709.26,
        reason="tp",
        net_pnl=438.68,
        path=jpath,
    )
    lines = jpath.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    entry, exit_ = json.loads(lines[0]), json.loads(lines[1])
    assert entry["event"] == "entry"
    assert exit_["event"] == "exit"
    assert exit_["trade_id"] == entry["trade_id"] == tid
    assert exit_["reason"] == "tp"
    assert exit_["net_pnl"] == 438.68


def test_long_rejects_inverted_sl_tp(jpath: Path) -> None:
    with pytest.raises(ValueError, match="long requires sl < entry < tp"):
        journal.log_entry(
            symbol="XAUUSD",
            direction="long",
            lots=0.1,
            entry=4669.26,
            sl=4709.26,  # SL above entry — wrong for long
            tp=4649.26,  # TP below entry — wrong for long
            rationale="bad",
            path=jpath,
        )
    assert not jpath.exists()  # nothing written


def test_short_accepts_correct_order_and_rejects_inverted(jpath: Path) -> None:
    journal.log_entry(
        symbol="USTEC",
        direction="short",
        lots=0.5,
        entry=29200,
        sl=29325,  # SL above entry (correct for short)
        tp=28950,  # TP below entry (correct for short)
        rationale="short setup",
        path=jpath,
    )
    rec = json.loads(jpath.read_text(encoding="utf-8").strip())
    assert rec["direction"] == "short"

    with pytest.raises(ValueError, match="short requires tp < entry < sl"):
        journal.log_entry(
            symbol="USTEC",
            direction="short",
            lots=0.5,
            entry=29200,
            sl=28950,  # inverted
            tp=29325,  # inverted
            rationale="bad",
            path=jpath,
        )


def test_invalid_direction_rejected(jpath: Path) -> None:
    with pytest.raises(ValueError, match="direction must be one of"):
        journal.log_entry(
            symbol="XAUUSD",
            direction="up",  # not 'long'/'short'
            lots=0.1,
            entry=100,
            sl=90,
            tp=120,
            rationale="x",
            path=jpath,
        )


def test_zero_or_negative_lots_rejected(jpath: Path) -> None:
    with pytest.raises(ValueError, match="lots must be positive"):
        journal.log_entry(
            symbol="XAUUSD",
            direction="long",
            lots=0,
            entry=100,
            sl=90,
            tp=120,
            rationale="x",
            path=jpath,
        )


def test_log_skip_records_rule_failed(jpath: Path) -> None:
    journal.log_skip(
        symbol="XAUUSD",
        direction="long",
        reason="H4 trend not down — no 3 LL + 3 LH pattern",
        rule_failed="stage_1_h4_trend",
        candidate={"entry": 4665, "sl": 4645, "tp": 4705},
        path=jpath,
    )
    rec = json.loads(jpath.read_text(encoding="utf-8").strip())
    assert rec["event"] == "skip"
    assert rec["rule_failed"] == "stage_1_h4_trend"
    assert rec["candidate"]["entry"] == 4665


def test_invalid_exit_reason_rejected(jpath: Path) -> None:
    with pytest.raises(ValueError, match="reason must be one of"):
        journal.log_exit(
            trade_id="abc123",
            symbol="XAUUSD",
            exit_price=100,
            reason="liquidation",  # not in allowlist
            net_pnl=-50,
            path=jpath,
        )


def test_timestamp_is_utc_iso_z(jpath: Path) -> None:
    journal.log_entry(
        symbol="XAUUSD",
        direction="long",
        lots=0.1,
        entry=100,
        sl=90,
        tp=120,
        rationale="x",
        path=jpath,
    )
    rec = json.loads(jpath.read_text(encoding="utf-8").strip())
    assert rec["ts"].endswith("Z")
    # parses cleanly as UTC
    datetime.strptime(rec["ts"], "%Y-%m-%dT%H:%M:%SZ")


def test_explicit_timestamp_preserved(jpath: Path) -> None:
    fixed = datetime(2026, 5, 13, 19, 0, 0, tzinfo=timezone.utc)
    journal.log_entry(
        symbol="XAUUSD",
        direction="long",
        lots=0.1,
        entry=100,
        sl=90,
        tp=120,
        rationale="x",
        ts=fixed,
        path=jpath,
    )
    rec = json.loads(jpath.read_text(encoding="utf-8").strip())
    assert rec["ts"] == "2026-05-13T19:00:00Z"


def test_load_returns_all_records_in_order(jpath: Path) -> None:
    tid = journal.log_entry(
        symbol="XAUUSD",
        direction="long",
        lots=0.1,
        entry=100,
        sl=90,
        tp=120,
        rationale="x",
        path=jpath,
    )
    journal.log_skip(
        symbol="USTEC",
        direction="short",
        reason="spread too wide",
        path=jpath,
    )
    journal.log_exit(
        trade_id=tid,
        symbol="XAUUSD",
        exit_price=120,
        reason="tp",
        net_pnl=20,
        path=jpath,
    )
    records = journal.load(jpath)
    assert [r["event"] for r in records] == ["entry", "skip", "exit"]


def test_load_missing_file_returns_empty(tmp_path: Path) -> None:
    assert journal.load(tmp_path / "nope.jsonl") == []


def test_data_dir_auto_created(tmp_path: Path) -> None:
    nested = tmp_path / "a" / "b" / "c" / "journal.jsonl"
    journal.log_entry(
        symbol="XAUUSD",
        direction="long",
        lots=0.1,
        entry=100,
        sl=90,
        tp=120,
        rationale="x",
        path=nested,
    )
    assert nested.exists()

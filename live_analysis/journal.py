"""JSONL trade journal — append-only record of entries, exits, and skips.

Each event is a single JSON object on its own line. Schema:

  entry:  ts, event="entry", trade_id, symbol, direction, lots,
          entry, sl, tp, rationale, [live_spread_pts], [live_session]
  exit:   ts, event="exit",  trade_id, symbol, exit_price, reason, net_pnl, [notes]
  skip:   ts, event="skip",  symbol, direction, reason, [rule_failed], [candidate]

`log_entry` returns the trade_id — pass it to `log_exit` later to link the pair.

Validation is strict on the things that catch real bugs (direction string,
positive lots, SL/TP ordering vs entry); everything else is trusted.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_JOURNAL_PATH = REPO_ROOT / "live_analysis" / "data" / "trade_journal.jsonl"

Direction = Literal["long", "short"]
ExitReason = Literal["tp", "sl", "be", "manual", "trail", "time_stop"]

_VALID_DIRECTIONS = ("long", "short")
_VALID_EXIT_REASONS = ("tp", "sl", "be", "manual", "trail", "time_stop")


def _utc_iso(ts: Optional[datetime] = None) -> str:
    ts = ts or datetime.now(timezone.utc)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _append(record: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def log_entry(
    *,
    symbol: str,
    direction: Direction,
    lots: float,
    entry: float,
    sl: float,
    tp: float,
    rationale: str,
    live_spread_pts: Optional[float] = None,
    live_session: Optional[str] = None,
    trade_id: Optional[str] = None,
    ts: Optional[datetime] = None,
    path: Path = DEFAULT_JOURNAL_PATH,
) -> str:
    """Record a new trade entry. Returns the trade_id (caller stores it for log_exit)."""
    if direction not in _VALID_DIRECTIONS:
        raise ValueError(f"direction must be one of {_VALID_DIRECTIONS}, got {direction!r}")
    if lots <= 0:
        raise ValueError(f"lots must be positive, got {lots}")
    if direction == "long" and not (sl < entry < tp):
        raise ValueError(
            f"long requires sl < entry < tp, got sl={sl} entry={entry} tp={tp}"
        )
    if direction == "short" and not (tp < entry < sl):
        raise ValueError(
            f"short requires tp < entry < sl, got sl={sl} entry={entry} tp={tp}"
        )

    tid = trade_id or uuid.uuid4().hex[:12]
    record: dict[str, Any] = {
        "ts": _utc_iso(ts),
        "event": "entry",
        "trade_id": tid,
        "symbol": symbol,
        "direction": direction,
        "lots": lots,
        "entry": entry,
        "sl": sl,
        "tp": tp,
        "rationale": rationale,
    }
    if live_spread_pts is not None:
        record["live_spread_pts"] = live_spread_pts
    if live_session is not None:
        record["live_session"] = live_session
    _append(record, path)
    return tid


def log_exit(
    *,
    trade_id: str,
    symbol: str,
    exit_price: float,
    reason: ExitReason,
    net_pnl: float,
    ts: Optional[datetime] = None,
    notes: Optional[str] = None,
    path: Path = DEFAULT_JOURNAL_PATH,
) -> None:
    """Record exit for a previously-logged entry. trade_id links to the entry row."""
    if reason not in _VALID_EXIT_REASONS:
        raise ValueError(f"reason must be one of {_VALID_EXIT_REASONS}, got {reason!r}")
    record: dict[str, Any] = {
        "ts": _utc_iso(ts),
        "event": "exit",
        "trade_id": trade_id,
        "symbol": symbol,
        "exit_price": exit_price,
        "reason": reason,
        "net_pnl": net_pnl,
    }
    if notes:
        record["notes"] = notes
    _append(record, path)


def log_skip(
    *,
    symbol: str,
    direction: Direction,
    reason: str,
    rule_failed: Optional[str] = None,
    candidate: Optional[dict] = None,
    ts: Optional[datetime] = None,
    path: Path = DEFAULT_JOURNAL_PATH,
) -> None:
    """Record a setup considered but rejected — surfaces times the rules were followed."""
    if direction not in _VALID_DIRECTIONS:
        raise ValueError(f"direction must be one of {_VALID_DIRECTIONS}, got {direction!r}")
    record: dict[str, Any] = {
        "ts": _utc_iso(ts),
        "event": "skip",
        "symbol": symbol,
        "direction": direction,
        "reason": reason,
    }
    if rule_failed:
        record["rule_failed"] = rule_failed
    if candidate:
        record["candidate"] = candidate
    _append(record, path)


def load(path: Path = DEFAULT_JOURNAL_PATH) -> list[dict]:
    """Read all journal records as a list of dicts (chronological by file order)."""
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]

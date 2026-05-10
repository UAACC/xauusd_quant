"""
Symbol specification snapshots and diffing.

Why daily snapshots: brokers can silently change commission tiers, swap rates,
margin requirements, contract specs. A daily JSON snapshot committed to
``fixtures/spec/<symbol>_<date>.json`` gives a version-controlled audit trail.
``diff_snapshots()`` + ``severity()`` classify changes for alerting.

Static vs dynamic:
    SymbolStaticSpec    — fields that should NEVER change. Any change is a
                          broker-policy event and must be flagged immediately.
    Other fields on SymbolDailySnapshot — drift expected (swap reprices daily,
                          margin can move with leverage policy, etc.).

Bid/ask/spread captured at snapshot time are noise (depend on the moment we
ran the script); never alert on them.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


# Snapshot-of-live-state fields. Always differ across runs; never alert.
_NOISY_FIELDS = frozenset({
    "captured_at_utc",
    "spread_points_at_capture",
    "bid_at_capture",
    "ask_at_capture",
    "broker_clock.measured_at_utc",
})


@dataclass(frozen=True)
class SymbolStaticSpec:
    """Fields that should never change for a given symbol+broker."""

    symbol: str
    contract_size: float
    point: float
    digits: int
    tick_size: float
    tick_value: float
    swap_mode: int
    swap_3day_index: int
    profit_currency: str
    margin_currency: str

    @classmethod
    def from_mt5_info(cls, info: Any, symbol: str) -> "SymbolStaticSpec":
        return cls(
            symbol=symbol,
            contract_size=info.trade_contract_size,
            point=info.point,
            digits=info.digits,
            tick_size=info.trade_tick_size,
            tick_value=info.trade_tick_value,
            swap_mode=info.swap_mode,
            swap_3day_index=info.swap_rollover3days,
            profit_currency=info.currency_profit,
            margin_currency=info.currency_margin,
        )


@dataclass(frozen=True)
class SymbolDailySnapshot:
    """Full daily spec snapshot: static + dynamic + meta + ambient market state + broker clock."""

    captured_at_utc: str
    server: str
    account_login: int

    static: SymbolStaticSpec

    # daily-drift fields (broker re-prices periodically)
    swap_long: float
    swap_short: float
    margin_initial: float
    stops_level: int

    # caller-supplied (broker docs / observation, not in MT5 info)
    commission_roundtrip_usd: float

    # ambient market state at capture (NOISY — context only)
    spread_points_at_capture: int
    bid_at_capture: float
    ask_at_capture: float

    # Broker timezone discovered at capture (None = pre-clock-aware snapshot).
    # Stored as plain dict so it round-trips through JSON without coupling to BrokerClock dataclass.
    broker_clock: dict[str, Any] | None = None

    @classmethod
    def from_mt5(
        cls,
        info: Any,
        tick: Any,
        symbol: str,
        server: str,
        account_login: int,
        commission_roundtrip_usd: float,
        captured_at: datetime,
        broker_clock: Any = None,
    ) -> "SymbolDailySnapshot":
        clock_dict = broker_clock.to_dict() if broker_clock is not None else None
        return cls(
            captured_at_utc=captured_at.isoformat(),
            server=server,
            account_login=int(account_login),
            static=SymbolStaticSpec.from_mt5_info(info, symbol),
            swap_long=info.swap_long,
            swap_short=info.swap_short,
            margin_initial=info.margin_initial,
            stops_level=info.trade_stops_level,
            commission_roundtrip_usd=commission_roundtrip_usd,
            spread_points_at_capture=info.spread,
            bid_at_capture=tick.bid,
            ask_at_capture=tick.ask,
            broker_clock=clock_dict,
        )

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=True, default=str)

    @classmethod
    def from_json(cls, raw: str) -> "SymbolDailySnapshot":
        d = json.loads(raw)
        d["static"] = SymbolStaticSpec(**d["static"])
        return cls(**d)

    def write(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.to_json(), encoding="utf-8")

    @classmethod
    def read(cls, path: Path) -> "SymbolDailySnapshot":
        return cls.from_json(path.read_text(encoding="utf-8"))


def diff_snapshots(
    prev: SymbolDailySnapshot,
    curr: SymbolDailySnapshot,
) -> dict[str, tuple[Any, Any]]:
    """Field-by-field diff; ignores top-level noisy fields. Returns dotted-path keys."""
    diff: dict[str, tuple[Any, Any]] = {}
    p = asdict(prev)
    c = asdict(curr)

    def walk(prefix: str, a: Any, b: Any) -> None:
        if prefix in _NOISY_FIELDS:
            return
        if isinstance(a, dict) and isinstance(b, dict):
            for k in set(a.keys()) | set(b.keys()):
                walk(f"{prefix}.{k}" if prefix else k, a.get(k), b.get(k))
        elif a != b:
            diff[prefix] = (a, b)

    walk("", p, c)
    return diff


def severity(field_path: str) -> str:
    """Severity classifier for a diff field path.

    Returns one of: ``CRITICAL`` | ``HIGH`` | ``MEDIUM`` | ``EXPECTED``.
    """
    if field_path.startswith("static."):
        return "CRITICAL"
    if field_path in ("broker_clock.iana_tz", "broker_clock.agrees"):
        return "CRITICAL"
    if field_path == "commission_roundtrip_usd":
        return "HIGH"
    if field_path in ("margin_initial", "stops_level", "server", "account_login"):
        return "MEDIUM"
    # broker_clock subfields (offset, source, confidence) drift expected
    # across DST boundaries / market open vs closed; not alerts.
    if field_path.startswith("broker_clock."):
        return "EXPECTED"
    if field_path in ("swap_long", "swap_short"):
        return "EXPECTED"
    return "EXPECTED"

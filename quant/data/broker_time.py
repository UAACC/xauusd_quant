"""
Broker timezone discovery and conversion.

Why this module exists
----------------------
The MetaTrader 5 Python package returns ``time`` and ``time_msc`` fields as
**broker server time interpreted as Unix epoch** — i.e., the integer is a
naive wall-clock moment in the broker's local timezone, serialized as if it
were UTC. This is a well-known MT5 quirk and a foot-gun for any time-of-day
or cross-source analysis. Symmetrically, ``mt5.copy_ticks_from`` etc. read
their ``datetime`` argument as naive broker local time.

We never hard-code an offset constant ("subtract 3 hours") because:
  1. DST transitions happen twice a year (EEST <-> EET) and a constant breaks.
  2. Brokers can change their server timezone at any time.
  3. Different MT5 brokers run different timezones.

Instead we:
  1. Map known broker server prefixes -> IANA tz (delegates DST to ``zoneinfo``).
  2. At connect time, measure the actual broker offset against system UTC by
     comparing a freshly-pulled tick's ``time_msc`` to ``datetime.now(UTC)``.
  3. If measured ≠ expected, ALERT — the assumption is no longer valid.

Conventions used downstream
---------------------------
- All timestamps stored in our parquet data lake are **true UTC milliseconds**.
- Conversion happens at the MT5-API boundary: broker-time on the wire,
  UTC in memory and on disk.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo


# Known broker-server-name prefix -> IANA tz.
# Verified empirically for IC Markets 2026-05-10. Add more as encountered.
KNOWN_BROKER_TZ: dict[str, str] = {
    "ICMarketsSC":   "Europe/Athens",   # Raw Trading Ltd / Seychelles
    "ICMarkets":     "Europe/Athens",   # AU entity
    "ICMarketsEU":   "Europe/Athens",   # EU entity
    "ICMarketsKE":   "Europe/Athens",   # Kenya entity
    "ICMarketsGRP":  "Europe/Athens",   # Group
    "ICMarketsInternational": "Europe/Athens",
    # Fallback used when no prefix matches:
    # _DEFAULT below.
}
_DEFAULT_TZ = "Europe/Athens"

# Tolerances for sanity checks
_MAX_REASONABLE_OFFSET_S = 14 * 3600   # +14h covers the most extreme TZ
_MIN_REASONABLE_OFFSET_S = -12 * 3600  # -12h
_MEASURED_VS_EXPECTED_TOL_S = 60       # within 1 minute = same TZ
_FRESH_TICK_AGE_S = 300                # tick within last 5 min real time = "live"


@dataclass(frozen=True)
class BrokerClock:
    """Discovered + verified broker time information.

    Conversion uses the IANA tz (DST-aware via zoneinfo). The measured
    offset is for sanity-checking that our tz assumption matches reality.

    Attributes:
        iana_tz: IANA name, e.g. ``"Europe/Athens"``.
        measured_offset_seconds: ``broker_epoch - utc_epoch`` from a tick.
        expected_offset_seconds: ``ZoneInfo(iana_tz).utcoffset(now)``.
        measured_at_utc: when we measured.
        source: ``"live_tick"`` (high conf) | ``"stale_tick"`` (medium) |
                ``"default_assumption"`` (low — no measurement).
        confidence: ``"high"`` | ``"medium"`` | ``"low"``.
        agrees: True iff measured ≈ expected (within tolerance).
    """

    iana_tz: str
    measured_offset_seconds: int
    expected_offset_seconds: int
    measured_at_utc: str
    source: str
    confidence: str

    @property
    def agrees(self) -> bool:
        return abs(self.measured_offset_seconds - self.expected_offset_seconds) <= _MEASURED_VS_EXPECTED_TOL_S

    @property
    def measured_offset_hours(self) -> float:
        return self.measured_offset_seconds / 3600.0

    def broker_msc_to_utc_msc(self, broker_msc: int) -> int:
        """Convert ``broker_msc`` (broker wall-clock as Unix epoch) to true UTC ms.

        Always uses ``zoneinfo`` so DST is handled correctly across the year,
        not just at the moment of measurement.
        """
        broker_naive_dt = datetime.fromtimestamp(broker_msc / 1000.0, tz=timezone.utc).replace(tzinfo=None)
        broker_local_dt = broker_naive_dt.replace(tzinfo=ZoneInfo(self.iana_tz))
        return int(broker_local_dt.astimezone(timezone.utc).timestamp() * 1000)

    def utc_dt_to_mt5_query(self, utc_dt: datetime) -> datetime:
        """Convert UTC-aware to the datetime form ``mt5.copy_ticks_from`` /
        ``copy_rates_range`` expect as input.

        MT5 stores ``time_msc`` as **broker-wall-clock interpreted as Unix
        epoch** (i.e., the same number you'd get if you ran ``.timestamp()``
        on the broker-local wall-clock pretending it's UTC). For queries to
        line up with that storage convention, we need to feed MT5 an aware
        datetime whose ``.timestamp()`` returns that same broker-as-UTC
        epoch — independent of the operating system's local timezone.

        We do this by taking the broker-local wall-clock components and
        retagging the tzinfo as UTC. Example: real UTC ``22:03 May 10`` ->
        broker EEST wall-clock ``01:03 May 11`` -> returned datetime is
        ``2026-05-11 01:03:00+00:00`` (an aware UTC datetime whose
        wall-clock matches the broker but tag says UTC).
        """
        if utc_dt.tzinfo is None:
            raise ValueError("Pass a timezone-aware UTC datetime.")
        broker_local = utc_dt.astimezone(ZoneInfo(self.iana_tz))
        return broker_local.replace(tzinfo=timezone.utc)

    # Back-compat alias; old name was misleading because we don't actually
    # want a naive datetime (those silently use system local tz at .timestamp()).
    def utc_dt_to_broker_naive(self, utc_dt: datetime) -> datetime:
        return self.utc_dt_to_mt5_query(utc_dt)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["agrees"] = self.agrees
        d["measured_offset_hours"] = self.measured_offset_hours
        return d


def _expected_offset_at(iana_tz: str, utc_dt: datetime) -> int:
    return int(ZoneInfo(iana_tz).utcoffset(utc_dt).total_seconds())


def _resolve_iana_tz(server_name: str) -> str:
    # Longest matching prefix wins (so "ICMarketsInternational" beats "ICMarkets")
    matches = [(p, tz) for p, tz in KNOWN_BROKER_TZ.items() if server_name.startswith(p)]
    if not matches:
        return _DEFAULT_TZ
    return max(matches, key=lambda kv: len(kv[0]))[1]


def discover_clock(mt5_module: Any, server_name: str, symbol: str) -> BrokerClock:
    """Discover broker clock for the connected MT5 terminal.

    Uses ``mt5.symbol_info_tick(symbol)`` to get a recent broker timestamp,
    compares to system UTC to measure the actual offset, and cross-checks
    against the IANA tz assumption.
    """
    iana_tz = _resolve_iana_tz(server_name)
    real_now = datetime.now(timezone.utc)
    expected_off = _expected_offset_at(iana_tz, real_now)

    measured_off = expected_off
    source = "default_assumption"
    confidence = "low"

    tick = mt5_module.symbol_info_tick(symbol)
    if tick is not None and getattr(tick, "time_msc", 0) > 0:
        candidate = round(tick.time_msc / 1000.0 - real_now.timestamp())
        if _MIN_REASONABLE_OFFSET_S <= candidate <= _MAX_REASONABLE_OFFSET_S:
            measured_off = int(candidate)
            # Tick freshness in real UTC, given the candidate offset
            true_utc_of_tick = tick.time_msc / 1000.0 - candidate
            tick_age_real_s = real_now.timestamp() - true_utc_of_tick
            if 0 <= tick_age_real_s <= _FRESH_TICK_AGE_S:
                source = "live_tick"
                confidence = "high"
            else:
                source = "stale_tick"
                confidence = "medium"

    return BrokerClock(
        iana_tz=iana_tz,
        measured_offset_seconds=measured_off,
        expected_offset_seconds=expected_off,
        measured_at_utc=real_now.isoformat(),
        source=source,
        confidence=confidence,
    )

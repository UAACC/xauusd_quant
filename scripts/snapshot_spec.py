"""
scripts/snapshot_spec.py — daily XAUUSD spec snapshot + diff alert.

Run once per trading day (after market open is ideal — pulls live spec).
Writes ``fixtures/spec/<SYMBOL>_<YYYY-MM-DD>.json`` and diffs against the
most-recent prior snapshot. Severity-classified output:

    CRITICAL  — static spec field changed (broker policy event)
    HIGH      — commission changed
    MEDIUM    — margin / stops_level / server / account
    EXPECTED  — swap_long / swap_short (re-priced daily)

Commit ``fixtures/`` to git for an audit trail of broker behavior over time.

Usage::

    python scripts/snapshot_spec.py
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from mt5_connect import SYMBOL, get_symbol_info, init_mt5  # noqa: E402

import MetaTrader5 as mt5  # noqa: E402

from quant.costs.spec import SymbolDailySnapshot, diff_snapshots, severity  # noqa: E402
from quant.data.broker_time import discover_clock  # noqa: E402

TERMINAL_PATH = r"F:\demo-mt5\terminal64.exe"
COMMISSION_ROUNDTRIP_USD = 7.0  # IC Raw Trading Ltd XAUUSD, verified 2026-05-10
FIXTURES_DIR = REPO_ROOT / "fixtures" / "spec"

# Anonymize identifying fields when writing committed fixtures. fixtures/ is
# tracked in a public repo, so account_login is replaced with 0 by default.
# Override with --keep-identity for local-only audit snapshots.
ANONYMIZE_DEFAULT = True

_SEVERITY_RANK = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "EXPECTED": 3}


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--keep-identity", action="store_true",
                    help="Write real account_login to fixture (default: anonymize for public repo).")
    args = ap.parse_args()
    anonymize = not args.keep_identity if ANONYMIZE_DEFAULT else False

    if not init_mt5(terminal_path=TERMINAL_PATH):
        return 1

    try:
        info = get_symbol_info(SYMBOL)
        if info is None:
            return 2
        tick = mt5.symbol_info_tick(SYMBOL)
        if tick is None:
            print(f"[ERROR] symbol_info_tick({SYMBOL}) None: {mt5.last_error()}")
            return 3
        acct = mt5.account_info()
        clock = discover_clock(mt5, acct.server, SYMBOL)
        print(f"[CLOCK] iana_tz={clock.iana_tz}  measured={clock.measured_offset_hours:+.1f}h  "
              f"expected={clock.expected_offset_seconds/3600:+.1f}h  source={clock.source}  "
              f"confidence={clock.confidence}  agreement={'OK' if clock.agrees else 'MISMATCH'}")

        now = datetime.now(timezone.utc)
        snapshot = SymbolDailySnapshot.from_mt5(
            info=info, tick=tick, symbol=SYMBOL,
            server=acct.server,
            account_login=0 if anonymize else acct.login,
            commission_roundtrip_usd=COMMISSION_ROUNDTRIP_USD,
            captured_at=now,
            broker_clock=clock,
        )
        if anonymize:
            print(f"[INFO] account_login anonymized (set 0). Pass --keep-identity for local audit.")

        out_path = FIXTURES_DIR / f"{SYMBOL}_{now.date().isoformat()}.json"
        if out_path.exists():
            print(f"[INFO] {out_path.name} exists; overwriting (intra-day re-run)")
        snapshot.write(out_path)
        print(f"[OK] snapshot -> {out_path.relative_to(REPO_ROOT)}")

        prev_path = next(
            (p for p in sorted(FIXTURES_DIR.glob(f"{SYMBOL}_*.json"), reverse=True) if p != out_path),
            None,
        )
        if prev_path is None:
            print("[INFO] No prior snapshot; this is the first.")
            return 0

        prev = SymbolDailySnapshot.read(prev_path)
        diff = diff_snapshots(prev, snapshot)
        if not diff:
            print(f"[OK] no change vs {prev_path.name}")
            return 0

        print(f"\n=== Diff vs {prev_path.name} ===")
        items = sorted(diff.items(), key=lambda kv: _SEVERITY_RANK.get(severity(kv[0]), 99))
        for field, (a, b) in items:
            print(f"  [{severity(field):8s}] {field}: {a!r} -> {b!r}")

        critical = [f for f in diff if severity(f) == "CRITICAL"]
        if critical:
            print(f"\n[ALERT] {len(critical)} CRITICAL field(s) changed. "
                  "Investigate broker policy event before relying on cost model.")
            return 4
        return 0

    finally:
        mt5.shutdown()


if __name__ == "__main__":
    sys.exit(main())

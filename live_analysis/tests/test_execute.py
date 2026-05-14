"""Tests for live_analysis.execute — validation + dry-run paths.

We do NOT test actual MT5 order_send (would risk live execution). We test
ALL the validation logic + dry-run mode that builds requests without sending.
"""
from __future__ import annotations

import pytest

from live_analysis.execute import (
    FIXED_LOT,
    _next_magic,
    _validate,
    place_order_safe,
)


# ---------------------------------------------------------------------------
# Hardcoded lot constant
# ---------------------------------------------------------------------------

def test_fixed_lot_is_002_no_other_value() -> None:
    """If anyone changes FIXED_LOT, this test should make them feel pain."""
    assert FIXED_LOT == 0.02, (
        f"FIXED_LOT must remain 0.02 -- user authorization scope per "
        f"feedback-execute-authorization memory. Got {FIXED_LOT}."
    )


def test_no_volume_kwarg_on_place_order_safe() -> None:
    """The wrapper must NOT accept a volume parameter (would defeat the ceiling)."""
    import inspect
    sig = inspect.signature(place_order_safe)
    assert "volume" not in sig.parameters, (
        "place_order_safe must NOT accept a volume parameter -- volume is "
        "hardcoded to FIXED_LOT=0.02 internally"
    )
    assert "lot" not in sig.parameters, "no 'lot' parameter allowed either"
    assert "lots" not in sig.parameters, "no 'lots' parameter allowed either"


# ---------------------------------------------------------------------------
# Direction / price validation
# ---------------------------------------------------------------------------

def test_validate_long_requires_sl_below_entry_below_tp() -> None:
    """Long: SL < entry < TP."""
    _validate("XAUUSD", "long", entry=4700, sl=4680, tp=4730, order_type="market")


def test_validate_short_requires_tp_below_entry_below_sl() -> None:
    _validate("XAUUSD", "short", entry=4700, sl=4720, tp=4670, order_type="market")


def test_validate_rejects_long_with_inverted_sl_tp() -> None:
    with pytest.raises(ValueError, match="long requires"):
        _validate("XAUUSD", "long", entry=4700, sl=4720, tp=4680, order_type="market")


def test_validate_rejects_short_with_inverted_sl_tp() -> None:
    with pytest.raises(ValueError, match="short requires"):
        _validate("XAUUSD", "short", entry=4700, sl=4680, tp=4720, order_type="market")


def test_validate_rejects_negative_prices() -> None:
    with pytest.raises(ValueError, match="prices must be positive"):
        _validate("XAUUSD", "long", entry=-1, sl=4680, tp=4730, order_type="market")


def test_validate_rejects_invalid_direction() -> None:
    with pytest.raises(ValueError, match="direction must be"):
        _validate("XAUUSD", "sideways", entry=4700, sl=4680, tp=4730, order_type="market")


def test_validate_rejects_invalid_order_type() -> None:
    with pytest.raises(ValueError, match="order_type must be"):
        _validate("XAUUSD", "long", entry=4700, sl=4680, tp=4730, order_type="banana")


# ---------------------------------------------------------------------------
# Magic number behavior
# ---------------------------------------------------------------------------

def test_magic_number_is_int_and_starts_with_date_base() -> None:
    magic = _next_magic()
    assert isinstance(magic, int)
    # Should start with MAGIC_BASE (20260514) * 10000 prefix
    assert str(magic).startswith("20260514") or str(magic).startswith("202605140"), (
        f"magic should encode date base; got {magic}"
    )


# ---------------------------------------------------------------------------
# Dry-run mode (no MT5 needed for these)
# ---------------------------------------------------------------------------

def test_dry_run_long_market_builds_correct_request() -> None:
    result = place_order_safe(
        symbol="XAUUSD",
        direction="long",
        entry=4700.0,
        sl=4680.0,
        tp=4730.0,
        order_type="market",
        rationale="test long market",
        dry_run=True,
    )
    assert result["status"] == "dry_run"
    assert result["volume_validated"] == 0.02
    assert result["symbol"] == "XAUUSD"
    assert result["direction"] == "long"
    assert result["request"]["volume"] == 0.02
    assert result["request"]["symbol"] == "XAUUSD"
    assert result["request"]["sl"] == 4680.0
    assert result["request"]["tp"] == 4730.0
    assert result["request"]["magic"] == result["magic"]


def test_dry_run_short_stop_builds_pending_order() -> None:
    result = place_order_safe(
        symbol="XAGUSD",
        direction="short",
        entry=86.45,
        sl=88.45,
        tp=83.02,
        order_type="stop",
        rationale="XAG breakdown short",
        setup_grade="B-discretionary",
        dry_run=True,
    )
    assert result["status"] == "dry_run"
    assert result["volume_validated"] == 0.02
    assert result["setup_grade"] == "B-discretionary"
    # MT5 ORDER_TYPE_SELL_STOP is integer 5; we don't import mt5 here, so just
    # confirm the request has a type field set
    assert "type" in result["request"]


def test_dry_run_validation_still_runs() -> None:
    """Even in dry_run, the validation must reject invalid candidates."""
    with pytest.raises(ValueError, match="long requires"):
        place_order_safe(
            symbol="XAUUSD",
            direction="long",
            entry=4700,
            sl=4720,  # WRONG: above entry
            tp=4730,
            order_type="market",
            rationale="bad sl",
            dry_run=True,
        )


def test_dry_run_does_not_call_mt5() -> None:
    """Dry-run must NOT import or call MT5 (so it works on non-Windows too)."""
    # If this test runs on Windows where MT5 IS available, the assertion is still
    # valid -- we just confirm no actual order was sent. The dry_run path doesn't
    # call init_mt5_live or mt5.order_send.
    result = place_order_safe(
        symbol="XAUUSD", direction="long",
        entry=4700, sl=4680, tp=4730,
        order_type="limit", rationale="test", dry_run=True,
    )
    assert result["status"] == "dry_run"
    # No 'retcode' / 'ticket' / 'fill_price' in dry-run result -- those come
    # only from real order_send results
    assert "retcode" not in result
    assert "ticket" not in result
    assert "fill_price" not in result


# ---------------------------------------------------------------------------
# Hardcoded volume cannot be overridden by external manipulation
# ---------------------------------------------------------------------------

def test_volume_constant_not_easily_mutable() -> None:
    """The constant lives at module level; importing should give the canonical 0.02.

    Note: Python doesn't prevent runtime mutation of module globals, but the
    place_order_safe function reads FIXED_LOT inside the body each call, so
    a contract violator would need to also reach into the module. The double-
    check ``if volume != 0.02`` in place_order_safe catches mutation."""
    from live_analysis import execute
    assert execute.FIXED_LOT == 0.02

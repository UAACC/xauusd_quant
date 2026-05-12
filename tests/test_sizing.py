"""Tests for risk-based position sizing."""
from __future__ import annotations

import pytest

from quant.risk.sizing import lots_for_risk, dollar_risk_for_lots


def test_lots_for_risk_basic_xauusd():
    """20% risk on $1000 account, $20 SL, 100 oz/lot:
       loss-per-lot on SL = $20 × 100 = $2000
       budget = $1000 × 0.20 = $200
       lots = $200 / $2000 = 0.10
    """
    lots = lots_for_risk(
        account_balance=1000.0, risk_pct=0.20,
        sl_distance_price=20.0, contract_size=100.0,
    )
    assert lots == pytest.approx(0.10)


def test_lots_for_risk_scales_linearly_with_account():
    base = lots_for_risk(1000.0, 0.20, 20.0, 100.0)
    big = lots_for_risk(10000.0, 0.20, 20.0, 100.0)
    assert big == pytest.approx(10 * base)


def test_lots_for_risk_inverse_with_sl_distance():
    """Doubling SL distance halves the lots that can be risked."""
    tight = lots_for_risk(1000.0, 0.20, 10.0, 100.0)
    wide = lots_for_risk(1000.0, 0.20, 20.0, 100.0)
    assert wide == pytest.approx(tight / 2)


def test_lots_for_risk_zero_risk_returns_zero():
    assert lots_for_risk(1000.0, 0.0, 20.0, 100.0) == 0.0


def test_lots_for_risk_negative_inputs_raise():
    with pytest.raises(ValueError):
        lots_for_risk(-1000.0, 0.20, 20.0, 100.0)
    with pytest.raises(ValueError):
        lots_for_risk(1000.0, -0.20, 20.0, 100.0)
    with pytest.raises(ValueError):
        lots_for_risk(1000.0, 0.20, 0.0, 100.0)
    with pytest.raises(ValueError):
        lots_for_risk(1000.0, 0.20, 20.0, 0.0)


def test_lots_for_risk_risk_pct_above_one_raises():
    """Risk > 100% is almost certainly a unit error (e.g. passed 20 instead of 0.20)."""
    with pytest.raises(ValueError):
        lots_for_risk(1000.0, 1.5, 20.0, 100.0)


def test_lots_for_risk_optional_min_lot_rounding():
    """If min_lot is given, lots are floored to the broker's minimum step."""
    # Raw calc says 0.234 lots; min step 0.01 -> 0.23
    lots = lots_for_risk(
        account_balance=2340.0, risk_pct=0.20, sl_distance_price=20.0,
        contract_size=100.0, min_lot_step=0.01,
    )
    assert lots == pytest.approx(0.23)


def test_lots_for_risk_below_min_lot_returns_zero():
    """If raw calc rounds down to zero (account too small), return 0 — caller skips trade."""
    lots = lots_for_risk(
        account_balance=50.0, risk_pct=0.20, sl_distance_price=20.0,
        contract_size=100.0, min_lot_step=0.01,
    )
    # raw = 50*0.2/2000 = 0.005; floor to 0.01 step -> 0
    assert lots == 0.0


# ---------------------------------------------------------------------------
# dollar_risk_for_lots — inverse calc, used by reporting
# ---------------------------------------------------------------------------

def test_dollar_risk_for_lots():
    """0.10 lots × $20 SL × 100 oz = $200 risk."""
    assert dollar_risk_for_lots(0.10, sl_distance_price=20.0, contract_size=100.0) == pytest.approx(200.0)

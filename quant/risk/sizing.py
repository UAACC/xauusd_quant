"""Risk-based position sizing.

Given a fixed dollar risk budget and SL distance, compute the lot size
that puts EXACTLY ``risk_pct`` of the account at stake on an SL hit.

Formula::

    loss_per_lot_on_SL  = sl_distance_price × contract_size
    risk_budget_dollars = account_balance × risk_pct
    lots                = risk_budget_dollars / loss_per_lot_on_SL

For XAUUSD (contract_size=100 oz/lot), a $20 price-distance SL means
$2000/lot risk. At 20% on a $10,000 account: 1.0 lot.

A ``min_lot_step`` parameter (broker minimum, e.g. 0.01 for IC Markets
XAUUSD) floors the result; if the raw calculation underflows the
broker's minimum, returns 0 so the caller can skip the trade rather
than rounding UP and over-risking.
"""

from __future__ import annotations

import math


def lots_for_risk(
    account_balance: float,
    risk_pct: float,
    sl_distance_price: float,
    contract_size: float,
    min_lot_step: float | None = None,
) -> float:
    """Lots that risk ``risk_pct`` of ``account_balance`` on a hit at SL.

    Args:
        account_balance: account equity in USD.
        risk_pct: 0..1 (e.g. 0.20 = risk 20% of account).
        sl_distance_price: price distance from entry to SL in quote-currency
            units (e.g. $20 means a $20/oz adverse move on XAUUSD).
        contract_size: units per lot (XAUUSD = 100 oz).
        min_lot_step: if given, floor the result to a multiple of this step.
            Use the broker's minimum tradable lot (e.g. 0.01 for IC Markets).

    Raises:
        ValueError: any non-positive numeric input or risk_pct > 1.
    """
    if account_balance <= 0:
        raise ValueError(f"account_balance must be > 0; got {account_balance}")
    if risk_pct < 0 or risk_pct > 1:
        raise ValueError(f"risk_pct must be in [0, 1]; got {risk_pct}")
    if sl_distance_price <= 0:
        raise ValueError(f"sl_distance_price must be > 0; got {sl_distance_price}")
    if contract_size <= 0:
        raise ValueError(f"contract_size must be > 0; got {contract_size}")

    if risk_pct == 0:
        return 0.0

    raw_lots = (account_balance * risk_pct) / (sl_distance_price * contract_size)

    if min_lot_step is not None:
        if min_lot_step <= 0:
            raise ValueError(f"min_lot_step must be > 0; got {min_lot_step}")
        steps = math.floor(raw_lots / min_lot_step)
        return steps * min_lot_step
    return raw_lots


def dollar_risk_for_lots(
    lots: float,
    sl_distance_price: float,
    contract_size: float,
) -> float:
    """Inverse of ``lots_for_risk``: the dollar loss on an SL hit at this size."""
    return lots * sl_distance_price * contract_size

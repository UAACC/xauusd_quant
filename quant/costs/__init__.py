"""Cost-modeling components: static spec, spread surface, swap calendar, slippage, commission.

Stage 1 (current): static spec + daily snapshot diffing.
Stage 1+: spread_surface, swap_calendar, slippage, tca.
"""

from quant.costs.spec import (
    SymbolDailySnapshot,
    SymbolStaticSpec,
    diff_snapshots,
    severity,
)

__all__ = [
    "SymbolStaticSpec",
    "SymbolDailySnapshot",
    "diff_snapshots",
    "severity",
]

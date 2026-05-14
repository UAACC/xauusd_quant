"""live_analysis — analyst-mode infrastructure.

Decoupled from the ``quant/`` research package. This package supports the
"副驾驶 / 裁判" workflow: evaluate candidate trades against the frozen
SMC BOS-reversal spec, scan for in-progress setups, log every decision
to a JSONL journal, and monitor open positions.

The strategy spec these tools enforce is frozen — see HANDOFF.md §8.
"""

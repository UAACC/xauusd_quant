# Live Analysis Project — Handoff for New Claude Sessions

**Last updated**: 2026-05-14 by Claude Opus 4.7 + user
**Read this FIRST** if you're a new Claude session picking up this project.

This is the **discretionary-analyst** project — distinct from the
**xauusd_quant** project (frozen BOS-reversal strategy backtest research,
documented in `HANDOFF.md`). The two share the same git repo but have
different scope, constraints, and authorizations.

---

## 0. Project identity & scope

```
Project:      live-analysis (discretionary analyst for live trading)
Account:      Live IC Markets MT5  (ICMarketsSC-MT5-2)
              Account #7989546, ~$6,003 CAD, leverage 1:400
Universe:     Default focus = XAUUSD + XAGUSD
              Also available: EURUSD, GBPUSD, USDJPY, AUDUSD, USDCHF, US500,
              USTEC, BTCUSD, ETHUSD, XPTUSD, XTIUSD, XBRUSD
              Note: USTEC min_lot 0.1 -- cannot trade at 0.02 ceiling
Direction:    LONG and SHORT both authorized
Constraint:   HARDCODED 0.02 lot per order (user's risk control)
Workflow:     Propose-and-confirm (I analyze → propose → user says "go" → I execute)
```

---

## 1. Code architecture

### Pure analysis library (`live_analysis/`)

```
analyst_tools.py     TA-Lib-backed utilities (8 functions):
                       zscore, std_distance_from_ma, range_position,
                       synthetic_dxy, vol_regime, bollinger_distance,
                       find_divergences, volume_profile

eval_trade.py        Multi-purpose:
                       - evaluate_trade() — rule-based candidate checker
                       - pull_live_context(symbol, **kwargs) — 7-TF + cross-asset snapshot
                       - scan_symbols(symbols, **kwargs) — batch multi-symbol pull
                       - load_recent_bars() — parquet fallback for offline

data_audit.py        Data quality verification:
                       - Spread distribution by hour-of-day
                       - M1 gap detection (excludes weekend + 22:00 rollover)
                       - ATR/ADX cross-check vs TA-Lib (caught: quant.structure.atr
                         is SMA-of-TR not Wilder, ~7-15% off — known bug,
                         not patched, xauusd_quant project decides)
                       - Cross-source: D1 closes vs Yahoo Finance equivalent
                         (MT5 XAGUSD vs Yahoo SI=F: 0.988 corr, -0.21% systematic bias)

execute.py           THE single live order entry point:
                       - place_order_safe() — HARDCODED 0.02 lot, no volume kwarg
                       - cancel_pending() — remove pending order
                       - close_position() — close open position, refuses if vol > 0.03
                       - All path through init_mt5_live(allow_orders=True)
                       - Auto-journals via live_analysis.journal on fill

journal.py           JSONL trade log:
                       - log_entry / log_exit / log_skip
                       - Strict validation (direction, lot ordering, SL/TP)
                       - File: live_analysis/data/trade_journal.jsonl (gitignored)

level_watch.py       Price-level cross alerter:
                       - Polls MT5 at configurable rate (1Hz default)
                       - Fires Windows beep + writes data/alerts.jsonl + stdout
                       - --live flag to connect to live MT5
                       - --no-beep, --cooldown, --status-every flags
```

### Supporting reused infra (from earlier xauusd_quant project)

```
scripts/mt5_connect.py
  init_mt5()                  — demo-only, hard-aborts on trade_mode==2
  init_mt5_live(allow_orders) — allows live, explicit flag tracked
  get_symbol_info(), build_cost_model()

scripts/live_monitor.py       10Hz tick monitor + alerts (XAUUSD default symbol)
scripts/pull_bars.py          Historical bar pull (legacy, demo)
scripts/run_symbol_analysis.py    Full 5-axis analysis for any symbol (uses live)
scripts/run_intraday_analysis.py  Intraday-focused analysis (sessions/VWAP/ORB)
scripts/verify_xagusd.py      Data verification utility (template for verify scripts)

quant.config.get_demo_mt5_path()   Demo terminal path resolver
quant.config.get_live_mt5_path()   Live terminal path resolver
                                     (Currently: same binary as demo on this machine,
                                      user switches account in-terminal to toggle)

quant.structure.*             SMC primitives: swings, BOS, retest, ATR, ADX, EMA,
                              FVG, trend, volume — all reusable for analysis
```

### Reports (generated content)

```
live_analysis/reports/        GITIGNORED — contains analysis output, position details
  Files named: <SYMBOL>_<YYYY-MM-DD>_analysis.md           (trade plans)
               <SYMBOL>_<YYYY-MM-DD>_trade-review.md       (post-mortems)
```

---

## 2. Tools usage cheatsheet

### Quick live snapshot

```python
from live_analysis.eval_trade import pull_live_context
ctx = pull_live_context("XAUUSD")  # default 7 TFs + 6 cross-asset
# ctx["bars"]["H4"]  — DataFrame
# ctx["live_spread_pts"], ctx["account_balance"], ctx["cross_asset"]["EURUSD"]
```

### Multi-symbol scan

```python
from live_analysis.eval_trade import scan_symbols
results = scan_symbols(["XAUUSD", "XAGUSD"])
# ~50-100ms warm, fastest way to scan watchlist
```

### Standard report

```bash
python scripts/run_symbol_analysis.py XAUUSD
python scripts/run_intraday_analysis.py XAGUSD
```

### Level watcher (run in background)

```bash
python -u -m live_analysis.level_watch \
    --symbol XAUUSD --level 4666 --side below \
    --live --interval 2 --cooldown 300 --status-every 120
```

### Execute order (after user confirms "go")

```python
from live_analysis.execute import place_order_safe
# Volume is HARDCODED 0.02 -- no kwarg
result = place_order_safe(
    symbol="XAGUSD", direction="long",
    entry=84.00, sl=83.30, tp=86.78,
    order_type="limit",
    rationale="Counter-trend bounce at VAL, M30 z=-3.06σ",
    setup_grade="B-discretionary",
    dry_run=False,  # set True to test plumbing
)
# Auto-journals on fill via journal.log_entry
```

### Verify data accuracy (run before high-conviction trade)

```bash
python scripts/verify_xagusd.py
# Or write a new verify_<symbol>.py modeled after it
```

---

## 3. User preferences (from memory)

Located in `~/.claude/projects/E--xauusd-quant/memory/`:

| Memory file | Key constraint |
|---|---|
| `feedback_communication.md` | Terse replies, paste code/formulas directly, no fundamentals |
| `feedback_git.md` | Per-repo git identity, no Co-Authored-By, no real email |
| `project_live_account.md` | $6,003 CAD live; XAGUSD/XAUUSD 0.02 lot OK; USTEC min_lot 0.1 |
| `project_analyst_mode.md` | Desk-analyst output expected (5-axis structure analysis) |
| `project_institutional_standard.md` | 5-axis decision framework reference |
| `project_focus_strategy.md` | XAGUSD + USTEC focus (deep, not broad) |
| `project_discretionary_analyst.md` | Multi-direction + multi-instrument scope |
| `feedback_candidate_response.md` | Immediate response on "我想 long/short X at Y" |
| `feedback_analyst_only.md` | NO risk management overlay (user researches separately) |
| `feedback_execute_authorization.md` | LIVE execution at 0.02 lot via execute.place_order_safe |
| `feedback_report_format.md` | All reports MUST follow XAGUSD_2026-05-14_analysis.md 14-section format |
| `reference_python_env.md` | Python 3.14 at C:\Python314, no venv, TA-Lib installed |

**Key behavior contracts**:
- Respond immediately when user asks "我想 long/short X at Y"
- Don't lecture about risk (user's 0.02 lot ceiling IS the control)
- Don't add invalidation rules / sizing math / management plan to reports (user
  researches these separately) — UNLESS user explicitly asks
- ALWAYS use the 14-section report format for "出一份报告" / "做分析"
- Use SHORT tactical format for "现在怎么看 X" intraday questions
- Save reports to `live_analysis/reports/` (gitignored)
- ASCII-only output (no unicode that breaks Windows GBK console)

---

## 4. Live execution boundaries (HARD limits)

```
✓ I CAN:
  - place_order_safe() at 0.02 lot (live or demo) after user says "go"
  - cancel_pending() and close_position() for orders I placed
  - Read live account state via init_mt5_live(allow_orders=False)
  - Auto-journal on fill

✗ I WON'T:
  - Send any order via mt5.order_send() outside execute.place_order_safe
  - Volume != 0.02 (hardcoded in execute.py, no kwarg)
  - Execute without explicit user "go" / "yes" / affirmation
  - Modify positions I didn't place (without explicit user request)
  - Stack multiple positions same-direction same-symbol without user OK
```

**Grep verify**: `mt5.order_send` only appears in `live_analysis/execute.py` and
`scripts/test_order.py` (the latter is demo-only via init_mt5 abort guard).

---

## 5. Current state (as of 2026-05-14 ~20:30 UTC)

```
Live account:    Active, 0 open positions, 0 pending orders
Last trade:      5/13 XAUUSD SHORT (failed, -$125.41) — see trade-review doc
Watching:        XAUUSD level 4666 below side (background process, will need
                 restart in new session — process doesn't persist across
                 sessions)

XAUUSD current:  ~$4,680 (was $4,684 morning, dropped to $4,667 then bounced)
XAGUSD current:  ~$84.76 (was $87.16 morning, dropped to $83.83 then bounced)
XAU/XAG ratio:   55.19 (was 53.81 morning — mean rev partly tracking)

Recent reports in live_analysis/reports/ (gitignored, may not exist after clone):
  XAUUSD_2026-05-13_trade-review.md   — failed XAU short post-mortem
  XAUUSD_2026-05-14_analysis.md       — 4 trade plans (morning data, stale)
  XAGUSD_2026-05-14_analysis.md       — 4 trade plans (the approved template)
```

---

## 6. Data accuracy reminders (audit findings)

```
1. Volume Profile uses tick_volume — proxy not actual CME contract volume
2. Synthetic DXY: directional only, absolute value is dimensionless
3. Cross-source: D1 verified vs Yahoo (XAGUSD ~0.21% bias, XAUUSD ~0.01%)
   Intraday (H4/H1/M15) NOT cross-source verified — trust MT5 broker
4. quant.structure.atr is SMA-of-TR, NOT Wilder smoothing (~7-15% diff vs
   TA-Lib reference). Bug acknowledged, not patched (xauusd_quant project).
   Use talib.ATR directly in this project for accurate ATR.
5. fractal n=2 swings need 2 bars AFTER for confirmation -- most recent
   2 H4 bars cannot have confirmed swing yet
6. Spread P95 caps at ~45pt XAGUSD (broker policy, not market truth)
7. Daily 22:00 UTC ~1h rollover gap is expected, not a feed bug
```

---

## 7. New session quickstart

```bash
# 1. Clone repo
git clone https://github.com/UAACC/xauusd_quant.git
cd xauusd_quant

# 2. Verify Python env
python -c "import talib, MetaTrader5, yfinance, pandas, numpy; print('OK')"
# Should print "OK" (deps: TA-Lib, MetaTrader5, yfinance, pandas, numpy, pyarrow, pytest)

# 3. Verify MT5 connection (account currently logged into the terminal determines mode)
python -c "from live_analysis.eval_trade import pull_live_context; \
ctx = pull_live_context('XAUUSD', cross_asset_symbols=[]); \
print('balance:', ctx['account_balance'], 'spread:', ctx['live_spread_pts'])"

# 4. Read user memory
ls ~/.claude/projects/E--xauusd-quant/memory/
# Read MEMORY.md and at least the feedback_* entries

# 5. Run tests
python -m pytest
# Should see 200+ passing

# 6. If user has open trade or wants intraday read, use:
python scripts/run_symbol_analysis.py XAGUSD
# Or for intraday-focused:
python scripts/run_intraday_analysis.py XAUUSD
```

---

## 8. Standard workflow for a trade

```
1. User asks "我想 long/short X at Y" OR "现在 X 该不该 做"
2. Run pull_live_context(X) — fresh snapshot ~35ms warm
3. Compute multi-TF + volume profile + divergences + cross-asset + categories
4. IF user asked for full report: write to live_analysis/reports/ using
   the 14-section template (see feedback-report-format memory)
5. IF user asked for tactical: short response with 2-3 immediate plays
6. Propose entry/SL/TP per plan
7. User says "go A" / "go B" / etc. → execute via place_order_safe
8. Verify fill, auto-journal logged
9. Set level_watch on critical levels for management
10. Stand by for trigger / new ping
```

---

## 9. Known unresolved items

```
- Risk-management framework: user is researching separately. Don't include
  sizing math / invalidation rules / management plan in reports unless
  user explicitly asks (per feedback-analyst-only).
- USTEC: cannot trade at 0.02 lot ceiling (min_lot 0.1). Either de-scope
  USTEC from focus or escalate ceiling for that symbol only.
- 3-tier regime alerter from morning backtest (precision 21-24%): NOT
  deployed as auto-fire signal. Useful as watchlist promoter only.
- Intraday cross-source (H4/H1/M15 vs independent source): NOT verified.
  Yahoo doesn't have free intraday data. Trust MT5 with awareness.
- Multi-symbol parallel watcher: only XAUUSD has level_watch running.
  Other symbols need separate watcher processes.
```

---

## 10. If user asks something not covered here

```
Most common asks (with response pattern):
  "现在 X 怎么看 intraday" → short tactical (3-5 lines + 2 plays)
  "出一份 X 报告" / "做分析" → full 14-section report saved to reports/
  "复盘这笔" + screenshot → trade post-mortem variant
  "扫一下当前 setups" → scan_symbols + ranked candidates
  "我想 long/short X at Y" → propose-and-confirm with R:R calc
  "go A" / "go B" → execute via place_order_safe at 0.02 lot
  "cancel" / "close" → cancel_pending / close_position with confirm

When in doubt:
  - Honest about data caveats
  - Verify before high-conviction recommend (run scripts/verify_<symbol>.py pattern)
  - Use full 14-section format unless time-pressured
  - Save reports to live_analysis/reports/ (gitignored)
  - ASCII output only
```

---

## 11. File map

```
xauusd_quant/                          repo root
├── HANDOFF.md                         xauusd_quant project (separate)
├── live_analysis/                     this project
│   ├── LIVE_ANALYSIS_HANDOFF.md      ← you are here
│   ├── analyst_tools.py               TA-Lib utilities
│   ├── eval_trade.py                  pull_live_context, scan_symbols, evaluate_trade
│   ├── data_audit.py                  data quality verification
│   ├── execute.py                     THE order execution entry point
│   ├── journal.py                     JSONL trade log
│   ├── level_watch.py                 price-level alerter
│   ├── data/                          gitignored (alerts.jsonl, trade_journal.jsonl)
│   ├── reports/                       gitignored (analysis output, post-mortems)
│   └── tests/                         185+ tests, all passing
├── scripts/
│   ├── mt5_connect.py                 init_mt5, init_mt5_live, build_cost_model
│   ├── live_monitor.py                10Hz tick monitor
│   ├── pull_bars.py                   historical bar pull
│   ├── run_symbol_analysis.py         full 5-axis analysis
│   ├── run_intraday_analysis.py       intraday VWAP/ORB analysis
│   └── verify_xagusd.py               data verification (template)
├── quant/                             frozen for xauusd_quant project
│   ├── config.py                      get_demo_mt5_path, get_live_mt5_path
│   ├── data/parquet.py                bar/tick I/O
│   ├── strategies/bos_reversal.py     frozen strategy (xauusd_quant only)
│   └── structure/                     SMC primitives (reusable here)
├── data/                              gitignored (bars, ticks, alerts)
└── tests/                             quant package tests (116, all passing)
```

---

**End of handoff. If anything is unclear, the user is the source of truth.**

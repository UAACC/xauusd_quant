# XAUUSD CFD 量化项目 — 交接文档

> 把这份文档完整粘给新机器上的 Claude Code 当第一条消息，就能接上之前的进度。
> 最后更新：**2026-05-13 (Wednesday Calgary 早 / UTC 中午)** by 用户 + Claude Opus 4.7

---

## 1. 当前状态快照

- **阶段 0 完整闭环 ✅**：环境、连接、读路径、写路径、demo 实盘单全部跑通
- **阶段 1 完整闭环 ✅**：数据基础设施 + broker timezone robustness + cost model 校准 + BB 教学 baseline
- **阶段 2 SMC 策略 MVP ✅**：7 个 structure primitives + 8-stage 状态机 + event-driven backtest engine + risk-based sizing
- **阶段 2 第二波 ✅ 朋友 3 个 validation 全部落地**：
  - 追问 #1 (Jan 16 missed) → 是 trend-pullback-buy 策略, 不同 setup, 我们 filter 正确, 无需改代码
  - 追问 #2 (1-bar gap false positive) → `min_h4_bars_capitulation_to_bos = 3` 守卫已部署 (commit a91aecf)
  - 追问 #3 (HH 必须低于 LH 但高于前 LH) → micro-CHoCH H1 stage 3b 已部署 (commit 8209503)
  - **116 个单元测试全绿**（111 + 5 个 micro-CHoCH 专用 case）
- **阶段 2 第三波 ✅ 4 年回测 + ablation + walk-forward** (commit 1407893)：
  - **15 个 A 级 setup**（从 baseline 29 减半 — 14 个 V 反弹被新 filter 杀掉）
  - **per-trade edge $11.88**（比 baseline $9.54 提升 25%）
  - **Sharpe (Mode B fixed 0.01) = 0.81**, Mode C (2% risk) = 0.79, Mode A (20%) = 0.68
  - **Win rate 46.7%** (7 TP / 5 SL / 3 BE)
  - 2025 年完全无信号（gold 上涨年, BOS-reversal 不适用 — 这是好事, filter 正确拒绝）
- **🚨 Walk-forward (6-month 滚动 8 窗口) 暴露关键问题**：
  - 50% windows 零信号（连续 18 个月无交易: 2024-09→2026-03）
  - **75% 总盈利来自 2023-09..2024-03 一个窗口**（regime concentration risk）
  - In-sample $118.68 / Out-sample $39.88（OOS 只 1 笔成交, 统计意义弱）
  - **结论 (mindset 修正)**：这是 **rare-event regime-dependent strategy**, 不是稳定 income generator。应该和别的策略组合用 (覆盖 trending-up/ranging regime), 或者把它当 occasional high-conviction play
- **Ablation 发现** (run inline 5/13, 不需要重跑)：
  - baseline 29 → only min-gap 20 → only micro-CHoCH 15 → both 15
  - **min-gap 完全冗余**（所有它能杀的, micro-CHoCH 也能杀）
  - 保留作为 cheap fast-path early-exit, 不删
- **Repo**：https://github.com/UAACC/xauusd_quant ，main 分支
  - identity: per-repo `UAACC` / `61613205+UAACC@users.noreply.github.com`
  - **不带** `Co-Authored-By: Claude` trailer
- **本地 vs origin**：本地 main == origin/main 同步（5/13 所有 commits 已 push）
- **5/12-5/13 commit 历史（按时间倒序）**：
  ```
  1407893  feat(backtest): walk-forward harness for OOS analysis
  8209503  feat(bos_reversal): micro-CHoCH H1 stage 3b — structural filter
  a91aecf  fix(bos_reversal): min-gap guard rejects oversold-bounce false positives
  103e807  docs: HANDOFF rewrite for 5/12 EOD
  19ee5e4  feat(backtest): fixed-lot mode + Sharpe + 3-mode analytics script
  45e2cd1  fix(bos_reversal): dedup overlapping signals + cap capitulation->BOS staleness
  5c1a2d0  feat(smc): end-to-end BOS-reversal MVP
  c3e90fe  feat(structure): SMC primitives second wave - trend, BOS, retest
  bb9fa39  feat(structure): SMC primitives - swings, volume surge, FVG, EMA
  b3cbddf  fixture: XAUUSD daily spec snapshot 2026-05-12
  d24c631  feat(config): multi-machine MT5 path resolver + uv migration
  ```

## 2. 项目背景（不要重新讨论的事项）

- **券商**：IC Markets，**Raw Trading Ltd / Seychelles 实体**（`ICMarketsSC-MT5`）
  - demo 账户已开（`ICMarketsSC-Demo`）
  - 用户 live 账户也在 Raw Trading 跑一个 EA — **绝对不要碰**
- **执行平台**：MT5；算法在 MQL5 EA，研究/回测在 Python
- **研究方向**：波动率/统计套利/SMC，**不**走纯趋势跟踪、ML、事件驱动作为起点
- **质量标准**：institutional-grade engineering as feasible for retail single-trader
  - tick-level data infra
  - empirical cost surfaces (NOT 单点常数)
  - reproducible spec snapshots committed to repo
  - walk-forward + nested CV
  - structured order logs for TCA
  - parquet/pyarrow data lake

## 3. 整体路线图

| 阶段 | 目标 | 状态 |
|---|---|---|
| 0 | MT5 + IC Markets 接通，demo 跑通第一笔 | ✅ 完成 |
| 1 | tick/bar 数据管道 + broker time + cost model | ✅ 完成 (qa/spread_surface/swap_calendar 后续可选优化) |
| 2 | Alpha 研究 — SMC/BOS reversal 策略 | ✅ 策略代码完整 + filter 跟朋友 mental model 对齐 + 4 年 backtest + walk-forward。**结论：rare-event regime-dependent，单独用不够，需要 ensemble** |
| 3 | MQL5 EA 实现：把 OOS 通过的策略翻译成 EA | 未开始 |
| 4 | 风控层：仓位 sizing、止损、最大回撤熔断、新闻过滤 | 部分（sizing 已做，max DD 熔断 / news blackout 未做） |
| 5 | 实盘：模拟 → 小资金 → 放量 | 未开始 |

## 4. 已建好的文件清单

### Python package (`quant/`)
```
quant/
├── __init__.py
├── config.py                 # get_demo_mt5_path() 多机路径解析
├── costs/
│   ├── __init__.py
│   └── spec.py               # SymbolStaticSpec, SymbolDailySnapshot, diff + severity
├── data/
│   ├── __init__.py
│   ├── parquet.py            # tick + bar partitioned parquet I/O
│   └── broker_time.py        # BrokerClock + discover_clock; zoneinfo
├── structure/                # SMC primitives (7 个模块)
│   ├── __init__.py
│   ├── swings.py             # 5-bar fractal swing H/L
│   ├── volume.py             # rolling-median × N 倍爆量检测
│   ├── fvg.py                # 3-bar Fair Value Gap (bullish/bearish)
│   ├── ema.py                # EMA + 距离 confluence helper
│   ├── trend.py              # count_lower_lows/highs + is_downtrend/uptrend
│   ├── bos.py                # first_close_above/below + n_consecutive_closes_above/below
│   └── retest.py             # first_low_within_pct + first_high_within_pct
├── strategies/
│   ├── __init__.py
│   ├── bb_mean_revert.py     # BB 教学 baseline (已证实效果差)
│   └── bos_reversal.py       # 8-stage SMC 状态机, 含 dedup + capitulation 过期 cap
├── risk/
│   ├── __init__.py
│   └── sizing.py             # lots_for_risk: risk-based + min-lot-step floor
└── backtest/
    ├── __init__.py
    ├── engine.py             # close-only vectorized (BB 用)
    └── event_engine.py       # intrabar SL/TP/BE + 多模式 sizing + Sharpe
```

### Scripts (`scripts/`)
```
mt5_connect.py     # MT5 连接 + spec 拉取 + CostModel; trade_mode==2 (REAL) abort 守卫
snapshot_spec.py   # 每日 spec snapshot + diff alert
pull_ticks.py      # 历史 tick 按天拉, partitioned parquet
pull_bars.py       # 多 timeframe 历史 bar
live_monitor.py    # 10 Hz 实时 tick 监控 + 告警 + 每 5s flush parquet
test_order.py      # 0.01 lot round-trip slippage 测试 (--execute 才发单)
backtest_bb.py     # BB 策略回测 (教学)
backtest_smc.py    # SMC 策略端到端入口 — 加载 H4+H1+M15, 检测信号, 跑 event engine, 出 ledger
analyze_smc.py     # 3 模式横向对比 (raw / 2% risk / 20% risk) + 年度切片 + equity curve
walk_forward_smc.py # 6/12-month 滚动窗口分析, 检测 edge 是否稳定 vs 集中 (rare-event 诊断)
```

### Tests (`tests/`)
```
tests/
├── conftest.py               # smc_h4_apr_may fixture (load Apr-May 2026 H4 from parquet)
├── test_swings.py            # 12 tests
├── test_volume.py            # 9 tests
├── test_fvg.py               # 8 tests
├── test_ema.py               # 11 tests
├── test_trend.py             # 15 tests
├── test_bos.py               # 14 tests
├── test_retest.py            # 10 tests
├── test_sizing.py            # 9 tests
├── test_bos_reversal.py      # 10 tests (含 dedup + capitulation expiration)
└── test_event_engine.py      # 10 tests (含 SL/TP/BE 各种边界, balance compounding)
                              # 全套 108 tests 跑过 0.3s
```

### 数据与 fixtures
```
data/                                                  # gitignored 数据湖
├── ticks/XAUUSD/2026/05/<YYYY-MM-DD>.parquet         # 日级 tick (5/4-5/11 已采)
├── bars/XAUUSD/{H4,M15,M5,M1}/<YYYY>/<YYYY-MM>.parquet
│   # H4: 2021/6 - 2026/5 (60 个月, ~7800 bars)
│   # M15: 2022/2 - 2026/5 实际可用 (broker history limit, 早期是 1-bar 占位符)
│   # M5: 2022/2 - 2026/5
│   # M1: 2026/3 - 2026/5 (只拉了近 3 个月)
└── alerts.jsonl

fixtures/                                              # tracked, public-safe
├── spec/XAUUSD_2026-05-10.json
├── spec/XAUUSD_2026-05-11.json
└── spec/XAUUSD_2026-05-12.json                        # 5/12 snapshot, 仅 swap_long/short 微调
```

## 5. 关键技术决策与陷阱（必须知道）

### 5.1 MT5 Python 时区陷阱（CRITICAL）
**MT5 Python 包返回的 `time` / `time_msc` 不是真 UTC**，是 broker 本地墙钟当 Unix epoch 用。

- IC Markets MT5 server tz = **Europe/Athens** (EEST UTC+3 夏 / EET UTC+2 冬)
- 直接用 `pd.to_datetime(ts, unit='s', utc=True)` 是**错的** — 标了 UTC 但其实是 broker time
- 修复在 `quant/data/broker_time.py`：
  - `discover_clock(mt5, server_name, symbol)` — 运行时探测 broker tz + 实测对照 IANA 假设
  - `broker_msc_to_utc_msc(broker_msc)` — 用 `zoneinfo` 转换（DST 自动处理）
  - `utc_dt_to_mt5_query(utc_dt)` — 转给 MT5 查询用（**绝对不要 naive datetime**）

**所有新写入的 parquet 存真 UTC**，schema metadata 标记 `time_convention=utc`。
**Legacy 警告**：`mt5_connect.py:get_history_m1()` 早期 demo 路径还有 broker-time-as-UTC bug，产出的 CSV 时间标错（gitignored，丢了无伤但要警惕）。`pull_ticks/pull_bars/live_monitor/snapshot_spec` 都走 BrokerClock，是干净的。

### 5.2 Dual-MT5 隔离 + 多机器路径解析
用户有两个独立 MT5 安装：
- **Live MT5**（用户自己 EA 跑的，**绝对不要碰**）— 路径不知，他自己用
- **Demo MT5** — 路径因机器而异

**多机器路径用 `quant.config.get_demo_mt5_path()` 解析**（don't hardcode）：
1. 优先读 env var `XAUUSD_DEMO_MT5_PATH`
2. 否则按 `_KNOWN_DEMO_MT5_PATHS` 顺序找第一个存在的：
   - `F:\demo-mt5\terminal64.exe`（旧机器, repo 在 `E:\xauusd_quant`）
   - `C:\Program Files\MetaTrader 5\terminal64.exe`（当前机器, repo 在 `W:\xauusd_quant`）
3. 都不存在 → `FileNotFoundError`（loud-fail）

加新机器：在 `quant/config.py` 的 `_KNOWN_DEMO_MT5_PATHS` 前面加路径，**不要删** 已有项；或者直接在那台机器上设 env var。

`mt5_connect.init_mt5()` 里有 `trade_mode == 2 (REAL) abort` 守卫做次级防御，**不要拆**。

### 5.3 IC Markets XAUUSD 校准值（2026-05-12 verified）
```
contract_size       100 oz/lot
point / digits      0.01 / 2
tick_size / value   0.01 / $1 per lot per point
spread_mode         floating (典型 P50 = 9-10 pts 活市, 5 pts 当前亚盘)
filling_mode        IOC
stops_level         0  (无最小 SL/TP 距离限制)
commission          $3.5/lot/side, in/out → $7/lot 全程  ← Seychelles 实体, NOT 免佣
swap_long           -55.085 pts → -$55.09/lot/night     (2026-05-12 reprice from -56.80)
swap_short          +46.595 pts → +$46.60/lot/night     (2026-05-12 reprice from +48.82)
swap_3day_index     3 (Wed 三倍 swap)
swap_mode           1 (POINTS)
trading_hours       Mon-Thu 01:00-23:59 server, Fri 01:00-23:57
weekly_open         Monday 01:00 broker EEST = Sunday 22:00 UTC
                    = Sunday 16:00 MDT Calgary  ← 用户在 Calgary
```

### 5.4 SMC 回测用的 cost model
当前 `event_engine.py` 把 round-trip 成本设为 **$12 / lot** 常数（spread $5 + commission $7，按 5pts spread 算的 lower bound）。**实际历史均值更接近 $14-16 / lot**（spread P50 ~9pts × $1 + $7 commission）。这意味着回测稍微低估了成本，下一步把 cost 接到经验 spread surface 上能让结果更真。

### 5.5 不要 hardcode 任何时区偏移
**绝对不要**写 `subtract 3 hours` 这种代码。任何时区相关运算必须走 `BrokerClock` 或 `zoneinfo`。

### 5.6 修过的 bug（不要再踩）
- `BrokerClock.utc_dt_to_broker_naive` 之前返回 naive datetime，在非-UTC 系统上 `.timestamp()` 用本地 tz 解释 → 9 小时错位 → MT5 查询 0 ticks。已改为 `utc_dt_to_mt5_query()`。
- `mt5_connect.py:DATA_DIR` 之前用 `Path.home()`，已改为 `Path(__file__).resolve().parent.parent / "data"`。
- `.gitignore` 之前 `data/` 太泛，已改为 `/data/` 锚根。
- `live_monitor.py` 的 final flush 漏传 clock 参数。
- `mt5_connect.py:filling_mode` 报告的 "filling_mode: 2" 是 bitmask，不是 order request 用的常量（`ORDER_FILLING_IOC=1`）。
- `bos_reversal.py` 早期版会重复 emit 同一笔（同 entry_time / entry_price 不同 capitulation_time），已加 dedup。
- `bos_reversal.py` 早期版会让旧 capitulation（>2 周前）回死灰复燃做 BOS，已加 `max_h4_bars_capitulation_to_bos=12` 默认 cap。

## 6. 用户偏好（必读，影响所有交流）

- **回复要简洁**，不要长篇说教
- 用户**懂金融数学和编程**，但**对操作型量化术语生疏** — 第一次出现专业词汇（slippage 测量方法 / spread distribution / FVG / BOS / Wyckoff / Sharpe / raw edge / Kelly）需要简短解释，**之后默认理解**
- 不要 timing-conservative — 不要建议 "等理想时段再做"，他要执行
- 不要数据挖掘 / 优化 — 拒绝 "我们调参数让 sharpe 更高" 的诱惑
- 不要承诺夏普 3+ / 月化 10%+ / 90% 胜率，那都是骗子话术
- 用户在 **Calgary**（MDT = UTC-6 夏令时） — 涉及具体时段建议时用 Calgary 时间
- **不要把真实邮箱写进 repo 任何文件** — 用 GitHub noreply 别名
- **不要把账号 / 密码写进 chat 或 memory** — 即使 demo 账号也建议匿名化
- 如果用户说话术不清，**直接问澄清** — 不要假设
- 工具偏好 **uv**（不是 pip 不是 conda），见 memory `feedback_python_tooling.md`

## 7. 阶段 1 + 2 已完成 vs 待做

### ✅ 已完成（截至 2026/5/13 EOD）

**阶段 1（数据基础设施）**：
- tick/bar partitioned parquet (5 年 H4/H1/M15 + 3 月所有 TF)
- BrokerClock + zoneinfo timezone robustness (Europe/Athens)
- live_monitor 10 Hz polling + alerts + persistence
- snapshot_spec 每日 audit 入 fixtures/

**阶段 2（SMC 策略）**：
- 7 个 structure primitives (`quant/structure/`)
- 8-stage 状态机 `bos_reversal.py`（含 min-gap + micro-CHoCH 两个 V 反弹 filter）
- Event-driven backtester `event_engine.py`（intrabar SL/TP/BE + 多模式 sizing + Sharpe）
- Risk-based sizing `quant/risk/sizing.py`
- 朋友 3 个 validation 问题全部答完 + 落地代码
- 4 年 backtest + 3 模式分析 + walk-forward 8 窗口分析
- **116 个单元测试全绿**

**阶段 2 关键发现（必读）**：
- baseline 29 → 加 micro-CHoCH 后 15（半数 V 反弹被过滤）
- per-trade edge 提升 $9.54 → $11.88 (+25%)
- Sharpe 0.79 (Mode C 2% risk) — 真实但弱 edge
- Walk-forward 暴露 rare-event 性质：50% 窗口零信号、75% 盈利集中在 1 个窗口
- 2025 整年零信号是 correct behavior（gold 上涨年, BOS-reversal 不适用）
- min-gap 是 micro-CHoCH 的 subset, 当前数据上完全冗余, 保留作 fast-path

### 🚧 待做（按优先级；新 Claude 接手优先看这块）

**P0 — 接受 strategy 已收尾, 重新定位**
策略代码层 spec 完成. 接下来不再"调 SMC 让它更好" — 数据明确告诉我们它是 rare-event regime-dependent 形态. 下一阶段是 **ensemble / 多策略组合**.

**P1 — 移植到 NQ（最高 ROI 扩样本）**
- 朋友 Q12: NQ 同套参数也用
- 需要 IC Markets symbol：先查 `mt5.symbol_info("NAS100")` / `US100`
- 改动：pull_bars 加 symbol 参数, cost model 重算 (NQ 不是 100 oz/lot)
- 期待：如果 NQ 也有类似 win rate, 信号频率翻倍, 同样 strategy 可以跑两个 symbol

**P2 — 加一个互补策略覆盖其他 regime**
- SMC BOS-reversal 只抓 trending-down 转折
- 需要：trend pullback buy (朋友 Jan 16 missed setup 的策略类型)
- 或者：range-bound mean reversion (针对横盘期)
- 或者：carry-aware short bias（用 swap 正 carry $48/night）
- 目标：3 个互补策略组合，每个覆盖一个 regime

**P3 — 操作层（不依赖市场数据，可并行）**
- `quant/risk/circuit_breaker.py`：max_daily_loss + max_drawdown 强制停盘
- `quant/risk/news_blackout.py`：CPI/NFP/FOMC 前后窗口屏蔽 entry（朋友 Q9）
- `quant/risk/concurrency.py`：max-1-position 过滤器（朋友 Q14: 听大周期）

**P4 — 空头 setup**
- 镜像逻辑, 要求 multi-bar volume surge（黄金偏多, 朋友 Q13）
- 加到 `bos_reversal.py`

**P5 — 经验 spread surface**
- `quant/costs/spread_surface.py`：从历史 tick 算 spread 分布 by (weekday, hour)
- 接入 event_engine 替代 $12 常数

**P6 — 长期 backlog**
- `quant/data/qa.py`: gap detection / spread anomaly / weekend boundary
- MT5 EA implementation (阶段 3) — 当有 ensemble + 验证 OOS 后再做
- 真实 demo paper trading: 让 live_monitor 持续采集, 每周自动跑 detector 看是否对得上手工执行

## 8. SMC/BOS reversal 策略 — 完整规格

### 8.1 朋友的 15 题答案（最终确认版）
1. **入场点**：等回踩 + 守住 1-2 根 M15 后市价进
2. **SL**：固定 $20（价格距离）
3. **TP**：固定 $40（不再用 FVG 自动找；4/12 用户简化决策）
4. **EMA**：M1/M5 EMA20/50 当 confluence 参考（state machine 当前未用，留给后续）
5. **回踩 deadline**：BOS 后 2 根 H4 不回踩 → 放弃
6. **Invalidation**：H4 close 跌破前低 OR H4 close 跌回 LH 下方 → 放弃
7. **趋势定义**：H4 出现 3 个 LL events（不是 strict chain，朋友说"出现 3 个"= count 不是 chain）
8. **Mid-trade SL**：浮盈 +$15 → SL 移到 BE；不做 trailing；路径有小阻力可以 50% 平
9. **News blackout**：重要数据公布后 1 小时内不交易；高级别数据前一天可能也不
10. **仓位**：单笔最多损失 20% 账户（risk-per-trade 不是 margin）
11. **Volume**：当前 K 线 ≥ 前 20 根 H4 中位数 × 1.4
12. **品种**：XAUUSD + NQ 同套参数
13. **空头**：镜像逻辑但要求多根 bar 连续爆量（黄金偏多市场）
14. **多 setup 冲突**：听大周期
15. **B 级 setup**：直接拒；BOS+FVG+整数位 confluence 回踩可加仓

### 8.2 8-stage 状态机（已实现）
```
Stage 1  Trend     H4 ≥3 LL events AND ≥3 LH events in last 6 swings of each kind
Stage 2  Capit.    H4 swing low + tick_volume ≥ 1.4 × median(prior 20 H4 bars)
Stage 3  BOS       first H4 close > prior LH (within 12 H4 bars of capitulation)
                   + M15 sustains 2 consecutive closes above LH
Stage 4  Wait      max 2 H4 bars after BOS; abandon if H4 close < LH or < prior swing low
Stage 5  Retest    M15 low within 0.5% of LH (default tolerance)
Stage 6  Hold      M15: 2 consecutive closes above LH after retest
Stage 7  Entry     market BUY at close of 2nd hold bar
                   SL = entry − $20;  TP = entry + $40
                   sizing = 0.20 × balance / ($20 × 100)  [risk-based, see quant/risk/sizing.py]
Stage 8  Manage    BE move at +$15 profit; no trailing; partial close not implemented
```

### 8.3 4 年回测的关键数字（reference）

**Baseline (filter 上线前, 29 signals)**:
```
样本: 2022-03-01 → 2026-05-12 (50 个月, ~6500 H4 bars, ~99k M15 bars)
信号: 29 笔 A-grade long-only (空头未实现)
TP/SL/BE: 12/10/7
胜率 41.4%  (vs breakeven 33.3% at R:R 2:1)
Per-trade raw edge: $9.54 / 0.01 lot
年度: 2022 +$98 / 2023 +$118 / 2024 +$39 / 2025 -$20 / 2026 +$39
信号频率: 9 → 12 → 4 → 2 → 2 (按年)
```

**Post-filter (min-gap + micro-CHoCH 上线后, 15 signals — 当前 production state)**:
```
信号: 15 笔 A-grade long-only (14 笔 V 反弹被 filter 杀掉)
TP/SL/BE: 7/5/3
胜率 46.7%  ← 比 baseline 41.4% 略升
Per-trade raw edge: $11.88 / 0.01 lot  ← 比 baseline +25%

3 模式对比:
                  Net P&L     Max DD     Sharpe
Mode A (20%)      +$23,901   -$6,125    0.68     ← +239%, 710 天水下
Mode B (0.01 lot) +$178      -$40       0.81     ← raw edge $11.88/笔
Mode C (2%)       +$1,742    -$402      0.79     ← 现实可执行 sizing

年度 (固定 0.01 lot):
  2022  5 trades  40% win   net +$39
  2023  6 trades  50% win   net +$79  ← 最佳年
  2024  2 trades  50% win   net +$20
  2025  0 trades  (gold uptrend year, 策略正确不开仓)
  2026  2 trades  50% win   net +$40  (含 May 6)

Ablation:
  baseline 29   → only min-gap 20   → only micro-CHoCH 15   → both 15
  min-gap 完全是 micro-CHoCH 的子集 (但保留作 fast-path early-exit)
```

**Walk-forward (6-month 滚动 8 窗口, fixed 0.01 lot)**:
```
window                  n  win%  net      sharpe
2022-03..2022-09        2   0%  -$20.24   -3.09
2022-09..2023-03        5  40%  +$19.40   +0.44
2023-03..2023-09        0   -    $0        0
2023-09..2024-03        4  75% +$119.52   +6.54  ← 75% of total 集中于此
2024-03..2024-09        1 100%  +$39.88    0
2024-09..2025-03        0   -    $0        0    ← 沉睡
2025-03..2025-09        0   -    $0        0    ← 沉睡  
2025-09..2026-03        0   -    $0        0    ← 沉睡

In-sample (前 4): +$118.68   positive 2/4
Out-sample (后 4): +$39.88   positive 1/4   ← 仅 1 笔成交, 统计意义弱
median net per window: $0    ← 一半窗口零机会

诊断: rare-event regime-dependent strategy. 不是稳定 income generator. 
75% 盈利来自 1 个窗口 (2023-09..2024-03, 明确 trending-down regime).
2025 整年零信号 = 上涨年没有"下跌通道+capitulation"形态, filter 正确拒绝.
```

### 8.4 还原忠实度
朋友描述的 May 6 setup 在我们数据上完整复现：
- Capitulation 5/4 13:00 UTC 周一早 7:00 Calgary，vol 2.91× median, 破前低 -1.31%
- BOS 5/6 05:00 UTC 周二晚 23:00 Calgary，H4 close 4700 突破 LH 4660
- M15 retest 4644 (LH 下方 $16)，2 根 hold
- Entry 5/6 06:45 UTC 周三凌晨 0:45 Calgary @ 4669.26
- TP 命中 5/6 10:30 UTC 周三凌晨 4:30 Calgary @ 4709.26
- 净赚 $3,151 (Mode A) / $39.88 (Mode B 固定 0.01 lot)

朋友的描述价位（4917, 4673, 4510 等）跟我们数据（4891, 4660, 4500 等）差 10-30 美元——broker quote noise，结构 100% 一致。

## 9. 重要 mindset / 已对齐的认知

### 9.1 Sample size > strategy tweaks
N=4 (12 月) → 不能下任何结论。N=29 (50 月) → 仍在统计边缘，置信区间宽 (Sharpe 95% CI [0.51, 1.37])。**真正能下结论需要 N=100+**。所有"调参看回测好不好"都是 multiple-comparison 陷阱。

### 9.2 Raw edge vs 复利收益
- Mode A 名义 +484% 几乎全部是复利效应，**不是策略变强了**
- Strategy 真实 edge 是 raw $9.54 / 笔（弱但真实）
- 复利 + 大仓位 = 心理地狱（21 个月水下，零售扛不住）

### 9.3 超 Kelly 的 volatility drag
- 20% 仓位 Sharpe **比** 2% 仓位 Sharpe **低**（0.60 vs 0.92）
- 不是仓位越大越好；超 Kelly 的方差损耗是真实数学
- 朋友选 20% 是高 R:R 假设下的合理 Kelly（5:1 时 20% ≈ 半 Kelly）；但回测实测 R:R 落到 ~2.7（被 BE 拉低了），20% 已经过 Kelly 了

### 9.4 高胜率不等于赚钱
- 经典陷阱：高胜率 EA 通常是紧 TP + 宽 SL（赚 $1 输 $20 → -EV）
- 真正决定盈利的是 EV per trade after costs
- 用户已被教育，不要再被"90% 胜率剥头皮 EA"话术忽悠

### 9.5 不要参数优化
- 67 天样本 + 9 组 BB 参数 → 最好的 BB(20,1.5) gross +$828 但 all-in -$312
- 数据挖掘陷阱（multiple comparison）— 100 组里挑出来的 sharpe 2 在 OOS 上必死
- 永远不要"调参数让回测好看"
- 唯一合理的"调参"是 walk-forward 框架内 + nested CV，**不是 grid search**

### 9.6 现实 sharpe / 胜率 benchmark
```
机构买方:   Sharpe 1.5-2.5, 年化 15-25%, 回撤 8-15%
顶尖零售:   Sharpe 1.0-1.5, 年化 10-20%, 回撤 10-20%, 胜率 45-60%
现实零售:   Sharpe 0.3-0.8, 年化 5-12%, 回撤 15-25%   ← 我们 raw Sharpe 0.94 在这区间高端
零售平均:   长期 -2% 到 -5%/年, 1-2 年破产               ← 95% 零售
```

### 9.7 真实的研究流程
```
1. 提出假设 (有经济解释)             ← 朋友的 SMC narrative
2. 设计实验 + null hypothesis        ← 我们的 8-stage state machine
3. 跑数据                            ← ✅ 50 月 backtest 跑了
4. 统计显著性 (t-stat > 2.5, 不是看 sharpe)  ← 还没正式做; N=29 不够
5. OOS 验证 (hold out 30%)            ← 还没做
6. Walk-forward (rolling)             ← P3
7. 风控加成 (sizing / SL / blackout / circuit breaker)  ← 部分 (sizing 做了)
8. Paper trade 1-3 月                ← 未开始
9. Small live ($1-5k) 1-3 月          ← 未开始
10. Scale or kill                    ← 未开始
```
**90% 假设死在 step 4-5。我们到 step 3 + 朋友 validation 中。**

## 10. 立即可执行的下一步（按 ROI 排序）

```powershell
# 1. (新机器) 克隆 + 装环境 + smoke test
git clone https://github.com/UAACC/xauusd_quant.git
cd xauusd_quant
uv venv
uv pip install -r requirements.txt
git config user.name "UAACC"
git config user.email "61613205+UAACC@users.noreply.github.com"

# 验证基础设施
.\.venv\Scripts\python.exe -c "from quant.config import get_demo_mt5_path; print(get_demo_mt5_path())"
.\.venv\Scripts\python.exe -m pytest tests/ -q   # 应该 108 passed

# 2. (如果需要拉数据) 数据本来就 gitignored, 新机器要从 0 拉
.\.venv\Scripts\python.exe scripts/snapshot_spec.py
.\.venv\Scripts\python.exe scripts/pull_bars.py --months-back 60 --timeframes H4,M15,M5

# 3. 跑端到端回测 (需要数据)
.\.venv\Scripts\python.exe scripts/backtest_smc.py --start 2022-03 --end 2026-05
.\.venv\Scripts\python.exe scripts/analyze_smc.py    # 3 模式横向对比

# 4. 等朋友 validation 答案 (P0)
#    用户已经给朋友发了 3 个验证问题, 见对话记录
#    朋友答完之后:
#      - 如果说"必破前低" -> 加 capitulation 过滤, 重跑
#      - 如果说漏检了 setup -> 把那些 setup 拿出来, 反推规则缺什么

# 5. (可并行) 移植到 NQ
#    需要先确认 IC Markets 给不给 NAS100 的历史
#    .\.venv\Scripts\python.exe -c "import MetaTrader5 as mt5; mt5.initialize(...); print(mt5.symbol_info('NAS100'))"
```

## 11. 文件位置速查 (按机器)
```
旧机器 (Administrator):
  项目根:        E:\xauusd_quant
  Memory:        C:\Users\Administrator\.claude\projects\E--xauusd-quant\memory\
  Demo MT5:      F:\demo-mt5\terminal64.exe

当前机器 (ldh19):
  项目根:        W:\xauusd_quant
  Memory:        C:\Users\ldh19\.claude\projects\W--xauusd-quant\memory\
  Demo MT5:      C:\Program Files\MetaTrader 5\terminal64.exe

通用:
  Demo MT5 路径解析: quant.config.get_demo_mt5_path() (env var 或 _KNOWN_DEMO_MT5_PATHS)
  凭据 (本地, 不入 repo): credentials  (放在 repo 根, 已被 .gitignore 排除)
```

## 12. 当前未推进的事项 (2026-05-12 快照)

- **本地领先 origin/main 7 commits 没 push**（用户没明确要求 push 之前不要主动 push）
- **朋友的 3 个 validation 问题未回**
- **NQ symbol 还没在 IC Markets 上确认是否能拉**（需要先 query symbol_info）
- **Walk-forward harness 没写**（P3 backlog）
- **Regime filter / news blackout / circuit breaker 都没写**（P4-P5）
- **空头 setup 没实现**（P6）
- **Phase 3 MQL5 EA 完全没动**（即使策略最终验证 OK，也得至少 paper trade 几个月才该考虑 EA）

## 13. 已经讨论过的概念（用户已掌握，不要再从零讲）

- **spread**：ask - bid 的差，每笔交易必付的隐性成本；P50 ≈ 10pt 活市
- **point vs pip vs tick**：XAUUSD 上三者基本同义，均 = 0.01 USD/oz
- **commission vs spread**：IC Raw 把两者显性分开
- **swap (overnight financing)**：XAUUSD 短偏置当前拿正 carry ~+$46.60/lot/night
- **slippage**：点击价 vs 实际成交价的差
- **filling mode IOC**：MT5 订单填充模式
- **broker time vs UTC**：MT5 返回的 time_msc 是 broker 本地墙钟（必须用 `BrokerClock` 转换）
- **R:R (risk-reward ratio)**：止损距离 vs 止盈距离的比；breakeven win rate = 1/(1+R:R)
- **Hedge mode vs Netting**：IC demo 用 Hedge
- **BOS / FVG / Wyckoff / SMC**：Smart Money Concepts；BOS = Break of Structure，FVG = Fair Value Gap
- **BE (Break-even) move**：浮盈触发后把 SL 移到入场价
- **Sharpe ratio**：年化 risk-adjusted 收益；零售 0.5-1.0 算合格，1.0+ 算顶尖
- **Raw edge**：策略每笔在固定仓位下的期望盈亏，剥掉复利和杠杆的影响 — 真实 alpha 度量
- **Kelly / 超 Kelly**：最优仓位公式；超过最优会让 Sharpe 反而下降（volatility drag）
- **Walk-forward / OOS**：滚动 train/test 分割，避免过拟合
- **Path-dependence**：同样一组 trades 不同顺序 → 不同复利曲线 → 不同回撤

## 14. 用户当前心智状态 (重要)

- **完全消化** raw edge / Sharpe / Kelly / 超 Kelly volatility drag 这些概念了
- **接受** SMC 策略只有"弱但真实的正 EV"，不是神器
- **接受** N=29 还不能下结论，需要更多样本（NQ 移植 + 长样本）
- **关注**忠实度优先：在扩样本之前要先确认代码真的还原朋友的策略（这就是为什么要做 validation）
- **不接受** timing-conservative 建议
- **会问基础概念澄清**（"BE 是什么意思" / "raw edge 怎么算的" / "Sharpe 多少"），首次出现要简短解释，二次默认理解
- **Calgary** 时区，所有时段建议用 Calgary local time

# XAUUSD CFD 量化项目 — 交接文档

> 把这份文档完整粘给新机器上的 Claude Code 当第一条消息，就能接上之前的进度。
> 最后更新：**2026-05-11 (Monday 13:04 Calgary / 19:04 UTC)** by 用户 + Claude Opus 4.7

---

## 1. 当前状态快照

- **阶段 0 完整闭环 ✅**：环境、连接、读路径、写路径、demo 实盘单全部跑通
- **阶段 1 第一波 ✅**：数据基础设施 + broker timezone robustness + cost model 校准
- **阶段 1 第二波 — Bollinger baseline ✅**：教学回测跑完，验证 cost-stack 杀伤力（gross +$828 → all-in -$312 on best params）
- **阶段 1 第二波 — SMC/BOS 反转策略（进行中）**：等朋友答 15 个澄清问题后开 Phase 1
- **Repo**：https://github.com/UAACC/xauusd_quant ，main 分支，最新 commit `8692204`（5/11 fixture 由用户在 GitHub web UI 创建）
  - identity: per-repo `UAACC` / `61613205+UAACC@users.noreply.github.com`
  - **不带** `Co-Authored-By: Claude` trailer
- **已推送**：本地 == origin/main 同步，工作树干净
- **commit 历史**（5/10 起）:
  ```
  8692204  Create XAUUSD_2026-05-11.json                (web UI, 用户睡前)
  abcc3b9  feat(backtest): BB mean-revert + bar-level engine
  ec78df2  docs: comprehensive HANDOFF update
  9f57143  feat(stage-1): tick/bar pipeline + spec + live monitor + clock-aware UTC
  4f6e5a4  Initial project structure
  ```

## 2. 项目背景（不要重新讨论的事项）

- **券商**：IC Markets，**Raw Trading Ltd / Seychelles 实体**（`ICMarketsSC-MT5`）
  - demo 账户已开（`ICMarketsSC-Demo`）
  - 用户 live 账户也在 Raw Trading 跑一个 EA
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
| 1 | tick/bar 数据管道 + broker time + cost model | ⚠️ 第一波完成；spread_surface / swap_calendar / qa 仍待做 |
| 2 | Alpha 研究（Python）：vol regime + SMC/BOS reversal | 🚧 进行中 — 正在还原朋友的 SMC 策略 |
| 3 | MQL5 EA 实现：把 OOS 通过的策略翻译成 EA | 未开始 |
| 4 | 风控层：仓位 sizing、止损、最大回撤熔断、新闻过滤 | 未开始 |
| 5 | 实盘：模拟 → 小资金 → 放量 | 未开始 |

## 4. 已建好的文件清单

### Python package (`quant/`)
```
quant/
├── __init__.py
├── costs/
│   ├── __init__.py
│   └── spec.py              # SymbolStaticSpec, SymbolDailySnapshot, diff + severity 分级
├── data/
│   ├── __init__.py
│   ├── parquet.py           # tick + bar partitioned parquet I/O, schema metadata
│   └── broker_time.py       # BrokerClock + discover_clock; zoneinfo 处理 DST, 不 hardcode
├── strategies/
│   ├── __init__.py
│   └── bb_mean_revert.py    # Bollinger 均值回归 (教学 baseline, 已证实效果差)
└── backtest/
    ├── __init__.py
    └── engine.py            # bar-level vectorized PnL with cost deduction
```

### Scripts (`scripts/`)
```
mt5_connect.py     # MT5 连接 + spec 拉取 + CostModel; trade_mode==2 (REAL) abort 守卫
snapshot_spec.py   # 每日 spec snapshot + diff alert; --keep-identity 才保留 account_login
pull_ticks.py      # 历史 tick 按天拉, partitioned parquet
pull_bars.py       # 多 timeframe (M1/M5/M15/M30/H1/H4/D1) 历史 bar
live_monitor.py    # 10 Hz 实时 tick 监控 + 告警 + 每 5s flush parquet
test_order.py      # programmatic 0.01 lot round-trip slippage 测试 (--execute 才发单)
backtest_bb.py     # BB 策略回测 (教学例子, 演示成本对策略的杀伤)
```

### 数据与 fixtures
```
data/                                                  # gitignored 数据湖
├── ticks/XAUUSD/2026/05/<YYYY-MM-DD>.parquet         # 日级别 tick
├── bars/XAUUSD/{M1..D1}/2026/<YYYY-MM>.parquet       # 月级别 bar
└── alerts.jsonl                                       # live_monitor 告警日志

fixtures/                                              # tracked, public-safe
└── spec/XAUUSD_2026-05-10.json                       # 首份 audit-trail snapshot
                                                      # account_login 已脱敏为 0
```

### Docs
```
HANDOFF.md (本文)
README.md
.gitignore       # /data/ (anchored), credentials*, .claude/, __pycache__/, .env, etc.
requirements.txt # MetaTrader5, pandas, numpy, pyarrow
```

## 5. 关键技术决策与陷阱（必须知道）

### 5.1 MT5 Python 时区陷阱（CRITICAL）
**MT5 Python 包返回的 `time` / `time_msc` 不是真 UTC**，是 broker 本地墙钟当 Unix epoch 用。

- IC Markets MT5 server tz = **Europe/Athens** (EEST UTC+3 夏 / EET UTC+2 冬)
- 直接用 `pd.to_datetime(ts, unit='s', utc=True)` 是**错的** — 标了 UTC 但其实是 broker time
- 我们的修复：`quant/data/broker_time.py` 提供 `BrokerClock`：
  - `discover_clock(mt5, server_name, symbol)` — 运行时探测 broker tz + 实测对照 IANA 假设
  - `broker_msc_to_utc_msc(broker_msc)` — 用 `zoneinfo` 转换（DST 自动处理）
  - `utc_dt_to_mt5_query(utc_dt)` — 转给 MT5 查询用（**绝对不要 naive datetime**，naive 在不同系统时区下的 `.timestamp()` 行为不同）

**所有 parquet 写入存真 UTC**，schema metadata 标记 `time_convention=utc`。Legacy 文件（May 4-8 ticks 和早期 bars）仍是 broker-time-as-UTC 标错的状态，read 时由 `_read_existing_ticks_as_utc()` 自动转换。Task #16 待办：用 `--force` 重新拉一遍干净的 UTC 版本。

### 5.2 Dual-MT5 隔离 + 多机器路径解析
用户有两个独立 MT5 安装：
- **Live MT5**（用户自己 EA 跑的，**绝对不要碰**）— 路径不知，他自己用
- **Demo MT5** — 路径因机器而异，所有代码必须显式绑这个

**多机器路径用 `quant.config.get_demo_mt5_path()` 解析**（don't hardcode）：
1. 优先读 env var `XAUUSD_DEMO_MT5_PATH`
2. 否则按 `_KNOWN_DEMO_MT5_PATHS` 顺序找第一个存在的：
   - `F:\demo-mt5\terminal64.exe`（旧机器, repo 在 `E:\xauusd_quant`）
   - `C:\Program Files\MetaTrader 5\terminal64.exe`（当前机器, repo 在 `W:\xauusd_quant`）
3. 都不存在 → `FileNotFoundError`（loud-fail）

加新机器：在 `quant/config.py` 的 `_KNOWN_DEMO_MT5_PATHS` 前面加路径，**不要删** 已有项（用户在两台机器之间切换）；或者直接在那台机器上设 env var。

`mt5_connect.init_mt5()` 里有 `trade_mode == 2 (REAL) abort` 守卫做次级防御，意外绑到 live 会 abort，**不要拆**。

### 5.3 IC Markets XAUUSD 校准值（2026-05-10 verified）
```
contract_size       100 oz/lot
point / digits      0.01 / 2
tick_size           0.01
tick_value          $1 / lot / point
spread_mode         floating
filling_mode        IOC
stops_level         0  (无最小 SL/TP 距离限制)
commission          $3.5/lot/side, in/out → $7/lot 全程  ← Seychelles 实体, NOT 免佣
swap_long           -56.801 pts → -$56.80/lot/night
swap_short          +48.815 pts → +$48.82/lot/night       (短偏置有正 carry)
swap_3day_index     3 (Wed 三倍 swap)
swap_mode           1 (POINTS)
trading_hours       Mon-Thu 01:00-23:59 server, Fri 01:00-23:57
weekly_open         Monday 01:00 broker EEST = Sunday 22:00 UTC
                    = Sunday 16:00 MDT Calgary  ← 用户在 Calgary
```

### 5.4 历史成本计算示例
```
0.01 lot round-trip on XAUUSD (活市 P50 spread):
  spread       : 9 pt × 0.01 lot × $1/pt = $0.09
  commission   : $7/lot × 0.01 lot       = $0.07
  slippage     : ~5 pt × 0.01 lot × $1/pt= $0.05  (test_order 实测)
  ─────────────────────────────────────
  total r/t    : ~$0.21

  Verified by test_order.py 2026-05-10:
  predicted (cost_model) = $0.16  (no slippage)
  measured (real fills)  = $0.16  (spread 9 + commission 7) ✓ 数学对齐
```

### 5.5 不要 hardcode 任何时区偏移
**绝对不要**写 `subtract 3 hours` 这种代码。任何时区相关运算必须走 `BrokerClock` 或 `zoneinfo`。原因：
1. DST 切换每年两次（EEST ↔ EET）
2. broker 可能换时区
3. 不同系统本地时区跑同一份代码结果会不一致

### 5.6 修过的 bug（不要再踩）
- `mt5_connect.py:DATA_DIR` 之前是 `Path.home() / "xauusd_quant" / "data"` — 仅在 home dir = repo dir 时正确，已改为 `Path(__file__).resolve().parent.parent / "data"` 锚定 repo 根
- `.gitignore` 之前是 `data/` 太泛，把 `quant/data/` 误伤，已改为 `/data/` 锚根
- `BrokerClock.utc_dt_to_broker_naive` 之前返回 naive datetime，在非-UTC 系统上 `.timestamp()` 用本地 tz 解释 → 9 小时错位 → MT5 查询 0 ticks。已改为 `utc_dt_to_mt5_query()` 返回 UTC-aware 但 wall-clock 是 broker 本地。
- `live_monitor.py` 的 final flush 漏传 clock 参数 (现已修复)
- `mt5_connect.py:filling_mode` 报告中说"filling_mode: 2"，那是 symbol 支持的 bitmask（`SYMBOL_FILLING_IOC=2`），不是 order request 用的常量（`ORDER_FILLING_IOC=1`），不要混

## 6. 用户偏好（必读，影响所有交流）

- **回复要简洁**，不要长篇说教
- 用户**懂金融数学和编程**，但**对操作型量化术语生疏** — 第一次出现专业词汇（slippage 测量方法 / spread distribution / tick data / FVG / BOS / Wyckoff）需要简短解释，**之后默认理解**
- 不要 timing-conservative — 不要建议 "等理想时段再做"，他要执行
- 不要数据挖掘 / 优化 — 拒绝 "我们调参数让 sharpe 更高" 的诱惑
- 不要承诺夏普 3+ / 月化 10%+ / 90% 胜率，那都是骗子话术
- 用户在 **Calgary**（MDT = UTC-6 夏令时） — 当涉及具体时段建议时，用 Calgary 时间
- **不要把真实邮箱写进 repo 任何文件** — 用 GitHub noreply 别名
- **不要把账号 / 密码写进 chat 或 memory** — 即使 demo 账号也建议匿名化
- 如果用户说话术不清，**直接问澄清** — 不要假设

## 7. 阶段 1 已完成 vs 待做

### ✅ 已完成
- [x] quant/ 包骨架 + pyarrow
- [x] snapshot_spec.py 每日 JSON 入 fixtures + diff alert
- [x] pull_ticks.py 历史 tick 按天 partitioned parquet（已拉 7 天 = 3.3M ticks）
- [x] pull_bars.py 多 TF bar（已拉 3 个月 = 89k bars）
- [x] live_monitor.py 实时 tick + alerts + persist（开市后挂着持续采集）
- [x] test_order.py round-trip 滑点 plumbing 测试
- [x] BrokerClock + zoneinfo timezone robustness
- [x] BB(20,2) baseline backtest（教学案例，验证 cost model 数学对齐）

### 🚧 待做（按优先级）
- [ ] **Task #16**: re-pull historical with `--force` to overwrite legacy broker-time-as-UTC parquets with canonical UTC
  - `python scripts/pull_ticks.py --days-back 7 --force`
  - `python scripts/pull_bars.py --months-back 3 --force`
- [ ] **正在做**: 还原朋友的 SMC/BOS reversal 策略（详见第 8 节）
- [ ] `quant/structure/`: swings.py, fvg.py, bos.py, capitulation.py — SMC primitives
- [ ] `quant/costs/spread_surface.py`: 经验 spread 分布 by (weekday, hour-of-day) 切片
- [ ] `quant/costs/swap_calendar.py`: 每日 snapshot 时间序列
- [ ] `quant/data/qa.py`: gap detection / spread anomaly / weekend boundary check
- [ ] `quant/backtest/`: walk-forward harness + intrabar high/low stop check（当前只用 close）
- [ ] `quant/risk/`: position sizing (Kelly fractional), VAR limits, max DD circuit, news blackout

## 8. 当前进行中：还原朋友的 SMC/BOS reversal 策略

### 8.1 朋友描述的具体一笔（XAUUSD H4）
```
Apr 17 10:00      4H 高点 4917  ← 下降趋势起点
Apr 29 14:00      4H 低点 4522  ← 第一波下跌底
May 1  14:00      pinbar 反弹高点 4673  ← 上一波 lower high
May 4  14:00      新低 4510  ← 比 4522 只低 11 美元 (0.24%) + 爆大成交量
                              → 触底信号 (false breakdown + capitulation)
May 5  22:00 + May 6 02:00    两根 4H K 突破 4673 + 回踩  ← BOS 确认
                              → 多头 entry signal
TP                4770 (Apr 20-22 FVG 上沿 + 多日阻力)
SL                ~4510 下方 (隐含)
```

### 8.2 朋友总结的转势 3 条件
1. 底部/顶部成交量放量
2. Break of Structure (BOS) — 下降趋势出现 higher high 而无 lower low（反之亦然）
3. 大级别支撑/阻力位被突破后回踩被 reject

### 8.3 这套策略在量化圈的归属
- **Smart Money Concepts (SMC)** / **Wyckoff** / **ICT (Inner Circle Trader)** — 不是玄学，有 institutional 根基
- 经济解释：机构资金大单建仓/平仓在 price action 留下可识别痕迹（流动性扫荡 / order block / fair value gap）
- **比 BB 这种纯统计指标靠谱**

### 8.4 算法化方案
```
Phase 1: 建 quant/structure/ 子包
  - swings.py        : 5-bar fractal swing high/low detection
  - fvg.py           : Fair Value Gap (3-bar imbalance)
  - bos.py           : Break of Structure (close > recent LH)
  - capitulation.py  : Volume Z-score + failed breakdown

Phase 2: quant/strategies/bos_reversal.py
  Stage 1: 识别 swing structure
  Stage 2: 检测 failed breakdown + capitulation
  Stage 3: 等待 BOS (突破 LH)
  Stage 4: 等待 retest + hold
  Stage 5: 生成 entry/SL/TP, 强制 R:R ≥ 1.5

Phase 3: backtest 在 H4 数据上 (3 个月 ~400 bars, 期待 N=15-30 setup)
Phase 4: case study — 我们代码能否在 5/5 22:00 - 5/6 02:00 生成同样信号
Phase 5: walk-forward 跨更长样本 (拉 1-2 年 H4)
```

### 8.5 阻塞：等用户朋友回答 15 个澄清问题
**已发给用户**，5/10 傍晚发出，**5/11 下午仍未回**（朋友可能要几天才有空整理）。最关键的 5 个：
1. 入场点是 break 还是 retest 还是 hold-confirmed？
2. SL 放哪（4510 下还是 retest low 下）？— 这决定 R:R 是 1:0.6 还是 1:5+
3. TP 4770 的 FVG 来自哪 3 根 bar？
4. 爆量是几倍中位数？
5. 假突破容忍多少 % 跌幅？

剩下 10 个见上次对话末尾，包括：BOS 突破细节、趋势定义、SL trailing、新闻过滤、仓位、胜率历史、跨标的、反向 setup、confluence、B 级 setup 接不接。

朋友回完 15 题后立刻动手 Phase 1（structure primitives）。

## 9. 重要 mindset / 已对齐的认知

### 9.1 高胜率不等于赚钱
- 经典陷阱：高胜率 EA 通常是紧 TP + 宽 SL（赚 $1 输 $20 → -EV）
- 真正决定盈利的是 EV per trade after costs
- 用户已被教育，不要再被"90% 胜率剥头皮 EA"话术忽悠

### 9.2 不要参数优化
- 67 天样本 + 9 组 BB 参数 → 最好的 BB(20,1.5) gross +$828 但 all-in -$312
- 数据挖掘陷阱（multiple comparison）— 100 组里挑出来的 sharpe 2 在 OOS 上必死
- 永远不要"调参数让回测好看"
- 唯一合理的"调参"是 walk-forward 框架内 + nested CV，**不是 grid search**

### 9.3 现实 sharpe / 胜率 benchmark
```
机构买方:   Sharpe 1.5-2.5, 年化 15-25%, 回撤 8-15%
顶尖零售:   Sharpe 1.0-1.5, 年化 10-20%, 回撤 10-20%, 胜率 45-60%  ← 我们目标
现实零售:   Sharpe 0.3-0.8, 年化 5-12%, 回撤 15-25%
零售平均:   长期 -2% 到 -5%/年, 1-2 年破产               ← 95% 零售
```

### 9.4 真实的研究流程
```
1. 提出假设 (有经济解释)
2. 设计实验 + null hypothesis
3. 跑数据
4. 统计显著性 (t-stat > 2.5, 不是看 sharpe)
5. OOS 验证 (hold out 30%)
6. Walk-forward (rolling)
7. 风控加成 (sizing / SL / blackout / circuit breaker)
8. Paper trade 1-3 月
9. Small live ($1-5k) 1-3 月
10. Scale or kill
```
**90% 假设死在 step 4-5。能活到 step 10 的 1-2%。**

## 10. 立即可执行的下一步

```powershell
# 1. (用户在另一台机器上 catch up 时) 克隆 repo
git clone https://github.com/UAACC/xauusd_quant.git
cd xauusd_quant

# 2. 建虚拟环境 + 装依赖 (用 uv, 比 pip 快 ~10x)
#   没装 uv: winget install --id=astral-sh.uv  或  pipx install uv
uv venv
uv pip install -r requirements.txt
# 验证: .\.venv\Scripts\python.exe -c "import MetaTrader5, pandas, numpy, pyarrow"
# 之后所有 python 命令前面要么用 .\.venv\Scripts\python.exe 完整路径,
# 要么先 .\.venv\Scripts\activate (每个新 PowerShell 会话)

# 3. 装 IC Markets MT5 demo 客户端
# 已知路径见 quant.config._KNOWN_DEMO_MT5_PATHS:
#   - F:\demo-mt5\terminal64.exe (旧机器)
#   - C:\Program Files\MetaTrader 5\terminal64.exe (当前机器)
# 如果新机器还没装, 跑这个 setup:
#   https://download.mql5.com/cdn/web/ic.markets.pty.ltd/mt5/icmarkets5setup.exe
# 装到独立路径 (不要覆盖任何已有 MT5)
# 登录 IC demo (Raw Trading Ltd 实体, ICMarketsSC-Demo 服务器)

# 4. 设 per-repo git identity
git config user.name "UAACC"
git config user.email "61613205+UAACC@users.noreply.github.com"

# 5. 添加机器路径 (二选一):
#   (a) 在 quant/config.py 的 _KNOWN_DEMO_MT5_PATHS 元组里加一行 (推荐, 跨机器自洽)
#   (b) 设 env var: $env:XAUUSD_DEMO_MT5_PATH = "C:\path\to\terminal64.exe"
# 验证: python -c "from quant.config import get_demo_mt5_path; print(get_demo_mt5_path())"

# 6. 跑 smoke test 验证
python scripts/snapshot_spec.py     # 应输出 [CLOCK] iana_tz=Europe/Athens, agreement=OK
python scripts/mt5_connect.py        # 拉 spec + tick + 30 天 M1

# 7. 接着做朋友 SMC 策略 (等他答 15 题)
#    见第 8 节
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

## 12. 当前未推进的事项 (2026-05-11 19:04 UTC 快照)

- **live_monitor 已停**（用户睡前 Ctrl+C，最后 flush 在 2026-05-11 04:35 UTC ≈ 22:35 Sunday Calgary）
  - 5/10 22:06 - 5/11 04:35 UTC 共采到 **162,673 ticks**（约 6.5h 活市数据）
  - 实测 spread P50 = 10 pts, P95 = 14 pts（跟历史 7 天数据一致，cost model 假设进一步验证）
  - 想继续采集需要重启：`python scripts/live_monitor.py`
- **task #16 (re-pull historical with UTC)** 仍 pending — 一行 `--force` 就能跑（Monday 活市数据丰富时机最好）
- **5/11 fixture 已 verify 为真数据**（不是占位）— diff vs 5/10 显示：所有 static / commission / swap / margin / stops_level **全部 unchanged**（broker 没偷改任何成本结构 ✓）
- **用户朋友 15 题答复仍未到**（>24h）— SMC Phase 1 仍阻塞
- **Stage 1 后续模块**（spread_surface / swap_calendar / qa）未开始 — 不阻塞 SMC，可并行
- **Repo state**：本地 main == origin/main 同步，工作树干净。最新 commit `8692204`。

## 13. 已经讨论过的概念（用户已掌握，不要再从零讲）

- **spread 的物理 + 财务含义**：ask - bid 的差，每笔交易必付的隐性成本
  - 公式：spread_pts = (ask - bid) / point；$ cost = spread × contract × lot
  - 时段分布：液态 P50 ≈ 10pt，亚盘 P50 ≈ 15pt，NY 17:00 滚动结算 P95 > 50pt
- **point vs pip vs tick**：XAUUSD 上三者基本同义，均 = 0.01 USD/oz
- **commission vs spread**：IC Raw Spread 把两者显性分开（spread 浮动 + commission $7/lot 固定），比"零佣金"账户对量化研究友好
- **swap (overnight financing)**：持仓过夜利息；XAUUSD 短偏置当前拿正 carry ~+$48/lot/night
- **slippage**：你 click 的价 vs 实际成交价的差，是 spread 之外的额外摩擦
- **filling mode IOC**：MT5 订单填充模式之一，部分成交剩余取消（IC XAUUSD 的默认）
- **broker time vs UTC**：MT5 返回的 time_msc 是 broker 本地墙钟（EEST UTC+3）当 epoch 用，不是真 UTC（必须用 `BrokerClock` 转换）
- **R:R (risk-reward ratio)**：止损距离 vs 止盈距离的比；R:R 1:2 意味输 1 元为了赚 2 元；胜率门槛 = 1/(1+R:R)
- **Hedge mode vs Netting**：IC demo 用 Hedge，可同时持多空仓位（不自动 net）
- **BOS / FVG / Wyckoff / SMC**：Smart Money Concepts 术语；BOS = Break of Structure（趋势转折信号），FVG = Fair Value Gap（3-bar 价格真空）

## 14. 用户当前心智状态 (重要)

- **已经放弃**"找到 90% 胜率剥头皮"幻想，接受现实 retail benchmark (Sharpe 1.0-1.5)
- **接受**"先扎实研究 3-6 个月再上实盘"的时间预期
- **专注**于把朋友的 SMC 策略系统化（认可它比 BB 指标更有经济解释）
- **会问基础概念澄清**（比如 "spread 到底是啥"）— 不要因为之前讲过就跳过解释，他会主动问
- **不接受**timing-conservative 建议（不要说"等更好时段再做"，他要执行）

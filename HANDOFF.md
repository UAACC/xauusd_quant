# XAUUSD CFD 量化项目 — 交接文档

> 把这份文档完整粘给本地机器上的 Claude Code，作为新会话的第一条消息，就能接上之前的讨论。

---

## 项目背景

我在搭建一个 **XAUUSD CFD 量化交易系统**，从 0 到 1。

- **券商**：IC Markets，**Raw Spread MT5** 账户（已开户）
- **执行平台**：MetaTrader 5，算法用 MQL5 写 EA
- **研究方向（已对齐）**：**波动率 / 统计套利**优先；不走趋势跟踪、不优先 ML、不做事件驱动作为起点
- **当前阶段**：阶段 0（环境搭建 + 第一笔手动交易），demo 首单**还未完成**

## 整体路线图

| 阶段 | 目标 |
|---|---|
| 0 | 环境搭建：MT5 + IC Markets 接通，demo 跑通第一笔 |
| 1 | 数据与微观结构：tick/M1 数据管道、点差/佣金/swap 成本模型 |
| 2 | Alpha 研究（**Python 做，不在 MQL5 做**）：vol regime + mean reversion baseline |
| 3 | MQL5 实现：把 OOS 通过的策略翻译成 EA |
| 4 | 风控层：仓位 sizing、止损、最大回撤熔断、新闻过滤 |
| 5 | 实盘：模拟 → demo → 小资金实盘 → 放量 |

## 已经识别的 XAUUSD 关键坑

- IC Raw Spread XAUUSD commission：**Seychelles 实体 (Raw Trading Ltd, `ICMarketsSC-MT5`) 不是免佣 — $3.5/lot/side = $7/lot 全程**。免佣只适用 AU 实体 (`ICMarkets-MT5`)。脚本默认按 Seychelles 算，因为用户 live 在那。(Verified 2026-05-10.)
- **三倍 swap 通常在周三**（不是周五）
- 美东 17:00 前后流动性骤降、点差爆炸 —— 回测必须剔除
- **1 lot = 100 oz**；1 个 pip ≈ 1 美元/0.01 lot（容易和外汇 pip 混淆）

## 文件清单

- `scripts/mt5_connect.py` — Python 连 MT5 的脚本（已写好），做 5 件事：
  1. 连接 MT5 终端
  2. 拉 XAUUSD symbol specification
  3. 拿最新 tick
  4. 拉最近 30 天 M1 K 线
  5. 构建 `CostModel` dataclass，存 CSV

## 关键技术决策（已对齐，不要重新讨论）

- **MetaTrader5 Python 包是 Windows-only** — 必须在 Windows 机器/VM/Wine 里跑，Linux 原生不行
- **Alpha 研究在 Python 做**，MQL5 只做执行层
- **回测必须包含**点差 + 佣金 + swap + 滑点
- **第一笔交易在 demo 上做**，不直接上真金白银

## 下一步立即要做的事

1. 把这个项目目录 scp 到本地 Windows 机器（或 Wine 环境）
2. `pip install MetaTrader5 pandas`
3. 装 IC Markets MT5 终端，登录 demo 账户
4. 在 MT5 GUI 里 Market Watch 加 XAUUSD，**截图/抄下 Specification**（contract size / tick value / commission / swap long/short / stops level）
5. 把 Specification 数值发给 Claude，让 Claude 帮你校准 `CostModel`
6. 跑 `python mt5_connect.py`，验证连接 + 拉数据
7. 在 demo 上手动下一笔 **0.01 lot** 的测试单（流程：F9 → Buy/Sell Market Execution → 观察 Terminal 里的成交价 vs Market Watch 当时的 Ask 差，记录滑点感知）
8. 完成后再讨论阶段 1 的数据管道和阶段 2 的 mean-reversion baseline

## 我希望 Claude 记住

- 我的方向是**波动率/均值回归**优先，不要给我推趋势跟踪或 ML 方案作为起点
- 我会**先在 demo 上验证**，不要假设我已经在实盘
- **回测里成本模型不能省**，每个策略都要扣完成本看 OOS 夏普
- 我**懂金融数学和编程**，可以直接给我贴代码和公式，不用从基础概念讲起

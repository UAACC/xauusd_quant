# xauusd_quant

XAUUSD CFD 量化交易系统 — IC Markets Raw Spread + MetaTrader 5。

## 状态

阶段 0：环境搭建。第一笔 demo 测试单尚未完成。

## 路线图

| 阶段 | 目标 |
|---|---|
| 0 | MT5 + IC Markets 接通，demo 跑通第一笔 |
| 1 | 数据管道 + 成本模型（点差 / 佣金 / swap） |
| 2 | Python 做 alpha 研究：vol regime + mean reversion baseline |
| 3 | MQL5 EA 实现 |
| 4 | 风控层：仓位 sizing、止损、最大回撤熔断、新闻过滤 |
| 5 | demo → 小资金实盘 → 放量 |

研究方向：**波动率 / 统计套利**。Alpha 研究在 Python，MQL5 只做执行层。

## 目录结构

```
scripts/        Python 脚本（连 MT5、拉数据、回测）
notebooks/      Jupyter 探索用
data/           本地缓存的 CSV — 不入 git
HANDOFF.md      项目交接文档（机器迁移用）
```

## 平台

`MetaTrader5` Python 包仅支持 Windows。Linux 用户走 Wine 或在 MT5 端跑 EA 经 ZeroMQ 桥接。

## 安装

```powershell
# uv (推荐, ~10x 快于 pip)
uv venv
uv pip install -r requirements.txt

# 没装 uv 的话:  winget install --id=astral-sh.uv  或  pipx install uv
# 也可以走 stdlib 方案: python -m venv .venv ; .\.venv\Scripts\pip install -r requirements.txt
```

需要先装好 IC Markets MT5 客户端并登录 demo 账户。Demo MT5 路径由 `quant.config.get_demo_mt5_path()` 自动解析（已知机器路径见 `quant/config.py`）。

## 用法

```powershell
.\.venv\Scripts\python.exe scripts/mt5_connect.py
# 或者先 .\.venv\Scripts\activate, 然后 python scripts/mt5_connect.py
```

输出：账户连接状态、XAUUSD specification、最新 tick、最近 30 天 M1 K 线（存到 `data/`）、成本模型估算。

## 关键提醒（XAUUSD 微观结构）

- 1 lot = 100 oz；1 pip ≈ 1 美元/0.01 lot（容易和外汇 pip 混淆）
- 三倍 swap 在**周三**结算，不是周五
- 美东 17:00 前后流动性骤降、点差爆炸 — 回测剔除
- 回测必须包含点差 + 佣金 + swap + 滑点

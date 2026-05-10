"""
mt5_connect.py — IC Markets MT5 连接 + XAUUSD 数据/规格抓取脚本

用途:
  1. 验证 demo 账户能从 Python 连上 MT5 终端
  2. 拉取 XAUUSD 的 symbol specification (回测成本模型用)
  3. 拉取最新 tick + 最近 30 天 M1 K 线, 存成 CSV

平台要求:
  MetaTrader5 Python 包目前只支持 Windows (含 Wine 下的 Python).
  Mac/Linux 原生 Python 装不上. 见底部 "平台说明".

依赖:
  pip install MetaTrader5 pandas
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

try:
    import MetaTrader5 as mt5
except ImportError:
    print("[FATAL] 没装 MetaTrader5. 在 Windows Python 里跑: pip install MetaTrader5")
    sys.exit(1)


SYMBOL = "XAUUSD"
DATA_DIR = Path.home() / "xauusd_quant" / "data"


# ---------------------------------------------------------------------------
# 成本模型: 后面回测时所有信号都要扣这个成本才有意义
# ---------------------------------------------------------------------------
@dataclass
class CostModel:
    contract_size: float        # 1 lot 多少 oz (黄金 = 100)
    point: float                # symbol_info.point, 最小价格步长
    digits: int                 # 报价小数位
    tick_size: float            # 实际成交最小步长 (通常 = point)
    tick_value: float           # 1 tick * 1 lot 的美金价值
    spread_points: int          # 当前点差(以 point 为单位)
    stops_level: int            # 最小止损/止盈距离(point)
    swap_long: float            # 多头隔夜利息
    swap_short: float           # 空头隔夜利息
    swap_3day_index: int        # 哪一天三倍展期 (MT5: 0..6, 一般 3=Wed)
    margin_initial: float       # 1 lot 初始保证金
    commission_roundtrip_usd: float = 0.0  # IC Raw Spread 黄金通常 = 0, 用户确认后改

    def spread_cost_usd_per_lot(self) -> float:
        """单笔开仓只算点差的成本(美金/手)."""
        return self.spread_points * self.tick_value

    def total_roundtrip_usd_per_lot(self) -> float:
        return self.spread_cost_usd_per_lot() + self.commission_roundtrip_usd


# ---------------------------------------------------------------------------
# 连接
# ---------------------------------------------------------------------------
def init_mt5(
    login: int | None = None,
    password: str | None = None,
    server: str | None = None,
    terminal_path: str | None = None,
) -> bool:
    """连 MT5 终端. 如果终端已在前台手动登录, 三个凭据可以全传 None."""
    kwargs: dict = {}
    if terminal_path:
        kwargs["path"] = terminal_path
    if login is not None:
        kwargs["login"] = int(login)
    if password is not None:
        kwargs["password"] = password
    if server is not None:
        kwargs["server"] = server

    if not mt5.initialize(**kwargs):
        print(f"[ERROR] MT5 initialize failed: {mt5.last_error()}", file=sys.stderr)
        return False

    acct = mt5.account_info()
    term = mt5.terminal_info()
    if acct is None or term is None:
        print(f"[ERROR] account_info / terminal_info 返回 None: {mt5.last_error()}")
        return False

    print(f"[OK] Connected.")
    print(f"     Server   : {acct.server}")
    print(f"     Account  : {acct.login}  ({'DEMO' if 'Demo' in acct.server else 'LIVE?'})")
    print(f"     Balance  : {acct.balance} {acct.currency}   Leverage: 1:{acct.leverage}")
    print(f"     Terminal : {term.name} build {term.build}  connected={term.connected}")
    return True


# ---------------------------------------------------------------------------
# Symbol specification
# ---------------------------------------------------------------------------
def get_symbol_info(symbol: str = SYMBOL):
    if not mt5.symbol_select(symbol, True):
        print(f"[ERROR] symbol_select({symbol}) failed: {mt5.last_error()}")
        return None
    info = mt5.symbol_info(symbol)
    if info is None:
        print(f"[ERROR] symbol_info({symbol}) is None: {mt5.last_error()}")
        return None

    print(f"\n=== {symbol} Specification ===")
    print(f"  contract_size     : {info.trade_contract_size}")
    print(f"  digits / point    : {info.digits} / {info.point}")
    print(f"  tick_size         : {info.trade_tick_size}")
    print(f"  tick_value (USD)  : {info.trade_tick_value}")
    print(f"  spread (points)   : {info.spread}")
    print(f"  stops_level       : {info.trade_stops_level}")
    print(f"  swap long / short : {info.swap_long} / {info.swap_short}")
    print(f"  swap_3day_index   : {info.swap_rollover3days}  (0=Sun..6=Sat)")
    print(f"  margin_initial    : {info.margin_initial}")
    print(f"  filling_mode      : {info.filling_mode}")
    return info


def build_cost_model(info, commission_roundtrip_usd: float = 0.0) -> CostModel:
    return CostModel(
        contract_size=info.trade_contract_size,
        point=info.point,
        digits=info.digits,
        tick_size=info.trade_tick_size,
        tick_value=info.trade_tick_value,
        spread_points=info.spread,
        stops_level=info.trade_stops_level,
        swap_long=info.swap_long,
        swap_short=info.swap_short,
        swap_3day_index=info.swap_rollover3days,
        margin_initial=info.margin_initial,
        commission_roundtrip_usd=commission_roundtrip_usd,
    )


# ---------------------------------------------------------------------------
# 实时报价
# ---------------------------------------------------------------------------
def get_latest_tick(symbol: str = SYMBOL):
    tick = mt5.symbol_info_tick(symbol)
    info = mt5.symbol_info(symbol)
    if tick is None or info is None:
        print(f"[ERROR] tick None: {mt5.last_error()}")
        return None
    spread_pts = (tick.ask - tick.bid) / info.point
    ts = datetime.fromtimestamp(tick.time, tz=timezone.utc)
    print(f"\n=== {symbol} Tick @ {ts.isoformat()} ===")
    print(f"  bid={tick.bid}  ask={tick.ask}  spread={spread_pts:.0f} pts")
    return tick


# ---------------------------------------------------------------------------
# 历史 K 线
# ---------------------------------------------------------------------------
def get_history_m1(symbol: str = SYMBOL, days_back: int = 30) -> pd.DataFrame:
    utc_to = datetime.now(tz=timezone.utc)
    utc_from = utc_to - timedelta(days=days_back)
    rates = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_M1, utc_from, utc_to)
    if rates is None or len(rates) == 0:
        print(f"[WARN] copy_rates_range 返回空: {mt5.last_error()}")
        return pd.DataFrame()

    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
    df = df.set_index("time").rename(
        columns={"tick_volume": "tick_vol", "real_volume": "real_vol"}
    )

    print(f"\n=== {symbol} M1 history ===")
    print(f"  rows  : {len(df):,}")
    print(f"  range : {df.index.min()}  →  {df.index.max()}")
    print(df.tail(3).to_string())
    return df


def save_history(df: pd.DataFrame, symbol: str = SYMBOL) -> Path | None:
    if df.empty:
        return None
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    csv = DATA_DIR / f"{symbol}_M1_{datetime.utcnow():%Y%m%d_%H%M%S}.csv"
    df.to_csv(csv)
    print(f"\n[OK] saved {len(df):,} bars → {csv}")
    return csv


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> int:
    # ---- 改这里: 从 IC Markets 后台抄你 demo 账户的凭据 ----
    # 如果你已经在 MT5 终端 GUI 里手动登录, 这三个全留 None 即可
    LOGIN: int | None = None        # e.g. 51234567
    PASSWORD: str | None = None     # e.g. "xxx"
    SERVER: str | None = None       # e.g. "ICMarketsSC-Demo"
    TERMINAL_PATH: str | None = None  # 多终端时指定 terminal64.exe 全路径

    if not init_mt5(LOGIN, PASSWORD, SERVER, TERMINAL_PATH):
        return 1

    try:
        info = get_symbol_info(SYMBOL)
        if info is None:
            return 2

        get_latest_tick(SYMBOL)

        # IC Raw Spread 黄金通常免佣金, 先按 0 算; 你在 Specification 里看到 commission 字段后改这里
        cost = build_cost_model(info, commission_roundtrip_usd=0.0)
        print(f"\n=== Cost model ===")
        print(f"  spread cost / lot : ${cost.spread_cost_usd_per_lot():.2f}")
        print(f"  roundtrip / lot   : ${cost.total_roundtrip_usd_per_lot():.2f}")

        df = get_history_m1(SYMBOL, days_back=30)
        save_history(df, SYMBOL)
    finally:
        mt5.shutdown()

    return 0


if __name__ == "__main__":
    sys.exit(main())


# ---------------------------------------------------------------------------
# 平台说明
# ---------------------------------------------------------------------------
# MetaTrader5 Python 包是 Windows-only. 你在 Linux/Mac 上跑这个文件会
# ImportError. 解决方案:
#
# 方案 A (最简单, 推荐起步): 在 Windows 机器/VM 上直接跑
#   - 装 Python 3.10+, pip install MetaTrader5 pandas
#   - 装 IC Markets MT5 终端, 登录 demo 账户
#   - 直接 python mt5_connect.py
#
# 方案 B (Linux 用户): Wine 跑 Windows 版 Python + MT5
#   - winetricks 装 python (或 conda for Wine)
#   - wine python -m pip install MetaTrader5
#   - 这个项目里所有 mt5_*.py 都用 wine python 跑
#   - 数据落盘到共享目录, 主机 Linux 上做 alpha 研究
#
# 方案 C (远期): 用 ZeroMQ/socket 桥接, MT5 上跑一个发数据的 EA, Python
#   订阅. 工作量大但跨平台干净, 等策略稳定再上.

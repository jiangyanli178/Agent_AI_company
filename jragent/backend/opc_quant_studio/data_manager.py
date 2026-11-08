"""数据管家：行情数据拉取/清洗 + 数据快照（红线 4/5：业绩不造假、可复现）。

- 默认用种子化合成行情（离线可复现），支持从 CSV 加载真实公开数据
- 每份数据落盘为快照（CSV + meta JSON），带 snapshot_id，随报告交付
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np


def _ensure_vendor() -> None:
    """支持项目本地 vendor/ 安装的国内数据源依赖（沙箱环境无法写全局 site-packages）。"""
    vendor = Path(__file__).parent.parent / "vendor"
    if vendor.exists() and str(vendor) not in sys.path:
        sys.path.insert(0, str(vendor))


def _normalize_code(symbol: str) -> str:
    """提取 6 位 A 股代码：'600519.SS（示例）' → '600519'。"""
    m = re.search(r"(\d{6})", symbol)
    return m.group(1) if m else symbol.strip()


@dataclass
class DataSnapshot:
    snapshot_id: str
    symbol: str
    dates: list[str]
    closes: list[float]
    source: str
    pool_definition: str  # 股票池口径（幸存者偏差检查用）
    meta: dict = field(default_factory=dict)

    def to_meta(self) -> dict:
        return {
            "snapshot_id": self.snapshot_id,
            "symbol": self.symbol,
            "source": self.source,
            "n_bars": len(self.closes),
            "date_range": [self.dates[0], self.dates[-1]],
            "pool_definition": self.pool_definition,
            **self.meta,
        }


def _make_snapshot(symbol: str, dates: list[str], closes: list[float],
                   source: str, pool_definition: str) -> DataSnapshot:
    digest = hashlib.sha1(f"{symbol}{dates[0]}{dates[-1]}{closes[-1]:.4f}".encode()).hexdigest()[:8]
    return DataSnapshot(
        snapshot_id=f"SNAP-{digest}", symbol=symbol, dates=dates,
        closes=closes, source=source, pool_definition=pool_definition,
    )


def synthetic_ohlcv(symbol: str = "DEMO-INDEX", n_days: int = 750,
                    seed: int = 47, start_price: float = 100.0) -> DataSnapshot:
    """生成种子化合成日线行情（几何布朗运动 + 轻微趋势切换）。"""
    rng = np.random.default_rng(seed)
    rets = rng.normal(0.0003, 0.008, n_days)
    # 注入三段趋势（按区间比例定位：样本内两段、样本外一段），让均线策略有交易可做
    rets[int(n_days * 0.11):int(n_days * 0.32)] += 0.0015
    rets[int(n_days * 0.50):int(n_days * 0.66)] -= 0.0012
    rets[int(n_days * 0.74):int(n_days * 0.95)] += 0.0015
    closes = start_price * np.cumprod(1 + rets)
    dates = [f"D{i:04d}" for i in range(n_days)]
    snap = _make_snapshot(symbol, dates, closes.tolist(),
                          source=f"synthetic(seed={seed})",
                          pool_definition="全样本单一标的，无成分剔入剔出，无幸存者偏差")
    snap.meta = {"seed": seed, "generator": "GBM+趋势段"}
    return snap


def fetch_cn(symbol: str, n_days: int = 504) -> DataSnapshot:
    """真实公开数据（国内直连·免代理）：AKShare 拉取 A 股日线（东方财富源，前复权）。

    红线 4：只用真实公开数据。依赖缺失/网络失败时抛 RuntimeError，
    由数据收集师依次降级（yfinance → 合成演示数据）并披露。
    """
    _ensure_vendor()
    try:
        import akshare as ak
    except ImportError as e:
        raise RuntimeError("akshare 未安装（pip install akshare）") from e
    import datetime as dt
    code = _normalize_code(symbol)
    if not re.fullmatch(r"\d{6}", code):
        raise RuntimeError(f"无法从 {symbol} 解析出 6 位 A 股代码")
    end = dt.date.today()
    start = end - dt.timedelta(days=int(n_days * 1.6))  # 多留冗余以覆盖交易日口径
    df = ak.stock_zh_a_hist(symbol=code, period="daily",
                            start_date=start.strftime("%Y%m%d"),
                            end_date=end.strftime("%Y%m%d"), adjust="qfq")
    if df is None or len(df) == 0:
        raise RuntimeError(f"{code} 未取到行情")
    dates = [str(d)[:10] for d in df["日期"].tolist()][-n_days:]
    closes = [round(float(v), 4) for v in df["收盘"].tolist()][-n_days:]
    snap = _make_snapshot(symbol, dates, closes,
                          source=f"akshare:{code}（真实公开数据·东方财富源·国内直连）",
                          pool_definition="单一标的全样本日线（前复权），无成分剔入剔出，无幸存者偏差")
    snap.meta = {"provider": "akshare", "code": code, "adjust": "qfq（前复权）"}
    return snap


def fetch_baostock(symbol: str, n_days: int = 504) -> DataSnapshot:
    """真实公开数据（国内直连·免代理·独立服务器）：BaoStock 拉取 A 股日线（前复权）。

    作为 akshare（东方财富源）的备用通道：东财 push2 接口被临时限流时
    自动切换到本通道，避免单源限流导致降级合成数据。
    红线 4：只用真实公开数据。依赖缺失/网络失败时抛 RuntimeError，
    由数据收集师继续降级（yfinance → 合成演示数据）并披露。
    """
    _ensure_vendor()
    try:
        import baostock as bs
    except ImportError as e:
        raise RuntimeError("baostock 未安装（pip install baostock）") from e
    import datetime as dt
    code = _normalize_code(symbol)
    if not re.fullmatch(r"\d{6}", code):
        raise RuntimeError(f"无法从 {symbol} 解析出 6 位 A 股代码")
    # BaoStock 证券代码格式：sh./sz. + 6 位代码
    if code.startswith(("6", "9")):
        bs_code = f"sh.{code}"
    else:
        bs_code = f"sz.{code}"
    end = dt.date.today()
    start = end - dt.timedelta(days=int(n_days * 1.6))  # 多留冗余以覆盖交易日口径
    lg = bs.login()
    if lg.error_code != "0":
        raise RuntimeError(f"baostock 登录失败：{lg.error_msg}")
    try:
        rs = bs.query_history_k_data_plus(
            bs_code, "date,close",
            start_date=start.strftime("%Y-%m-%d"),
            end_date=end.strftime("%Y-%m-%d"),
            frequency="d", adjustflag="2"  # 2=前复权
        )
        if rs.error_code != "0":
            raise RuntimeError(f"baostock 查询失败：{rs.error_msg}")
        rows = []
        while (rs.error_code == "0") and rs.next():
            rows.append(rs.get_row_data())
    finally:
        bs.logout()
    if not rows:
        raise RuntimeError(f"{bs_code} 未取到行情")
    dates = [r[0] for r in rows][-n_days:]
    closes = [round(float(r[1]), 4) for r in rows][-n_days:]
    snap = _make_snapshot(symbol, dates, closes,
                          source=f"baostock:{bs_code}（真实公开数据·独立服务器·国内直连）",
                          pool_definition="单一标的全样本日线（前复权），无成分剔入剔出，无幸存者偏差")
    snap.meta = {"provider": "baostock", "code": bs_code, "adjust": "qfq（前复权）"}
    return snap


def fetch_public(symbol: str, n_days: int = 504) -> DataSnapshot:
    """真实公开数据：yfinance 拉取最近约 n_days 个交易日日线。

    红线 4：只用真实公开数据。依赖缺失/网络失败时抛 RuntimeError，
    由数据收集师明确降级为合成演示数据并在快照与报告中披露。
    """
    try:
        import yfinance as yf
    except ImportError as e:
        raise RuntimeError("yfinance 未安装") from e
    import datetime as dt
    end = dt.date.today()
    start = end - dt.timedelta(days=int(n_days * 1.6))  # 多留冗余以覆盖交易日口径
    df = yf.download(symbol, start=start.isoformat(), end=end.isoformat(),
                     progress=False, auto_adjust=True)
    if df is None or len(df) == 0:
        raise RuntimeError(f"{symbol} 未取到行情")
    close = df["Close"]
    if getattr(close, "ndim", 1) > 1:  # 多级列兼容
        close = close.iloc[:, 0]
    dates = [d.strftime("%Y-%m-%d") for d in df.index][-n_days:]
    closes = [round(float(v), 4) for v in close.tolist()][-n_days:]
    return _make_snapshot(symbol, dates, closes,
                          source=f"yfinance:{symbol}（真实公开数据）",
                          pool_definition="单一标的公开日线，无成分剔入剔出，无幸存者偏差")


def load_csv(path: str | Path, symbol: str = "CSV-LOADED") -> DataSnapshot:
    """从 CSV 加载真实公开数据（要求两列：date,close）。"""
    lines = Path(path).read_text(encoding="utf-8").strip().splitlines()[1:]
    dates, closes = [], []
    for line in lines:
        d, c = line.split(",")[:2]
        dates.append(d.strip())
        closes.append(float(c))
    return _make_snapshot(symbol, dates, closes,
                          source=f"csv:{path}",
                          pool_definition="需人工确认股票池口径（加载时未提供）")


def save_snapshot(snap: DataSnapshot, hub) -> Path:
    """快照落盘到 09-backtest/，保证任何报告可追溯复现（红线 5）。"""
    csv_lines = ["date,close"] + [f"{d},{c:.6f}" for d, c in zip(snap.dates, snap.closes)]
    hub.save_backtest_asset("_snapshots", f"{snap.snapshot_id}.csv", "\n".join(csv_lines))
    meta_path = hub.save_backtest_asset("_snapshots", f"{snap.snapshot_id}.meta.json",
                                        json.dumps(snap.to_meta(), ensure_ascii=False, indent=2))
    hub.log("数据管家", "数据快照留痕", snap.symbol,
            f"{snap.snapshot_id}（{len(snap.closes)} 根K线）", tools=["data.save_snapshot"])
    return meta_path

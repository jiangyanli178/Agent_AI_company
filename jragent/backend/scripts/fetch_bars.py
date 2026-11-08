# -*- coding: utf-8 -*-
"""
离线拉数：akshare → MySQL daily_bars

三条硬规则：
1. 必须前复权 adjust="qfq"。不复权的价格在分红除权日会出现假跳空，均线信号全错。
2. 只保留已收盘交易日。盘中数据不入库，回测与信号一律截至 T-1。
3. 增量更新。每天只拉库里最新日期之后的部分，不做全量重灌。

用法：
    python scripts/fetch_bars.py              # 增量更新全部标的
    python scripts/fetch_bars.py --full       # 首次全量拉 5 年
    python scripts/fetch_bars.py --code 600519
"""
from __future__ import annotations

import argparse
import logging
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

log = logging.getLogger("fetch_bars")

YEARS_OF_HISTORY = 5
REQUEST_INTERVAL = 0.4      # 每次请求间隔，别打得太猛被限流
MAX_RETRY = 3


# ============================================================
# 纯逻辑部分（不依赖网络，可单测）
# ============================================================
def resolve_start_date(latest_in_db: Optional[date], full: bool,
                       today: Optional[date] = None) -> date:
    """
    决定这次从哪天开始拉。
    - 全量或库里没数据：5 年前
    - 增量：库中最新日期的下一天
    """
    today = today or date.today()
    if full or latest_in_db is None:
        return today - timedelta(days=365 * YEARS_OF_HISTORY)
    return latest_in_db + timedelta(days=1)


def is_up_to_date(latest_in_db: Optional[date], today: Optional[date] = None) -> bool:
    """
    数据是否已经够新。
    判定标准：库中最新日期 >= 今天 - 3 天。
    用 3 天是为了覆盖周末（周五的数据，下周一看仍然是新的），
    不处理法定节假日 —— 长假后第一次运行会多拉几天，无害。
    """
    if latest_in_db is None:
        return False
    today = today or date.today()
    return (today - latest_in_db).days <= 3


def latest_allowed_date(now: Optional[datetime] = None) -> date:
    """
    允许入库的最新交易日。
    当天 15:30 之前，当天这根 K 线还没走完，必须剔除，否则回测会用到"半根K线"。
    周末与节假日不用特判：akshare 只返回真实交易日，多往前退一天无害。
    """
    now = now or datetime.now()
    if now.hour < 15 or (now.hour == 15 and now.minute < 30):
        return now.date() - timedelta(days=1)
    return now.date()


def normalize_rows(raw: Iterable[Dict[str, Any]], symbol: str,
                   since: Optional[date] = None,
                   max_date: Optional[date] = None) -> List[Tuple]:
    """
    把 akshare 的行标准化成 daily_bars 的插入元组。
    同时做三件防御：
      - 丢弃收盘价非正数的行（脏数据）
      - 丢弃早于 since 的行（增量时重复部分）
      - 丢弃晚于 max_date 的行（当天未收盘的半根 K 线）
    """
    out: List[Tuple] = []
    for r in raw:
        d = r["trade_date"]
        if isinstance(d, str):
            d = datetime.strptime(d[:10], "%Y-%m-%d").date()
        elif isinstance(d, datetime):
            d = d.date()
        if since and d < since:
            continue
        if max_date and d > max_date:
            continue
        close = float(r["close"])
        if close <= 0:
            continue
        out.append((
            symbol, d,
            float(r["open"]), float(r["high"]), float(r["low"]), close,
            int(r.get("volume") or 0), float(r.get("amount") or 0.0),
        ))
    out.sort(key=lambda x: x[1])
    return out


UPSERT_SQL = """
INSERT INTO daily_bars
  (symbol_code, trade_date, open_price, high_price, low_price, close_price, volume, amount)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
ON DUPLICATE KEY UPDATE
  open_price=VALUES(open_price), high_price=VALUES(high_price),
  low_price=VALUES(low_price),  close_price=VALUES(close_price),
  volume=VALUES(volume),        amount=VALUES(amount)
"""
# 说明：这个脚本是【只跑在 MySQL 上】的离线任务，所以直接用 ON DUPLICATE KEY，
# 不像 credits.py 那样需要兼容 SQLite。重复执行安全。


# ============================================================
# 网络部分
# ============================================================
def fetch_from_akshare(symbol: str, start: date, end: date) -> List[Dict[str, Any]]:
    """带重试的 akshare 调用。失败重试 3 次，仍失败抛出让上层记日志。"""
    import akshare as ak                       # 延迟导入，纯逻辑单测不需要它

    last_err: Optional[Exception] = None
    for attempt in range(1, MAX_RETRY + 1):
        try:
            df = ak.stock_zh_a_hist(
                symbol=symbol,
                period="daily",
                start_date=start.strftime("%Y%m%d"),
                end_date=end.strftime("%Y%m%d"),
                adjust="qfq",                  # ← 前复权，最重要参数
            )
            if df is None or df.empty:
                return []
            col = {
                "日期": "trade_date", "开盘": "open", "最高": "high",
                "最低": "low", "收盘": "close", "成交量": "volume", "成交额": "amount",
            }
            df = df.rename(columns=col)
            return df[list(col.values())].to_dict("records")
        except Exception as e:                 # 网络抖动、限流都会落到这里
            last_err = e
            wait = 2 * attempt
            log.warning("%s 第 %d 次拉取失败：%s，%ds 后重试", symbol, attempt, e, wait)
            time.sleep(wait)
    raise RuntimeError(f"{symbol} 拉取失败：{last_err}")


# ============================================================
# 主流程
# ============================================================
@dataclass
class Stat:
    symbol: str
    inserted: int = 0
    skipped: bool = False
    error: str = ""


def run(full: bool = False, only: Optional[str] = None,
         dry_run: bool = False) -> List[Stat]:
    import os
    import pymysql

    conn = pymysql.connect(
        host=os.getenv("DB_HOST", "127.0.0.1"),
        port=int(os.getenv("DB_PORT", "3306")),
        user=os.getenv("DB_USER", "root"),
        password=os.getenv("DB_PASSWORD", ""),
        database=os.getenv("DB_NAME", "quant_shop"),
        charset="utf8mb4",
        autocommit=False,
    )
    stats: List[Stat] = []
    today = date.today()
    cutoff = latest_allowed_date()
    log.info("数据截止日：%s（当天 15:30 前的未收盘 K 线会被剔除）", cutoff)

    with conn.cursor() as cur:
        if only:
            cur.execute("SELECT code FROM symbols WHERE code=%s AND active=1", (only,))
        else:
            cur.execute("SELECT code FROM symbols WHERE active=1 ORDER BY code")
        symbols = [r[0] for r in cur.fetchall()]

    log.info("待处理标的 %d 只，模式=%s", len(symbols), "全量" if full else "增量")

    for sym in symbols:
        st = Stat(symbol=sym)
        stats.append(st)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT MAX(trade_date) FROM daily_bars WHERE symbol_code=%s", (sym,))
                latest = cur.fetchone()[0]

            if not full and is_up_to_date(latest, today):
                st.skipped = True
                log.info("%s 数据已新（%s），跳过", sym, latest)
                continue

            start = resolve_start_date(latest, full, today)
            raw = fetch_from_akshare(sym, start, cutoff)
            rows = normalize_rows(raw, sym, since=start, max_date=cutoff)

            if not rows:
                log.info("%s 无新增数据", sym)
                continue

            if dry_run:
                st.inserted = len(rows)
                log.info("[dry-run] %s 将写入 %d 行，%s ~ %s",
                         sym, len(rows), rows[0][1], rows[-1][1])
                continue

            with conn.cursor() as cur:
                cur.executemany(UPSERT_SQL, rows)
            conn.commit()
            st.inserted = len(rows)
            log.info("%s 写入 %d 行（%s ~ %s）", sym, len(rows), rows[0][1], rows[-1][1])
            time.sleep(REQUEST_INTERVAL)

        except Exception as e:
            conn.rollback()
            st.error = str(e)
            log.error("%s 失败：%s", sym, e)
            # 单只失败不影响其它标的，继续跑

    inserted = sum(s.inserted for s in stats)
    failed = [s.symbol for s in stats if s.error]
    log.info("完成：新增 %d 行，跳过 %d 只，失败 %d 只 %s",
             inserted, sum(1 for s in stats if s.skipped), len(failed), failed)
    return stats


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true", help="全量拉 5 年")
    ap.add_argument("--code", help="只拉某一只，如 600519")
    ap.add_argument("--dry-run", action="store_true", help="只看不写")
    args = ap.parse_args()
    run(full=args.full, only=args.code, dry_run=args.dry_run)


# ============================================================
# 纯逻辑自测：python scripts/fetch_bars.py --self-test
# ============================================================
def _self_test() -> None:
    print("=" * 70)
    print("纯逻辑自测（不访问网络，不需要 MySQL）")
    print("=" * 70)
    today = date(2026, 11, 6)      # 周五

    print("\n1) 起始日期计算")
    cases = [
        (None, False, "库里无数据 → 5 年前"),
        (date(2026, 11, 5), False, "增量 → 最新日期的下一天"),
        (date(2026, 11, 5), True, "强制全量 → 5 年前"),
    ]
    for latest, full, desc in cases:
        s = resolve_start_date(latest, full, today)
        print(f"   latest={latest} full={full} → {s}   ({desc})")

    print("\n2) 是否需要更新（容差 3 天，覆盖周末）")
    for latest, expect in [
        (None, False), (date(2026, 11, 5), True),
        (date(2026, 11, 3), True),      # 周二数据，周五看间隔 3 天仍算新
        (date(2026, 10, 30), False),    # 一周前，该更新了
    ]:
        got = is_up_to_date(latest, today)
        flag = "✓" if got == expect else "✗ 不符预期"
        print(f"   latest={latest} → is_up_to_date={got}  {flag}")

    print("\n3) 数据清洗")
    raw = [
        {"trade_date": "2026-11-02", "open": 10, "high": 11, "low": 9.5, "close": 10.5,
         "volume": 1000, "amount": 10500},
        {"trade_date": "2026-11-03", "open": 10.5, "high": 11, "low": 10, "close": 0,
         "volume": 0, "amount": 0},                       # 脏数据，应丢弃
        {"trade_date": "2026-11-04", "open": 10.6, "high": 11, "low": 10.2, "close": 10.8,
         "volume": 1200, "amount": 12960},
        {"trade_date": "2026-11-01", "open": 9, "high": 9.5, "low": 8.8, "close": 9.2,
         "volume": 900, "amount": 8280},                  # 早于 since，应丢弃
    ]
    rows = normalize_rows(raw, "600519", since=date(2026, 11, 2))
    print(f"   原始 4 行 → 清洗后 {len(rows)} 行")
    for r in rows:
        print(f"     {r[0]} {r[1]} close={r[5]} vol={r[6]}")
    assert len(rows) == 2, "应当只剩 2 行"
    assert all(r[5] > 0 for r in rows)
    assert rows[0][1] < rows[1][1], "必须按日期升序，回测引擎依赖这个前提"
    print("   断言通过：脏数据已剔除、日期已升序")

    print("\n4) UPSERT 语句可重复执行（ON DUPLICATE KEY UPDATE）")
    print("   " + " ".join(UPSERT_SQL.split())[:110] + "…")
    print("\n全部通过。注意：网络部分需要在能访问 akshare 的机器上单独验证。")


if __name__ == "__main__":
    import sys
    if "--self-test" in sys.argv:
        _self_test()
    else:
        main()

# -*- coding: utf-8 -*-
"""
积分账本 —— 整个产品里最不能写错的一段代码。

三条铁律：
1. 余额只存在 credit_accounts，任何变更必须同时写 credit_transactions，且在同一事务内。
2. 所有出账/入账都带 idempotency_key，唯一索引兜底，重复请求只生效一次。
3. 扣费时先 SELECT ... FOR UPDATE 锁住账户行，再判断余额，再扣。顺序不能反。

为什么不做「预扣冻结」：40 小时里不必引入两阶段结算。
统一用「先扣，失败则退回」，退回也走幂等键（refund:{原键}），效果等价且少一半代码。
"""
from __future__ import annotations

import math
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy import (
    Column, DateTime, Integer, MetaData, String, Table, text,
)
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import IntegrityError

metadata = MetaData()

# 只映射本模块要用的两张表，其余表结构以 schema.sql 为准
credit_accounts = Table(
    "credit_accounts", metadata,
    Column("user_id", Integer, primary_key=True),
    Column("balance", Integer, nullable=False, default=0),
    Column("frozen", Integer, nullable=False, default=0),
    Column("total_recharge", Integer, nullable=False, default=0),
    Column("total_gift", Integer, nullable=False, default=0),
    Column("total_consumed", Integer, nullable=False, default=0),
    Column("updated_at", DateTime, nullable=False, default=datetime.now),
)

credit_transactions = Table(
    "credit_transactions", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("user_id", Integer, nullable=False),
    Column("change_type", String(16), nullable=False),
    Column("amount", Integer, nullable=False),
    Column("balance_after", Integer, nullable=False),
    Column("biz_type", String(32), nullable=False),
    Column("biz_id", String(64), nullable=False, default=""),
    Column("idempotency_key", String(96), nullable=False),
    Column("remark", String(200), nullable=False, default=""),
    Column("created_at", DateTime, nullable=False, default=datetime.now),
)


# ============================================================
# 扣费价目表 —— 改价只改这里，不要散落在业务代码里
# ============================================================
PRICE = {
    "REGISTER_GIFT": 100,   # 注册赠送（入账）
    "AI_CHAT": 2,           # 单轮问答（固定兜底价）
    "DIAGNOSE": 30,         # 策略诊断报告
    "OPTIMIZE": 60,         # 策略优化（15 轮）
    "BACKTEST": 10,         # 一次回测
    "DEPLOY_DAY": 5,        # 观察项日租，每策略每天，由 scripts/charge_deploy.py 扣
    # 注意：不再单列 SIGNAL_DAY。日租已包含每日信号生成，
    # 拆成两笔会让用户看不懂"为什么同一个东西收两次钱"。
}

# ============================================================
# 按量计费参数 —— AI 类动作用这套，回测/部署这类无 token 的仍走固定价
#
# 为什么不是"完全按 token 实扣"：
#   用户在发消息之前必须知道自己最多花多少，否则不敢提问。
#   所以对外是「预扣封顶 + 按实结算 + 多退不补」：
#     发消息前预扣 CAP，回来按真实 token 算，多出来的部分退回。
#   用户看到的是"本轮最多 5 积分，实扣 2 积分"，体验是比预期便宜。
#   反过来"预估 2 实扣 8"会直接导致流失，宁可我们偶尔少收。
# ============================================================
CNY_PER_POINT = 0.10        # 1 积分 = 0.1 元
TOKEN_PRICE_IN = 2.0        # 元 / 百万输入 token，拿到 MiMo 实价后改这里
TOKEN_PRICE_OUT = 8.0       # 元 / 百万输出 token
MARKUP = 12.0               # 成本加价倍率。12 倍 ≈ 毛利 92%，且正常一问落在 2 积分
POINTS_FLOOR = 1            # 最低扣 1 积分，避免极短问答变成 0 元单

# 各业务的预扣封顶值（也是给用户看的"最多花费"）
POINTS_CAP = {
    "AI_CHAT": 5,
    "DIAGNOSE": 60,
    "OPTIMIZE": 120,
}

# ⚠️ 成本失控的唯一真正防线在这里，不在封顶。
# 极端情况：用户贴 20 万 token，成本约 0.56 元，封顶只能收 0.5 元 —— 你是亏的。
# 所以必须在调模型之前截断输入，超限就让用户分段，不要指望封顶能兜住成本。
MAX_INPUT_TOKENS = {
    "AI_CHAT": 8000,        # 约 1.2 万字，日常问答绰绰有余
    "DIAGNOSE": 30000,
    "OPTIMIZE": 80000,
}
MAX_HISTORY_TURNS = 6       # 只带最近 6 轮上下文，否则 token 随对话长度线性膨胀


def quote_tokens(tokens_in: int, tokens_out: int, biz_type: str = "AI_CHAT") -> int:
    """按真实 token 用量折算应扣积分，含加价与封顶。"""
    cost_cny = (tokens_in / 1_000_000 * TOKEN_PRICE_IN
                + tokens_out / 1_000_000 * TOKEN_PRICE_OUT)
    points = math.ceil(cost_cny * MARKUP / CNY_PER_POINT)
    points = max(POINTS_FLOOR, points)
    cap = POINTS_CAP.get(biz_type)
    if cap:
        points = min(points, cap)
    return points


class CreditError(Exception):
    """积分相关业务异常基类"""


class InsufficientCredits(CreditError):
    def __init__(self, need: int, have: int):
        self.need, self.have = need, have
        super().__init__(f"积分不足：需要 {need}，当前 {have}")


class AccountNotFound(CreditError):
    pass


@dataclass
class LedgerEntry:
    balance_after: int
    amount: int
    duplicated: bool = False   # True 表示幂等命中，本次未真正扣费


def new_idem_key(prefix: str = "") -> str:
    return f"{prefix}{uuid.uuid4().hex}" if prefix else uuid.uuid4().hex


class CreditsService:
    """所有积分操作都从这里进，禁止业务代码直接 UPDATE 余额"""

    def __init__(self, engine: Engine):
        self.engine = engine

    # ---------------- 查询 ----------------
    @staticmethod
    def get_balance(conn: Connection, user_id: int) -> int:
        row = conn.execute(
            text("SELECT balance FROM credit_accounts WHERE user_id=:u"),
            {"u": user_id},
        ).first()
        if row is None:
            raise AccountNotFound(f"用户 {user_id} 没有积分账户")
        return int(row[0])

    @staticmethod
    def find_by_idem(conn: Connection, key: str) -> Optional[int]:
        row = conn.execute(
            text("SELECT balance_after FROM credit_transactions WHERE idempotency_key=:k"),
            {"k": key},
        ).first()
        return int(row[0]) if row else None

    # ---------------- 开户 ----------------
    def open_account(self, conn: Connection, user_id: int, gift: Optional[int] = None) -> int:
        """
        建账户 + 发注册赠送。重复调用安全（幂等键固定为 register:{user_id}）。
        """
        gift = PRICE["REGISTER_GIFT"] if gift is None else gift
        key = f"register:{user_id}"
        hit = self.find_by_idem(conn, key)
        if hit is not None:
            return hit

        if self._ensure_account_row(conn, user_id, init_balance=gift, init_gift=gift):
            self._write_tx(
                conn, user_id=user_id, change_type="GIFT", amount=gift,
                balance_after=gift, biz_type="REGISTER", biz_id=str(user_id),
                idem=key, remark="注册赠送",
            )
            return gift
        # 账户已存在（并发注册），直接返回当前余额
        return self.get_balance(conn, user_id)

    # ---------------- 入账 ----------------
    def recharge(self, conn: Connection, user_id: int, points: int,
                 order_no: str, remark: str = "") -> LedgerEntry:
        """
        充值入账。order_no 直接当幂等键，重复回调不会重复加积分。
        """
        if points <= 0:
            raise ValueError("充值积分必须大于 0")
        key = f"recharge:{order_no}"
        hit = self.find_by_idem(conn, key)
        if hit is not None:
            return LedgerEntry(balance_after=hit, amount=0, duplicated=True)

        self._ensure_account_row(conn, user_id)
        self._lock_account(conn, user_id)
        balance = self.get_balance(conn, user_id)
        after = balance + points

        conn.execute(
            text(
                "UPDATE credit_accounts SET balance=:b, total_recharge=total_recharge+:p "
                "WHERE user_id=:u"
            ),
            {"b": after, "p": points, "u": user_id},
        )
        self._write_tx(
            conn, user_id=user_id, change_type="RECHARGE", amount=points,
            balance_after=after, biz_type="RECHARGE", biz_id=order_no,
            idem=key, remark=remark or "充值",
        )
        return LedgerEntry(balance_after=after, amount=points)

    # ---------------- 出账（核心） ----------------
    def consume(self, conn: Connection, user_id: int, points: int,
                biz_type: str, biz_id: str = "",
                idem: Optional[str] = None, remark: str = "") -> LedgerEntry:
        """
        扣积分。调用方必须已经开好事务（自己 begin / commit）。
        返回 LedgerEntry；duplicated=True 表示这次是重复请求，没有再扣。

        并发安全：
          SELECT ... FOR UPDATE 锁行 → 判断 → UPDATE，三步在同一个事务里，
          InnoDB 会阻塞其它事务直到本事务提交，余额不可能被扣成负数。
        """
        if points <= 0:
            raise ValueError("扣费积分必须大于 0")
        key = idem or new_idem_key(f"consume:{biz_type}:")
        hit = self.find_by_idem(conn, key)
        if hit is not None:
            return LedgerEntry(balance_after=hit, amount=0, duplicated=True)

        self._lock_account(conn, user_id)          # 行锁
        balance = self.get_balance(conn, user_id)  # 锁内读，值可信
        if balance < points:
            raise InsufficientCredits(need=points, have=balance)

        after = balance - points
        # 条件 UPDATE：数据库层面的第二道防线。
        # 本机 MySQL 8.0.12 的 CHECK 约束【只解析不强制执行】（8.0.16 才真正生效），
        # 所以 schema 里的 ck_balance 靠不住，必须靠这里的 WHERE balance >= :p 兜底。
        # 即便行锁因某种原因失效，这条语句也绝不会把余额扣成负数。
        res = conn.execute(
            text(
                "UPDATE credit_accounts SET balance = balance - :p, "
                "total_consumed = total_consumed + :p "
                "WHERE user_id = :u AND balance >= :p"
            ),
            {"p": points, "u": user_id},
        )
        if res.rowcount == 0:
            raise InsufficientCredits(need=points, have=balance)
        try:
            self._write_tx(
                conn, user_id=user_id, change_type="CONSUME", amount=-points,
                balance_after=after, biz_type=biz_type, biz_id=biz_id,
                idem=key, remark=remark or biz_type,
            )
        except IntegrityError:
            # 极小概率：两个并发请求同时通过了上面的查重，唯一索引挡住后到的那个
            raise CreditError("重复的扣费请求，请刷新后重试")
        return LedgerEntry(balance_after=after, amount=points)

    def refund(self, conn: Connection, user_id: int, points: int,
               source_idem: str, remark: str = "任务失败退回") -> LedgerEntry:
        """
        退回。幂等键派生自原扣费键，天然保证「一次扣费最多退一次」。
        """
        key = f"refund:{source_idem}"
        hit = self.find_by_idem(conn, key)
        if hit is not None:
            return LedgerEntry(balance_after=hit, amount=0, duplicated=True)

        self._lock_account(conn, user_id)
        balance = self.get_balance(conn, user_id)
        after = balance + points
        conn.execute(
            text("UPDATE credit_accounts SET balance=:b WHERE user_id=:u"),
            {"b": after, "u": user_id},
        )
        self._write_tx(
            conn, user_id=user_id, change_type="REFUND", amount=points,
            balance_after=after, biz_type="REFUND", biz_id=source_idem,
            idem=key, remark=remark,
        )
        return LedgerEntry(balance_after=after, amount=points)

    # ---------------- 按量计费：预扣 + 结算 ----------------
    def preauth(self, conn: Connection, user_id: int, biz_type: str,
                biz_id: str, remark: str = "") -> LedgerEntry:
        """
        AI 调用前预扣「封顶值」。幂等键 = pre:{biz_type}:{biz_id}。
        用户在前端看到的就是这个数："本轮最多消耗 X 积分"。
        """
        cap = POINTS_CAP.get(biz_type)
        if not cap:
            raise ValueError(f"{biz_type} 不支持按量预扣，请用固定价 consume()")
        return self.consume(
            conn, user_id, cap, biz_type, biz_id,
            idem=f"pre:{biz_type}:{biz_id}",
            remark=remark or f"预扣（封顶 {cap}）",
        )

    def settle(self, conn: Connection, user_id: int, biz_type: str, biz_id: str,
               tokens_in: int, tokens_out: int) -> Dict[str, Any]:
        """
        AI 调用后按真实用量结算，多退不补。

        返回 {charged, refunded, balance}，charged 是最终实扣。
        重复调用安全：结算键 settle:{biz_type}:{biz_id} 唯一。
        """
        cap = POINTS_CAP[biz_type]
        actual = quote_tokens(tokens_in, tokens_out, biz_type)
        give_back = max(0, cap - actual)
        balance = self.get_balance(conn, user_id)
        refunded = 0

        if give_back > 0:
            entry = self.refund(
                conn, user_id, give_back,
                source_idem=f"pre:{biz_type}:{biz_id}",
                remark=f"按实结算退回（实扣 {actual}）",
            )
            refunded = 0 if entry.duplicated else give_back
            balance = entry.balance_after

        return {"charged": actual, "refunded": refunded, "balance": balance}

    def fail_release(self, conn: Connection, user_id: int, biz_type: str,
                     biz_id: str) -> LedgerEntry:
        """AI 调用彻底失败时，把预扣全额退回。"""
        cap = POINTS_CAP[biz_type]
        return self.refund(
            conn, user_id, cap,
            source_idem=f"pre:{biz_type}:{biz_id}",
            remark="调用失败，预扣全额退回",
        )

    # ---------------- 内部 ----------------
    @staticmethod
    def _lock_account(conn: Connection, user_id: int) -> None:
        """
        锁住账户行。MySQL/InnoDB 下 FOR UPDATE 是真正的行级排他锁，
        是余额不会被并发扣成负数的唯一保证。

        注意：SQLite 不支持 FOR UPDATE，本地用 SQLite 跑自测时这一步是空操作，
             所以「并发扣费」的验证必须在 MySQL 上做一遍，不能只看 SQLite 结果。
        """
        sql = "SELECT user_id FROM credit_accounts WHERE user_id=:u"
        if conn.dialect.name in ("mysql", "mariadb"):
            sql += " FOR UPDATE"
        row = conn.execute(text(sql), {"u": user_id}).first()
        if row is None:
            raise AccountNotFound(f"用户 {user_id} 没有积分账户")

    @staticmethod
    def _ensure_account_row(conn: Connection, user_id: int,
                            init_balance: int = 0, init_gift: int = 0) -> bool:
        """
        确保账户行存在。返回 True 表示本次真的新建了。
        用 savepoint 包住 INSERT，避免并发下的 IntegrityError 污染外层事务；
        同时兼容 MySQL / SQLite（不写 ON DUPLICATE KEY）。
        """
        exists = conn.execute(
            text("SELECT 1 FROM credit_accounts WHERE user_id=:u"), {"u": user_id}
        ).first()
        if exists:
            return False
        try:
            with conn.begin_nested():
                conn.execute(
                    text(
                        "INSERT INTO credit_accounts (user_id, balance, total_gift) "
                        "VALUES (:u, :b, :g)"
                    ),
                    {"u": user_id, "b": init_balance, "g": init_gift},
                )
            return True
        except IntegrityError:
            return False

    @staticmethod
    def _write_tx(conn: Connection, *, user_id: int, change_type: str, amount: int,
                  balance_after: int, biz_type: str, biz_id: str,
                  idem: str, remark: str) -> None:
        conn.execute(
            credit_transactions.insert().values(
                user_id=user_id,
                change_type=change_type,
                amount=amount,
                balance_after=balance_after,
                biz_type=biz_type,
                biz_id=biz_id,
                idempotency_key=idem,
                remark=remark[:200],
            )
        )


# ============================================================
# 一个薄封装，给路由层用，省掉每个接口都写 begin/commit
# ============================================================
def charge(engine: Engine, user_id: int, biz_type: str, biz_id: str = "",
           points: Optional[int] = None, idem: Optional[str] = None,
           remark: str = "") -> LedgerEntry:
    """
    用法：
        entry = charge(engine, uid, "BACKTEST", job_no)      # 自动取价目表
        entry = charge(engine, uid, "AI_CHAT", msg_id, points=2)

    扣费失败抛 InsufficientCredits，路由层捕获后返回 402。
    """
    cost = PRICE[biz_type] if points is None else points
    with engine.begin() as conn:
        return CreditsService(engine).consume(
            conn, user_id, cost, biz_type, biz_id, idem, remark
        )


# ============================================================
# 自测：用 SQLite 验证逻辑（生产用 MySQL，见 schema.sql）
# ============================================================
if __name__ == "__main__":
    from sqlalchemy import create_engine

    eng = create_engine("sqlite://")

    with eng.begin() as c:
        # SQLite 建最小表（MySQL 版本在 schema.sql 里，字段更全）
        c.execute(text("""
            CREATE TABLE credit_accounts (
              user_id INTEGER PRIMARY KEY,
              balance INTEGER NOT NULL DEFAULT 0,
              frozen INTEGER NOT NULL DEFAULT 0,
              total_recharge INTEGER NOT NULL DEFAULT 0,
              total_gift INTEGER NOT NULL DEFAULT 0,
              total_consumed INTEGER NOT NULL DEFAULT 0,
              updated_at DATETIME
            )"""))
        c.execute(text("""
            CREATE TABLE credit_transactions (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              user_id INTEGER NOT NULL,
              change_type VARCHAR(16) NOT NULL,
              amount INTEGER NOT NULL,
              balance_after INTEGER NOT NULL,
              biz_type VARCHAR(32) NOT NULL,
              biz_id VARCHAR(64) NOT NULL DEFAULT '',
              idempotency_key VARCHAR(96) NOT NULL UNIQUE,
              remark VARCHAR(200) NOT NULL DEFAULT '',
              created_at DATETIME
            )"""))

    svc = CreditsService(eng)
    uid = 1

    def show(label, fn):
        try:
            with eng.begin() as c:
                r = fn(c)
            print(f"  {label:<34} → 余额 {r.balance_after:>4}  "
                  f"{'（幂等命中，未扣费）' if r.duplicated else ''}")
        except CreditError as e:
            with eng.begin() as c:
                bal = svc.get_balance(c, uid)
            print(f"  {label:<34} → 拒绝：{e}  |  余额仍为 {bal}")

    print("=" * 72)
    print("场景 1：注册送 100，再消费")
    print("=" * 72)
    show("注册赠送", lambda c: LedgerEntry(svc.open_account(c, uid), 0))
    show("重复调用注册（幂等）", lambda c: LedgerEntry(svc.open_account(c, uid), 0))
    show("问答扣 2", lambda c: svc.consume(c, uid, PRICE["AI_CHAT"], "AI_CHAT", "m1"))
    show("诊断扣 30", lambda c: svc.consume(c, uid, PRICE["DIAGNOSE"], "DIAGNOSE", "j1"))
    show("优化扣 60", lambda c: svc.consume(c, uid, PRICE["OPTIMIZE"], "OPTIMIZE", "o1"))
    show("再扣 60（余额不足）", lambda c: svc.consume(c, uid, 60, "OPTIMIZE", "o2"))

    print()
    print("=" * 72)
    print("场景 2：幂等键重复提交（模拟用户狂点提交按钮 / 支付重复回调）")
    print("=" * 72)
    show("先充值 540 让余额够用", lambda c: svc.recharge(c, uid, 540, "ORD20260101001"))
    for i in range(3):
        show(f"第 {i+1} 次提交同一 idem", lambda c: svc.consume(
            c, uid, 10, "BACKTEST", "j9", idem="fixed-key-001"))

    print()
    print("=" * 72)
    print("场景 3：失败退回")
    print("=" * 72)
    show("同一订单号重复回调（幂等）", lambda c: svc.recharge(c, uid, 540, "ORD20260101001"))
    show("回测扣 10", lambda c: svc.consume(c, uid, 10, "BACKTEST", "j10", idem="bt-key-1"))
    show("回测失败退回", lambda c: svc.refund(c, uid, 10, "bt-key-1"))
    show("重复退回（幂等）", lambda c: svc.refund(c, uid, 10, "bt-key-1"))

    print()
    print("=" * 72)
    print("场景 4：账目一致性核对（余额 == 流水求和）")
    print("=" * 72)
    with eng.begin() as c:
        bal = svc.get_balance(c, uid)
        s = c.execute(text(
            "SELECT COALESCE(SUM(amount),0) FROM credit_transactions WHERE user_id=:u"
        ), {"u": uid}).scalar()
        n = c.execute(text(
            "SELECT COUNT(*) FROM credit_transactions WHERE user_id=:u"
        ), {"u": uid}).scalar()
        last_after = c.execute(text(
            "SELECT balance_after FROM credit_transactions WHERE user_id=:u "
            "ORDER BY id DESC LIMIT 1"
        ), {"u": uid}).scalar()
    print(f"  账户余额      {bal}")
    print(f"  流水求和      {s}")
    print(f"  最后一条余额  {last_after}")
    print(f"  流水条数      {n}")
    print(f"  一致性        {'通过' if bal == s == last_after else '不一致，有问题！'}")

    print()
    print("流水明细：")
    with eng.begin() as c:
        rows = c.execute(text(
            "SELECT change_type, amount, balance_after, biz_type, remark "
            "FROM credit_transactions WHERE user_id=:u ORDER BY id"
        ), {"u": uid}).all()
    print(f"  {'类型':<10}{'变动':>6}{'余额':>7}  {'业务':<12}备注")
    for r in rows:
        print(f"  {r[0]:<10}{r[1]:>6}{r[2]:>7}  {r[3]:<12}{r[4]}")

    print()
    print("=" * 72)
    print("场景 5：按量计费（预扣封顶 → 按实结算 → 多退不补）")
    print("=" * 72)
    print(f"  价格假设：输入 ¥{TOKEN_PRICE_IN}/M，输出 ¥{TOKEN_PRICE_OUT}/M，"
          f"加价 {MARKUP} 倍，问答封顶 {POINTS_CAP['AI_CHAT']} 积分")
    print()
    samples = [
        ("很短的问题", 300, 150),
        ("正常一问", 2000, 1000),
        ("贴了长文本", 20000, 4000),
        ("极端超长输入", 200000, 20000),
    ]
    print(f"  {'场景':<14}{'输入tok':>9}{'输出tok':>9}{'应扣积分':>10}")
    for label, ti, to in samples:
        print(f"  {label:<14}{ti:>9}{to:>9}{quote_tokens(ti, to):>10}")

    print()
    print("  实际走一遍：预扣 → 短问答 → 结算退回")
    show("预扣问答封顶", lambda c: svc.preauth(c, uid, "AI_CHAT", "msg-100"))
    with eng.begin() as c:
        r = svc.settle(c, uid, "AI_CHAT", "msg-100", tokens_in=2000, tokens_out=1000)
    print(f"    结算：实扣 {r['charged']}，退回 {r['refunded']}，余额 {r['balance']}")
    with eng.begin() as c:
        r2 = svc.settle(c, uid, "AI_CHAT", "msg-100", tokens_in=2000, tokens_out=1000)
    print(f"    重复结算：实扣 {r2['charged']}，退回 {r2['refunded']}（应为 0），余额 {r2['balance']}")

    print()
    print("  长文本场景：应扣会顶到封顶，不会多收")
    show("预扣 msg-200", lambda c: svc.preauth(c, uid, "AI_CHAT", "msg-200"))
    with eng.begin() as c:
        r3 = svc.settle(c, uid, "AI_CHAT", "msg-200", tokens_in=200000, tokens_out=20000)
    print(f"    结算：实扣 {r3['charged']}（= 封顶），退回 {r3['refunded']}，余额 {r3['balance']}")

    print()
    print("  失败场景：预扣全额退回")
    show("预扣 msg-300", lambda c: svc.preauth(c, uid, "AI_CHAT", "msg-300"))
    show("调用失败退回", lambda c: svc.fail_release(c, uid, "AI_CHAT", "msg-300"))
    show("重复失败退回（幂等）", lambda c: svc.fail_release(c, uid, "AI_CHAT", "msg-300"))

    print()
    with eng.begin() as c:
        bal = svc.get_balance(c, uid)
        s = c.execute(text(
            "SELECT COALESCE(SUM(amount),0) FROM credit_transactions WHERE user_id=:u"
        ), {"u": uid}).scalar()
    print(f"  最终一致性：余额 {bal}，流水求和 {s} → {'通过' if bal == s else '不一致！'}")

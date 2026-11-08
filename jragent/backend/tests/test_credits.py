# -*- coding: utf-8 -*-
"""积分账本测试：幂等、余额保护、退回、按量结算、账实一致"""
import pytest
from sqlalchemy import text

from app.services.credits import (
    CreditsService, InsufficientCredits, PRICE, quote_tokens,
)


def test_open_account_gift(svc, ledger_engine):
    with ledger_engine.begin() as c:
        bal = svc.open_account(c, 1)
    assert bal == PRICE["REGISTER_GIFT"]


def test_open_account_idempotent(svc, ledger_engine):
    with ledger_engine.begin() as c:
        svc.open_account(c, 1)
        bal = svc.open_account(c, 1)      # 重复开户不得重复送
    assert bal == PRICE["REGISTER_GIFT"]


def test_consume_and_balance(svc, ledger_engine, uid):
    with ledger_engine.begin() as c:
        entry = svc.consume(c, uid, 30, "DIAGNOSE", "j1")
    assert entry.balance_after == PRICE["REGISTER_GIFT"] - 30


def test_insufficient_rejected(svc, ledger_engine, uid):
    with pytest.raises(InsufficientCredits):
        with ledger_engine.begin() as c:
            svc.consume(c, uid, 999999, "OPTIMIZE", "x")
    # 失败后余额不变
    with ledger_engine.begin() as c:
        assert svc.get_balance(c, uid) == PRICE["REGISTER_GIFT"]


def test_idempotent_consume(svc, ledger_engine, uid):
    """同一幂等键重复提交只扣一次"""
    for _ in range(3):
        with ledger_engine.begin() as c:
            entry = svc.consume(c, uid, 10, "BACKTEST", "j9", idem="fixed-key")
    assert entry.duplicated is True
    with ledger_engine.begin() as c:
        assert svc.get_balance(c, uid) == PRICE["REGISTER_GIFT"] - 10


def test_recharge_idempotent(svc, ledger_engine, uid):
    for _ in range(2):
        with ledger_engine.begin() as c:
            svc.recharge(c, uid, 540, "ORDER-001")
    with ledger_engine.begin() as c:
        # 同一订单号重复回调不得重复加积分
        assert svc.get_balance(c, uid) == PRICE["REGISTER_GIFT"] + 540


def test_refund_once(svc, ledger_engine, uid):
    with ledger_engine.begin() as c:
        svc.consume(c, uid, 10, "BACKTEST", "j10", idem="bt-1")
    for _ in range(2):
        with ledger_engine.begin() as c:
            svc.refund(c, uid, 10, source_idem="bt-1")
    with ledger_engine.begin() as c:
        assert svc.get_balance(c, uid) == PRICE["REGISTER_GIFT"]


def test_metered_settle(svc, ledger_engine, uid):
    """预扣封顶 → 按实结算 → 多退不补"""
    with ledger_engine.begin() as c:
        svc.preauth(c, uid, "AI_CHAT", "m1")
    with ledger_engine.begin() as c:
        r = svc.settle(c, uid, "AI_CHAT", "m1", tokens_in=2000, tokens_out=1000)
    assert r["charged"] >= 1
    assert r["refunded"] == PRICE_CAP() - r["charged"]

    # 重复结算不得重复退款
    with ledger_engine.begin() as c:
        r2 = svc.settle(c, uid, "AI_CHAT", "m1", tokens_in=2000, tokens_out=1000)
    assert r2["refunded"] == 0


def PRICE_CAP():
    from app.services.credits import POINTS_CAP
    return POINTS_CAP["AI_CHAT"]


def test_fail_release(svc, ledger_engine, uid):
    with ledger_engine.begin() as c:
        svc.preauth(c, uid, "AI_CHAT", "m2")
        before = svc.get_balance(c, uid)
    with ledger_engine.begin() as c:
        svc.fail_release(c, uid, "AI_CHAT", "m2")
        after = svc.get_balance(c, uid)
    assert after == before + PRICE_CAP()


def test_ledger_consistency(svc, ledger_engine, uid):
    """任何操作序列后，余额必须等于流水求和"""
    with ledger_engine.begin() as c:
        svc.consume(c, uid, 30, "DIAGNOSE", "a")
        svc.recharge(c, uid, 540, "ORDER-9")
        svc.consume(c, uid, 10, "BACKTEST", "b", idem="k1")
        svc.refund(c, uid, 10, source_idem="k1")
        svc.preauth(c, uid, "AI_CHAT", "m9")
        svc.settle(c, uid, "AI_CHAT", "m9", 1000, 500)

        bal = svc.get_balance(c, uid)
        s = c.execute(text(
            "SELECT COALESCE(SUM(amount),0) FROM credit_transactions WHERE user_id=:u"
        ), {"u": uid}).scalar()
        last = c.execute(text(
            "SELECT balance_after FROM credit_transactions WHERE user_id=:u "
            "ORDER BY id DESC LIMIT 1"), {"u": uid}).scalar()
    assert bal == s == last


def test_quote_tokens_capped():
    """极端输入也绝不突破封顶，且永不为 0"""
    assert quote_tokens(100, 50) >= 1
    assert quote_tokens(10_000_000, 1_000_000) == PRICE_CAP()


def test_zero_or_negative_rejected(svc, ledger_engine, uid):
    with pytest.raises(ValueError):
        with ledger_engine.begin() as c:
            svc.consume(c, uid, 0, "AI_CHAT", "x")
    with pytest.raises(ValueError):
        with ledger_engine.begin() as c:
            svc.consume(c, uid, -5, "AI_CHAT", "x")

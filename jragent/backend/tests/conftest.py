# -*- coding: utf-8 -*-
"""
pytest 共享 fixture。
积分账本用 SQLite 内存库跑，不依赖 MySQL，CI 与现场都能跑。
"""
import os
import sys

import pytest
from sqlalchemy import create_engine, text

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.credits import CreditsService  # noqa: E402


@pytest.fixture()
def ledger_engine():
    """每个测试一个独立的内存库，互不污染"""
    eng = create_engine("sqlite://")
    with eng.begin() as c:
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
    return eng


@pytest.fixture()
def svc(ledger_engine):
    return CreditsService(ledger_engine)


@pytest.fixture()
def uid(svc, ledger_engine):
    """预置一个已开户、有余额的用户"""
    with ledger_engine.begin() as c:
        svc.open_account(c, 1)
    return 1


def make_bars(n=300, drift=0.0004, vol=0.018, seed=42):
    """合成 K 线，避免测试依赖真实行情"""
    import math
    import random
    from app.services.backtest import Bar
    rng = random.Random(seed)
    price, out = 100.0, []
    for i in range(n):
        price = max(1.0, price * (1 + rng.gauss(drift, vol)))
        out.append(Bar(f"D{i:04d}", price * 0.999, price * 1.01, price * 0.99, price, 1e6))
    return out

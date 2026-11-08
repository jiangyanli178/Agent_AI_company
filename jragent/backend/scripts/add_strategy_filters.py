# -*- coding: utf-8 -*-
"""
给 strategies 表补 filters 列（幂等）。

为什么需要它：
公共策略库里原先只能存「策略类型 + 参数」，装不下过滤条件。
但 AI 优化出来的策略往往是「RSI + ADX>20.5」这种带条件的形态，
存不进过滤条件就只能把条件写进文字说明 ——
用户点进去跑出来的结果和描述对不上，正是之前报告画错策略的同一个坑。

用法：python scripts/add_strategy_filters.py
"""
import os
import sys

import pymysql

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

HOST = os.getenv("DB_HOST", "127.0.0.1")
USER = os.getenv("DB_USER", "root")
PWD = os.getenv("DB_PASSWORD", "root")
DB = os.getenv("DB_NAME", "quant_shop")


def main():
    conn = pymysql.connect(host=HOST, user=USER, password=PWD, database=DB)
    cur = conn.cursor()
    cur.execute("SHOW COLUMNS FROM strategies LIKE 'filters'")
    if cur.fetchone():
        print("filters 列已存在，无需改动")
    else:
        cur.execute(
            "ALTER TABLE strategies ADD COLUMN filters JSON NULL "
            "COMMENT '附加过滤条件（因子+阈值），回测时自动叠加' AFTER default_params"
        )
        conn.commit()
        print("已添加 strategies.filters 列")

    cur.execute("SELECT code, filters FROM strategies ORDER BY id LIMIT 3")
    for row in cur.fetchall():
        print(" ", row[0], "|", row[1])
    conn.close()


if __name__ == "__main__":
    main()

"""
把一批公开、经典的策略配置和因子补进库里。

原则：
1. **只填真实存在的东西**。策略都是教科书上常见的参数组合（5/20、20/120、
   斐波那契 8/21…），因子都是学术/实务里标准的计算公式，
   没有一个是编出来凑数的。
2. **幂等**：按 code 判断，已存在就更新文案，不重复插入。
3. 写库只负责"目录"；真正的计算在 `extended_factors.py` / `backtest.py`，
   所以这里填的每个因子都必须能算出数、画得出图。

用法：
    python scripts/seed_library.py           # 补库并打印统计
    python scripts/seed_library.py --dry     # 只看会写什么，不落库
"""
from __future__ import annotations

import json
import os
import sys

import pymysql

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ============================================================
# 因子：code, 名称, 分类, 一句话, 怎么算, 怎么用, 常用参数, 排序
# ============================================================
FACTORS = [
    ("roc", "ROC 变动率", "趋势",
     "现在比 N 天前涨了百分之几",
     "(今收 ÷ N天前收盘 − 1) × 100",
     "大于 0 说明这段在涨，绝对值越大动能越强。缺点：看不出中间波动有多大，"
     "一只慢慢涨 10% 和一只暴涨暴跌后收在 +10% 的票，ROC 一样",
     "常用 12 / 20 / 60", 30),

    ("momentum", "动量 Momentum", "趋势",
     "现在的价格是 N 天前的几倍",
     "今收 ÷ N天前收盘（1.0 = 没动，1.1 = 涨了 10%）",
     "和 ROC 是同一件事，只是不换算成百分比。做动量策略时用这个更直观："
     "大于 1 才考虑买入，越大越强",
     "常用 20 / 60 / 120", 31),

    ("volatility", "年化波动率", "波动",
     "这只票平时晃得有多厉害，换算成一年的幅度",
     "日收益率的标准差 × √252 × 100",
     "20% 左右算温和，40% 以上很颠。波动大不等于会跌，但你的持仓体验会很差、"
     "回撤也会更深。仓位应该按波动率反比来配",
     "常用 20 / 60", 32),

    ("mfi", "MFI 资金流量", "量能",
     "把成交量加进 RSI 的思路：涨的时候量大算流入，跌的时候量大算流出",
     "典型价=(高+低+收)/3，乘成交量得资金流；N 日流入÷流出换算成 0~100",
     "80 以上过热、20 以下超卖。比 RSI 多一层量能确认，"
     "但震荡市同样会钝化，不能单用",
     "常用 14", 33),

    ("psy", "心理线 PSY", "震荡",
     "最近 N 天里有几天是上涨的",
     "N 日内上涨天数 ÷ N × 100",
     "低于 25 说明连跌得多、情绪悲观；高于 75 说明追的人多。"
     "它只量情绪不判方向，适合当反向参考",
     "常用 12", 34),

    ("aroon_up", "阿隆上线 Aroon Up", "趋势",
     "N 天内的最高点，是刚创的，还是很久以前创的",
     "(N − 距最高点的天数) ÷ N × 100",
     "接近 100 = 刚创新高、上升趋势强；接近 0 = 高点已经过去很久，趋势在走弱。"
     "比均线更早反映「多久没新高了」",
     "常用 25", 35),

    ("zscore", "价格 Z 分数", "震荡",
     "现价偏离均线几个标准差",
     "(现价 − N日均价) ÷ N日标准差",
     "±2 以上算异常偏离，是均值回归的常用入场依据。"
     "但强趋势里能长期停在 2 以上，逆势用会连续被打脸",
     "常用 20", 36),

    ("vwap_dev", "偏离筹码均价", "量能",
     "现价比这段时间大家的平均买入成本高多少",
     "(今收 − N日成交量加权均价) ÷ 该均价 × 100",
     "正数 = 多数人浮盈（抛压可能变大），负数 = 多数人被套（反弹时有解套盘）。"
     "比单纯看均线多考虑了成交量分布",
     "常用 20", 37),

    ("skew", "收益偏度", "波动",
     "涨跌分布是「偶尔暴涨」还是「偶尔暴跌」",
     "日收益率的三阶矩 ÷ 标准差的立方",
     "正偏 = 小跌多、大涨少；负偏 = 小涨多、大跌狠。"
     "负偏的票平时看着稳，一跌就很凶，更要控仓位",
     "常用 20", 38),

    ("volume_up", "放量", "量能",
     "今天成交量明显高于最近的均量",
     "今量 ≥ N日均量 × K 倍",
     "突破要有量配合才算数。但要注意：利好兑现时的巨量往往是出货，不是启动",
     "N=20，K=1.5", 39),

    ("gap_up", "跳空高开", "形态",
     "今开比昨收高出一截，中间留了个缺口",
     "今开 ≥ 昨收 × (1 + P%)",
     "常出现在消息面刺激之后，容易回补。追第一根跳空的风险很高",
     "P=1.0", 40),

    ("new_high", "创 N 日新高", "形态",
     "收盘价站上最近 N 天的最高价",
     "今收 ≥ 前 N 日最高价的最大值",
     "新高是新趋势的确认，也是追高风险最大的位置。"
     "配合放量更有意义，缩量新高要警惕",
     "常用 60 / 120 / 250", 41),

    ("ma_golden_cross", "当日金叉", "趋势",
     "快线今天刚刚上穿慢线，只有这一天为真",
     "昨日快线≤慢线 且 今日快线>慢线",
     "和「快线在慢线上方」不是一回事：后者是一个持续状态，"
     "这个只在交叉当天成立，适合做入场触发条件",
     "快 5 / 慢 20", 42),

    ("rsi_oversold", "RSI 超卖", "震荡",
     "RSI 掉到阈值以下",
     "RSI(N) < 阈值",
     "超卖不等于会反弹。下跌趋势里 RSI 能长期趴在 30 以下，"
     "这就是为什么单靠超卖抄底经常越抄越亏",
     "N=14，阈值 30", 43),

    ("close_above_vwap", "站上筹码均价", "量能",
     "收盘价高于成交量加权均价",
     "今收 > N日 VWAP",
     "站上均价说明近期买入的人整体浮盈，抛压相对小。"
     "跌破则说明这批人被套，反弹时会遇到解套盘",
     "N=20", 44),
]


# ============================================================
# 策略：code, 名称, 分类, 类型, 一句话, 详细说明, 参数, 默认值, 难度, 标签
# ============================================================
MA_SCHEMA = {"fast": {"type": "int", "min": 2, "max": 120, "default": 5},
             "slow": {"type": "int", "min": 5, "max": 250, "default": 20},
             "hysteresis": {"type": "float", "min": 0, "max": 20, "default": 0}}

STRATEGIES = [
    ("ma_cross_3_10", "3/10 均线交叉", "趋势", "ma_cross",
     "最快的均线组合，信号极密，手续费和假信号都多",
     "3 日线对价格极其敏感，几乎贴着 K 线走，10 日线稍慢。"
     "优点是最早发现转向，缺点是震荡市里一天一个信号，交易成本会吃掉大部分收益。"
     "适合日内或超短线的人，不适合拿着不动。",
     {**MA_SCHEMA,
      "fast": {"type": "int", "min": 2, "max": 10, "default": 3},
      "slow": {"type": "int", "min": 5, "max": 30, "default": 10}},
     {"fast": 3, "slow": 10, "hysteresis": 0}, 3, "短线,高频,趋势"),

    ("ma_cross_8_21", "8/21 均线交叉", "趋势", "ma_cross",
     "斐波那契参数的均线组合，介于短线和中线之间",
     "8 和 21 是斐波那契数列里的相邻项，在海外技术分析里很常用。"
     "比 5/20 稍慢一点，信号少一些，但对噪声的容忍度更好。"
     "这类「好看的数字」并不能保证更赚，主要还是看行情适不适合趋势策略。",
     {**MA_SCHEMA,
      "fast": {"type": "int", "min": 2, "max": 30, "default": 8},
      "slow": {"type": "int", "min": 10, "max": 120, "default": 21}},
     {"fast": 8, "slow": 21, "hysteresis": 0}, 2, "趋势,斐波那契"),

    ("ma_cross_20_50", "20/50 均线交叉", "趋势", "ma_cross",
     "中期趋势组合，月线与季线的交叉",
     "20 日约一个月、50 日约一个季度。信号一年大概几次，"
     "能过滤掉大部分日内噪声，但也意味着进场更晚、离场更晚。"
     "适合能忍住不频繁操作的人。",
     {**MA_SCHEMA,
      "fast": {"type": "int", "min": 5, "max": 60, "default": 20},
      "slow": {"type": "int", "min": 20, "max": 250, "default": 50}},
     {"fast": 20, "slow": 50, "hysteresis": 0}, 2, "中线,趋势"),

    ("ma_cross_60_250", "60/250 年线交叉", "趋势", "ma_cross",
     "牛熊分界：季线上穿年线通常被当成长期趋势转多",
     "60 日约一个季度、250 日约一年。这套组合一年可能只有一两次信号，"
     "是典型的「大周期择时」。好处是不会被短期波动洗出去，"
     "坏处是拐点确认得极晚，一段行情能错过一大半。",
     {**MA_SCHEMA,
      "fast": {"type": "int", "min": 20, "max": 120, "default": 60},
      "slow": {"type": "int", "min": 120, "max": 250, "default": 250}},
     {"fast": 60, "slow": 250, "hysteresis": 0}, 2, "长线,牛熊分界"),

    ("boll_revert_20_25", "布林带 20/2.5 下轨买入", "均值回归", "boll",
     "把带宽放大到 2.5 倍标准差，只在更极端的偏离时才买",
     "标准布林带用 2 倍标准差，2.5 倍意味着要跌得更狠才触发。"
     "信号更少但更极端，适合波动大的标的；"
     "代价是有些年份可能一次都不触发。",
     {"n": {"type": "int", "min": 10, "max": 60, "default": 20},
      "k": {"type": "float", "min": 1.0, "max": 3.5, "default": 2.5}},
     {"n": 20, "k": 2.5}, 2, "均值回归,布林带"),

    ("boll_revert_26", "布林带 26 日下轨买入", "均值回归", "boll",
     "26 日周期的布林带，比 20 日更平滑",
     "26 是 MACD 的标准周期，用在布林带上会让上下轨更稳、更少被单日暴涨暴跌带偏。"
     "适合波动不规则、但整体有均值回归倾向的标的。",
     {"n": {"type": "int", "min": 10, "max": 60, "default": 26},
      "k": {"type": "float", "min": 1.0, "max": 3.5, "default": 2.0}},
     {"n": 26, "k": 2.0}, 2, "均值回归,布林带"),

    ("mom_120", "120 日动量 10%", "动量", "momentum",
     "半年涨超 10% 才买入，是偏长线的动量策略",
     "回看 120 个交易日（约半年），涨幅超过 10% 才认为趋势成立。"
     "长周期动量的好处是不容易被短期回调甩下车，"
     "坏处是在急涨急跌的行情里反应很慢。",
     {"lookback": {"type": "int", "min": 20, "max": 250, "default": 120},
      "threshold": {"type": "float", "min": 0.01, "max": 0.5, "default": 0.10}},
     {"lookback": 120, "threshold": 0.10}, 2, "动量,长线"),

    ("rsi_21", "RSI 21 日慢线", "反转", "rsi",
     "把 RSI 周期拉长到 21 天，信号更少也更稳",
     "短周期 RSI 噪声大、假信号多；21 日 RSI 把窗口拉长后，"
     "进入超卖区往往对应真正的深跌，超买区也更有意义。"
     "代价是可能整年都不触发几次。",
     {"n": {"type": "int", "min": 5, "max": 30, "default": 21},
      "low": {"type": "float", "min": 10, "max": 45, "default": 30},
      "high": {"type": "float", "min": 55, "max": 90, "default": 70}},
     {"n": 21, "low": 30, "high": 70}, 2, "反转,慢线"),
]


def main() -> int:
    dry = "--dry" in sys.argv

    conn = pymysql.connect(host="127.0.0.1", user="root", password="root",
                           database="quant_shop", charset="utf8mb4",
                           cursorclass=pymysql.cursors.DictCursor)
    cur = conn.cursor()

    cur.execute("SELECT code FROM factors")
    have_f = {r["code"] for r in cur.fetchall()}
    cur.execute("SELECT code FROM strategies")
    have_s = {r["code"] for r in cur.fetchall()}

    new_f = [f for f in FACTORS if f[0] not in have_f]
    new_s = [s for s in STRATEGIES if s[0] not in have_s]

    print(f"因子：已有 {len(have_f)}，将新增 {len(new_f)} 个 → 合计 {len(have_f) + len(new_f)}")
    for f in new_f:
        print(f"   + {f[0]:20s} {f[1]}")
    print()
    print(f"策略：已有 {len(have_s)}，将新增 {len(new_s)} 个 → 合计 {len(have_s) + len(new_s)}")
    for s in new_s:
        print(f"   + {s[0]:20s} {s[1]}")

    if dry:
        print("\n--dry 模式，没有写库")
        conn.close()
        return 0

    for code, name, cat, summary, formula, usage, hint, sort in new_f:
        cur.execute(
            "INSERT INTO factors (code, name, category, summary, formula, usage_note, "
            "params_hint, sort_no, active) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,1) "
            "ON DUPLICATE KEY UPDATE name=VALUES(name), category=VALUES(category), "
            "summary=VALUES(summary), formula=VALUES(formula), "
            "usage_note=VALUES(usage_note), params_hint=VALUES(params_hint)",
            (code, name, cat, summary, formula, usage, hint, sort))

    for (code, name, cat, stype, summary, detail, schema, defaults, diff, tags) in new_s:
        cur.execute(
            "INSERT INTO strategies (code, name, category, strategy_type, summary, detail, "
            "param_schema, default_params, difficulty, tags, is_public) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,1) "
            "ON DUPLICATE KEY UPDATE name=VALUES(name), summary=VALUES(summary), "
            "detail=VALUES(detail), param_schema=VALUES(param_schema), "
            "default_params=VALUES(default_params)",
            (code, name, cat, stype, summary, detail,
             json.dumps(schema, ensure_ascii=False),
             json.dumps(defaults, ensure_ascii=False), diff, tags))

    conn.commit()

    cur.execute("SELECT COUNT(*) c FROM factors WHERE active=1")
    f_total = cur.fetchone()["c"]
    cur.execute("SELECT COUNT(*) c FROM strategies WHERE is_public=1")
    s_total = cur.fetchone()["c"]
    conn.close()

    print()
    print(f"完成：因子库 {f_total} 个，策略库 {s_total} 个")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# -*- coding: utf-8 -*-
"""
第二批公开库补充：再补 27 个指标 + 10 个策略。

和 `seed_library.py`（第一批）的分工：
  第一批：ROC / MFI / PSY / Aroon / Z 分数 / 量比 …（22→37 个因子，9→17 个策略）
  第二批（本脚本）：TA-Lib 里更硬核的那批 + 4 种经典策略类型

本脚本补的因子分两类：
  A. 数值 21 个：+DI / -DI / ADXR / TRIX / 线性回归斜率 / Aroon 下线与振荡 /
     DPO / CMO / 随机 RSI / 终极震荡 / 力量均衡 / 相对活力 / 累积分配线 /
     佳庆振荡 / 简易波动 / 力量指标 / 量能热度 / 归一化 ATR / 当日真实振幅 / 梅斯线
  B. 布尔 6 个：锤子线 / 看涨吞没 / 十字星 / 三连阳 / 当日死叉 / 站上布林上轨

策略补 10 个，覆盖 4 种新类型（MACD 金叉 / KDJ 金叉 / 唐奇安突破 / TRIX 金叉），
外加一条**带过滤条件的策略**（RSI + ADX>20.5）—— 公共库原先存不下过滤条件，
上一轮给 strategies 表加了 filters 列，这里第一个用它。

原则和第一批一致：只写教科书上真有的东西，每个因子都必须能算出数（本脚本会自检）。

用法：
    python scripts/seed_library_v2.py           # 写库并自检
    python scripts/seed_library_v2.py --dry     # 只看会写什么
"""
from __future__ import annotations

import json
import os
import sys

import pymysql

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _schema(*items) -> dict:
    """按 (key, label, type, min, max, default) 生成 param_schema"""
    out = {}
    for it in items:
        key, label, typ, lo, hi, default = it
        d = {"type": typ, "label": label, "default": default}
        if lo is not None:
            d["min"] = lo
        if hi is not None:
            d["max"] = hi
        out[key] = d
    return out


# ============================================================
# 因子：code, 名称, 分类, 一句话, 怎么算, 怎么用, 常用参数, 排序
# ============================================================
FACTORS = [
    # ---- DMI 家族 ----
    ("pdi", "+DI 正向动向", "趋势",
     "上涨方向的力量占多大比重",
     "先算 +DM（今天最高 − 昨天最高，且要大于下跌幅度），Wilder 平滑后 ÷ 平滑后的真实波幅 × 100",
     "和 -DI 一起看：+DI 上穿 -DI 视为转多。单独看容易在震荡市里反复穿越，"
     "所以主流用法都要配一个 ADX 门槛（ADX>20 才认）",
     "常用 14", 40),

    ("mdi", "-DI 负向动向", "趋势",
     "下跌方向的力量占多大比重",
     "先算 -DM（昨天最低 − 今天最低，且要大于上涨幅度），Wilder 平滑后 ÷ 平滑后的真实波幅 × 100",
     "和 +DI 互为镜像。-DI 从下方穿过 +DI 是转空信号。"
     "注意：-DI 高不等于会跌，只说明跌得有力，横盘时两个都低",
     "常用 14", 41),

    ("adxr", "ADXR 趋势评级", "趋势",
     "ADX 的再平滑版，比 ADX 更钝、假信号更少",
     "(今天的 ADX + n 天前的 ADX) ÷ 2",
     "好处是趋势刚起步时 ADXR 不会立刻冲高，能区分「新趋势」和「走了很久的趋势」；"
     "代价是更滞后，转折时离场更晚",
     "常用 14", 42),

    # ---- 趋势类 ----
    ("trix", "TRIX 三重指数", "趋势",
     "收盘价连做三次平滑后的日变化率",
     "EMA(收盘,n) → 再 EMA → 再 EMA，取这个序列的日变化百分比",
     "只看正负号和穿越零轴，不管绝对值。信号极慢但很干净，"
     "适合抓大波段，一年可能只有两三次交易机会",
     "常用 12 / 18", 43),

    ("linreg_slope", "线性回归斜率", "趋势",
     "最近 n 天的价格趋势是向上还是向下，陡不陡",
     "对最近 n 个收盘价做最小二乘拟合，斜率 ÷ 均价 × 100（换算成每根 K 线涨跌几%）",
     "比均线更早反映转向，因为它用整段数据拟合而不是两个点相减。"
     "缺点是任何一段都能拟合出一条线，横盘时斜率在零附近来回抖",
     "常用 14 / 20", 44),

    ("aroon_down", "阿隆下线", "趋势",
     "n 天内最低点离今天有几天远",
     "(n − 距今最低点天数) ÷ n × 100，越接近 100 说明刚创新低",
     "阿隆上线接近 100 = 刚创新高，下线接近 100 = 刚创新低。"
     "两个同时高说明波动放大而不是趋势明确",
     "常用 25", 45),

    ("aroon_osc", "阿隆振荡", "趋势",
     "上线减下线，正值偏多、负值偏空",
     "Aroon Up − Aroon Down，范围 -100 ~ +100",
     "穿越零轴是趋势转换的信号，比两条线分别看更直观。"
     "在窄幅震荡里会频繁穿越，别单用",
     "常用 25", 46),

    ("dpo", "区间震荡 DPO", "趋势",
     "当前价偏离 n/2 天前那段趋势中枢多少",
     "收盘价 − 位移 n/2+1 天后的 n 日均线，再除以均线换成百分比",
     "专门用来「去掉趋势看波动」。正值大 = 短期涨过头，负值大 = 跌过头。"
     "别用它判断趋势方向，它本来就是把趋势减掉之后的残差",
     "常用 20", 47),

    # ---- 震荡 / 动量 ----
    ("cmo", "钱德动量 CMO", "震荡",
     "只统计涨跌幅度、不看绝对价格强弱的动量指标",
     "(n 日涨幅之和 − n 日跌幅之和) ÷ (涨幅之和 + 跌幅之和) × 100，范围 -100~+100",
     "比 RSI 少一层平滑，反应更快。±50 以上算极端。"
     "和 RSI 一样会在单边行情里长期钝化",
     "常用 14", 48),

    ("stochrsi", "随机 RSI", "震荡",
     "把 RSI 再归一化到「n 日内的相对位置」",
     "(RSI − n 日 RSI 最低) ÷ (n 日 RSI 最高 − 最低) × 100",
     "比 RSI 灵敏得多，几乎天天在 0 和 100 之间跑。"
     "适合抓极值拐点，不适合判断趋势；用它做过滤会把大部分日子都挡掉",
     "常用 14", 49),

    ("ultosc", "终极震荡 ULTOSC", "震荡",
     "短中长三个周期的买入压力加权，跨周期确认",
     "分别算 7/14/28 日的「买入压力 ÷ 真实波幅」，按 4:2:1 加权 × 100",
     "设计初衷是避免单一周期被操纵。70 以上超买、30 以下超卖。"
     "三个周期一起钝化时，指标会长期贴顶或贴底",
     "常用 7/14/28", 50),

    ("bop", "力量均衡 BOP", "震荡",
     "当天多空谁占上风，纯粹看收盘相对开盘的位置",
     "(收盘 − 开盘) ÷ (最高 − 最低)，再做 n 日加权平均",
     "范围 -1 ~ +1。单日值噪音极大，必须平滑后看。"
     "连续多日为正说明买盘持续占优。跳空日会失真",
     "常用 5", 51),

    ("rvi", "相对活力 RVI", "震荡",
     "收盘价在当天区间里的位置，衡量「收得有没有力」",
     "EMA(收盘−开盘, n) ÷ EMA(最高−最低, n)",
     "和 BOP 思路类似但用了 EMA 平滑。正值表示收在区间上半部。"
     "缩量窄幅波动时分子分母都很小，容易乱跳",
     "常用 10", 52),

    # ---- 量能 ----
    ("ad_line", "累积/分配线 AD", "量能",
     "收在高位算吸筹、收在低位算派发，再乘成交量累加",
     "MFM = ((收−低) − (高−收)) ÷ (高−低)，AD = Σ MFM × 成交量",
     "**看斜率不要看绝对值**（起点不同数值完全不同）。"
     "价格新高而 AD 没跟上，是典型的量价背离。注意这个值会随回看区间变化",
     "无参数", 53),

    ("adosc", "佳庆振荡 ADOSC", "量能",
     "AD 线的短期加速度，比 AD 更早给出信号",
     "EMA(AD, 3) − EMA(AD, 10)",
     "穿越零轴表示资金流向转换。因为基数大，数值本身没有可比性，"
     "只适合在同一只票上纵向比较",
     "常用 3/10", 54),

    ("emv", "简易波动 EMV", "量能",
     "价格走得轻不轻：放量却涨不动，说明有抛压",
     "中点价移动 ÷（成交量÷缩放系数÷振幅），再做 n 日加权平均",
     "接近零轴说明上下都很费劲。正值 = 上涨轻松。"
     "极度缩量的日子会把值放大得很夸张，看趋势别看单点",
     "常用 14", 55),

    ("efi", "力量指标 EFI", "量能",
     "成交量 × 价格变化，衡量涨跌背后的量能",
     "Σ(收盘变化 × 成交量) ÷ Σ(收盘 × 成交量)，n 日窗口",
     "正值且放大 = 上涨有量支撑。它是无量纲的，不同股票之间可以直接比。"
     "换手率极低的票会失真",
     "常用 13", 56),

    ("volume_wma", "量能热度", "量能",
     "今天的成交量是近期加权平均量的几倍",
     "今日成交量 ÷ n 日加权移动平均成交量",
     "1.0 = 与近期持平，2.0 = 明显放量。"
     "除权、解禁、指数调整当天会出现异常值，别当成信号",
     "常用 10", 57),

    # ---- 波动 ----
    ("natr", "归一化 ATR", "波动",
     "ATR 占价格的百分比，不同价位的股票可以直接比",
     "ATR(n) ÷ 收盘价 × 100",
     "茅台 2% 和小票 5% 是不同量级的波动。做仓位管理时用它比用 ATR 绝对值靠谱："
     "波动大的票就少买点。低波动不代表安全，可能是暴风雨前的盘整",
     "常用 14", 58),

    ("trange_pct", "当日真实振幅", "波动",
     "这一根 K 线（含跳空）实际波动了百分之几",
     "max(高−低, |高−前收|, |低−前收|) ÷ 收盘价 × 100",
     "最直白的「今天有多刺激」。连续几天异常放大通常意味着有事发生。"
     "单日值波动很大，要配合均线看",
     "无参数", 59),

    ("mass_index", "梅斯线 Mass Index", "波动",
     "高低价差的两层平滑之比累加，专门预警反转",
     "EMA(高−低, 9) ÷ EMA(EMA(高−低,9), 9)，再把结果 25 日累加",
     "经典用法是「凸起」：涨过 27 之后跌回 26.5 以下，历史上常伴随反转。"
     "它只预警不指方向，而且假信号不少，必须配合价格形态确认",
     "常用 9 / 25", 60),

    # ---- K 线形态（布尔） ----
    ("cdl_hammer", "锤子线", "形态",
     "下影线很长、实体很小、上影线很短的 K 线",
     "下影线 ≥ 实体 2 倍，且上影线 ≤ 实体 0.6 倍",
     "出现在下跌末端才是「锤子」，出现在上涨末端叫「上吊线」，含义相反。"
     "本因子不判断位置，只看形状，必须自己结合趋势看",
     "倍数 2.0", 61),

    ("cdl_engulf_bull", "看涨吞没", "形态",
     "昨天阴线，今天阳线且实体完全包住昨天",
     "昨日收<开 且 今日收>开 且 今开≤昨收 且 今收≥昨开",
     "吞没的实体越大越可信，最好配合放量。"
     "在强下跌趋势里经常出现假吞没，别单凭这一根就进",
     "无参数", 62),

    ("cdl_doji", "十字星", "形态",
     "开盘收盘几乎相同，多空僵持",
     "|收−开| ≤ (高−低) × 10%",
     "十字星本身不含方向，含义取决于它出现在什么位置："
     "涨了一段之后出现要警惕，跌了一段之后出现可能是转折。低位缩量十字星意义不大",
     "实体占比 10%", 63),

    ("cdl_three_white", "三连阳（红三兵）", "形态",
     "连续三根阳线，收盘逐日抬高",
     "连续 3 根 收>开，且 收1<收2<收3",
     "标准的红三兵要求每根都在前一根实体内开盘、收盘创新高。"
     "本因子条件放宽了一点。追第三根的风险最大，最好等回踩",
     "无参数", 64),

    ("ma_death_cross", "当日死叉", "趋势",
     "快线从上方跌破慢线的当天",
     "前一日 fast≥slow，当日 fast<slow",
     "和「当日金叉」互为镜像。死叉当天卖出往往已经晚了一步，"
     "它的价值更多在于确认趋势转弱而不是择时卖点",
     "常用 5/20", 65),

    ("boll_upper_break", "站上布林上轨", "形态",
     "收盘价站到布林上轨之上",
     "收盘 > 中轨 + k × 标准差",
     "强势股会沿着上轨走很久，「站上上轨」既可能是突破也可能是见顶。"
     "要看带宽：带宽收窄后的突破有效性高得多",
     "常用 20 / 2", 66),
]


# ============================================================
# 策略：code, 名称, 分类, strategy_type, 一句话, 详细, schema, 默认参数,
#       difficulty, tags, filters
# ============================================================
MACD_SCHEMA = _schema(
    ("fast", "快线周期", "int", 2, 60, 12),
    ("slow", "慢线周期", "int", 5, 120, 26),
    ("signal", "信号线周期", "int", 2, 30, 9),
    ("zero_filter", "只在零轴上方买", "bool", None, None, False),
)
KDJ_SCHEMA = _schema(
    ("n", "RSV 周期", "int", 3, 60, 9),
    ("buy_below", "只在 K 低于此值买入", "int", 10, 90, 50),
    ("sell_above", "K 高于此值离场", "int", 50, 99, 85),
)
BREAK_SCHEMA = _schema(
    ("entry", "突破几日新高买入", "int", 5, 120, 20),
    ("exit", "跌破几日新低卖出", "int", 3, 120, 10),
)
TRIX_SCHEMA = _schema(
    ("n", "平滑周期", "int", 3, 40, 12),
    ("signal", "信号线周期", "int", 2, 30, 9),
)

STRATEGIES = [
    ("macd_cross_12_26", "MACD 金叉 12/26", "趋势", "macd_cross",
     "最经典的 MACD 参数组合，DIF 上穿 DEA 买入、下穿卖出",
     "12/26/9 是 Gerald Appel 在 1970 年代给出的原始参数，至今仍是默认值。"
     "DIF 是快慢两条 EMA 的差，DEA 是 DIF 自己的 EMA，两者交叉代表短期动能反转。"
     "缺点很明确：震荡市里会连续假信号，一年可能交易十几次，手续费吃掉大半收益。"
     "所以真实用法多半要加一层过滤（见下一条「零轴版」）。",
     MACD_SCHEMA, {"fast": 12, "slow": 26, "signal": 9, "zero_filter": False},
     2, "MACD,金叉,入门", None),

    ("macd_zero_12_26", "MACD 零轴金叉 12/26", "趋势", "macd_cross",
     "只认 DIF 在零轴上方的金叉，滤掉下跌趋势里的反弹",
     "和上一条唯一的区别是多一个条件：买入时 DIF 必须大于 0。"
     "零轴上方意味着中期均线本身在向上，这时候的金叉是「趋势中的回调结束」；"
     "零轴下方的金叉大多是下跌中继，反弹两天就继续跌。"
     "代价是永远吃不到底部第一波，但能少挨很多刀。"
     "代价的另一面：交易次数会大幅减少，样本变少，统计意义也跟着变弱。",
     MACD_SCHEMA, {"fast": 12, "slow": 26, "signal": 9, "zero_filter": True},
     2, "MACD,零轴,过滤", None),

    ("macd_fast_5_35", "MACD 快线短版 5/35", "趋势", "macd_cross",
     "快线压到 5、慢线拉到 35，信号更早也更毛躁",
     "把快线缩短能让交叉提前发生，把慢线拉长能减少它在震荡区的抖动。"
     "这是一组实务里常见的「土参数」，不是教科书标准值 —— "
     "所以它在你自己的数据上表现好，不代表在别人的数据上也成立，这类参数最容易过拟合。",
     MACD_SCHEMA, {"fast": 5, "slow": 35, "signal": 5, "zero_filter": False},
     3, "MACD,短线,需谨慎", None),

    ("kdj_cross_9", "KDJ 金叉 9", "反转", "kdj_cross",
     "K 线在低位上穿 D 线买入，超买或死叉卖出",
     "KDJ 是国内散户最熟的指标，本质是把收盘价在最近 9 天的高低区间里的位置（RSV）"
     "做两次平滑。默认只在 K<50 时认金叉，避免在 80 以上追高。"
     "它的毛病和 RSI 一样：单边行情里会长期钝化，价格一直涨，K 一直在 80 上方。",
     KDJ_SCHEMA, {"n": 9, "buy_below": 50, "sell_above": 85},
     1, "KDJ,金叉,入门", None),

    ("kdj_cross_9_strict", "KDJ 深度超卖金叉 9", "反转", "kdj_cross",
     "只在 K 低于 30 时才认金叉，出手极少但位置更低",
     "把买入门槛从 50 压到 30，只有在明显超卖区才进场。"
     "好处是买得便宜、假信号少；坏处是强趋势里可能整年都不触发，"
     "而且在真正的下跌趋势里，第一次跌破 30 往往不是底。",
     KDJ_SCHEMA, {"n": 9, "buy_below": 30, "sell_above": 80},
     2, "KDJ,超卖,低频", None),

    ("breakout_20_10", "唐奇安通道突破 20/10", "突破", "breakout",
     "突破 20 日新高买入、跌破 10 日新低卖出，海龟交易法的简化版",
     "唐奇安通道就是「过去 n 天的最高价和最低价」。突破上轨说明创了新高，"
     "趋势可能开始；跌破较短周期的下轨就走。"
     "关键细节：上下轨只用到昨天为止的数据，用当天最高价算上轨的话，"
     "收盘永远不可能高于它 —— 这是这种策略最常被写错的地方。"
     "在震荡市里会被反复假突破打脸，这是它的固有代价。",
     BREAK_SCHEMA, {"entry": 20, "exit": 10},
     2, "海龟,突破,趋势", None),

    ("breakout_55_20", "唐奇安通道突破 55/20", "突破", "breakout",
     "海龟原版参数：55 日新高入场、20 日新低离场",
     "《海龟交易法则》里的系统二参数。周期长意味着交易极少（几年可能只有个位数），"
     "但每次都是大级别行情。问题也在这：样本太少，"
     "你很难判断它到底有效还是运气好，回测收益好也不代表未来能复现。",
     BREAK_SCHEMA, {"entry": 55, "exit": 20},
     3, "海龟,突破,低频", None),

    ("trix_12", "TRIX 金叉 12", "趋势", "trix_cross",
     "三重平滑后的变化率上穿信号线买入",
     "TRIX 把收盘价连做三次 EMA，短期噪音基本被磨平，剩下的只有大方向。"
     "信号非常少（一年两三次），但每次都在趋势中段，不吃头也不吃尾。"
     "适合完全不想盯盘的人，代价是回撤期间你也得一直拿着。",
     TRIX_SCHEMA, {"n": 12, "signal": 9},
     2, "TRIX,趋势,低频", None),

    ("trix_18", "TRIX 慢版 18", "趋势", "trix_cross",
     "平滑周期拉到 18，信号更少更慢",
     "参数越大越平滑，交易次数继续下降。适合做长周期的判断，"
     "但在 3 年这样相对短的样本里，可能只有一两次交易 —— "
     "**这个样本量下的胜率、夏普都没有统计意义**，只能当参考。",
     TRIX_SCHEMA, {"n": 18, "signal": 9},
     3, "TRIX,长线,样本少", None),

    # 带过滤条件的示例：公共库第一次存下完整配置（策略 + 条件）
    ("rsi_adx_14", "RSI 超卖 + ADX 趋势过滤", "反转", "rsi",
     "RSI 低于 30 买入，但只在 ADX>20.5 有明确趋势时才出手",
     "这是从「RSI 超卖买入」优化出来的版本：加一个 ADX 门槛，"
     "躲掉无方向震荡市里那些「便宜了还能更便宜」的信号。"
     "ADX 只衡量趋势强度、不分涨跌，所以它在趋势末端也会很高 —— "
     "这版的问题是会追在最后一波，实盘用建议把阈值取整（20 或 25）并重新验证，"
     "20.5 这种小数位本身就是拟合的痕迹。",
     _schema(
         ("n", "周期", "int", 5, 60, 14),
         ("low", "超卖线", "int", 10, 45, 30),
         ("high", "超买线", "int", 55, 90, 70),
     ),
     {"n": 14, "low": 30, "high": 70}, 3, "RSI,ADX,过滤,过拟合风险中",
     [{"factor": "adx", "op": "above", "value": 20.5}]),
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
        print(f"   + {f[0]:18s} {f[1]}")
    print()
    print(f"策略：已有 {len(have_s)}，将新增 {len(new_s)} 个 → 合计 {len(have_s) + len(new_s)}")
    for s in new_s:
        print(f"   + {s[0]:20s} {s[1]}" + ("  [带过滤条件]" if s[10] else ""))

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

    for (code, name, cat, stype, summary, detail, schema, defaults,
         diff, tags, filters) in new_s:
        cur.execute(
            "INSERT INTO strategies (code, name, category, strategy_type, summary, detail, "
            "param_schema, default_params, filters, difficulty, tags, is_public) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,1) "
            "ON DUPLICATE KEY UPDATE name=VALUES(name), summary=VALUES(summary), "
            "detail=VALUES(detail), param_schema=VALUES(param_schema), "
            "default_params=VALUES(default_params), filters=VALUES(filters)",
            (code, name, cat, stype, summary, detail,
             json.dumps(schema, ensure_ascii=False),
             json.dumps(defaults, ensure_ascii=False),
             json.dumps(filters, ensure_ascii=False) if filters else None,
             diff, tags))

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

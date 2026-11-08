-- ============================================================
-- 增量升级 v2：我的策略 + 因子库
-- 在已有库上执行：mysql -u root -p quant_shop < schema_v2.sql
-- 全部使用 IF NOT EXISTS，可重复执行
-- ============================================================

-- ---------- 我的策略：把跑过的结果存下来，以后一直能看 ----------
CREATE TABLE IF NOT EXISTS saved_strategies (
  id            BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  -- 注意：必须跟 users.id / strategies.id 一样是 BIGINT UNSIGNED，
  -- 否则 MySQL 会报 "Cannot add foreign key constraint"（1215）
  user_id       BIGINT UNSIGNED NOT NULL,
  name          VARCHAR(80)  NOT NULL COMMENT '用户自己起的名字',
  strategy_id   BIGINT UNSIGNED NOT NULL,
  symbol_code   VARCHAR(10)  NOT NULL,
  params        JSON         NOT NULL,
  job_no        VARCHAR(32)  NOT NULL DEFAULT '' COMMENT '来自哪次回测',
  snapshot      JSON         NULL COMMENT '保存当时的指标快照，避免原记录被删就看不了',
  note          VARCHAR(500) NOT NULL DEFAULT '' COMMENT '用户的备注',
  created_at    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  -- 同一次回测不重复收藏，重复点保存就是更新备注
  UNIQUE KEY uk_user_job (user_id, job_no),
  KEY idx_user_time (user_id, created_at),
  CONSTRAINT fk_saved_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
  CONSTRAINT fk_saved_strategy FOREIGN KEY (strategy_id) REFERENCES strategies(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='用户收藏的策略配置';

-- ---------- 因子库：给 AI 和用户共用的「零件箱」 ----------
CREATE TABLE IF NOT EXISTS factors (
  id          BIGINT AUTO_INCREMENT PRIMARY KEY,
  code        VARCHAR(40)  NOT NULL UNIQUE COMMENT '英文标识，如 rsi',
  name        VARCHAR(40)  NOT NULL COMMENT '中文名，如 RSI 相对强弱',
  category    VARCHAR(20)  NOT NULL COMMENT '趋势/震荡/波动/量能/形态',
  summary     VARCHAR(200) NOT NULL COMMENT '一句话说明它衡量什么',
  formula     VARCHAR(300) NOT NULL DEFAULT '' COMMENT '怎么算出来的（人话版）',
  usage_note  VARCHAR(300) NOT NULL DEFAULT '' COMMENT '怎么用、什么时候好用',
  params_hint VARCHAR(120) NOT NULL DEFAULT '' COMMENT '常见参数',
  sort_no     INT          NOT NULL DEFAULT 100,
  active      TINYINT      NOT NULL DEFAULT 1,
  KEY idx_cat (category, sort_no)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='技术因子库';

-- ---------- 因子内容：常见技术指标，人话解释 ----------
INSERT IGNORE INTO factors (code, name, category, summary, formula, usage_note, params_hint, sort_no) VALUES
('ma', '移动平均线 MA', '趋势', '最近 N 天收盘价的平均值，用来抹平短期噪音看清方向',
 '把最近 N 天的收盘价加起来除以 N。N=7 就是最近一周的平均价',
 '最基础的指标。价格在均线上方通常认为偏强，下方偏弱。缺点是滞后，拐头总是慢半拍',
 '常用 5 / 10 / 20 / 60', 10),

('ema', '指数移动平均 EMA', '趋势', '和 MA 类似，但越近的价格权重越大，反应更快',
 '给每天的价格按时间加权，越近的权重越高，再平均',
 '比 MA 灵敏，但假信号也更多。短线常用 12 和 26',
 '常用 12 / 26', 11),

('ma_cross', '均线交叉', '趋势', '短期均线上穿长期均线叫金叉，下穿叫死叉',
 '比较两条不同周期的均线，谁在上方',
 '最经典的趋势跟踪。震荡市里会反复被打脸，这是它最大的问题',
 '快线 5~10，慢线 20~60', 12),

('macd', 'MACD 指数平滑异同', '趋势', '两条 EMA 的差值，反映趋势的加速还是减速',
 '快线 EMA12 减慢线 EMA26，再对这个差值做一次平滑得到信号线',
 '金叉买入、死叉卖出。比单纯均线交叉少一些假信号，但同样滞后',
 '12 / 26 / 9', 13),

('adx', 'ADX 趋势强度', '趋势', '不管涨还是跌，只告诉你「现在有没有趋势」',
 '比较相邻两天的最高最低价变化，算出方向运动的强度',
 'ADX 高于 25 说明趋势强，适合用趋势策略；低于 20 说明在震荡，趋势策略容易亏',
 '常用 14，阈值 20~25', 14),

('boll', '布林带 BOLL', '波动', '用均线上下各画一条线，把价格框在中间，看它偏离多少',
 '中轨是 20 日均线，上下轨是中轨加减 2 倍标准差',
 '价格触下轨可能超卖，触上轨可能超买。震荡市好用，单边市会被反复突破',
 '20 日，2 倍', 20),

('boll_width', '布林带宽度', '波动', '上下轨之间的距离，用来判断波动是在放大还是收敛',
 '上轨减下轨，再除以中轨',
 '宽度极窄往往意味着要变盘，方向不确定但波动会放大',
 '20 日', 21),

('atr', 'ATR 真实波幅', '波动', '最近 N 天价格平均波动多少，用来量风险和设止损',
 '取每天的真实波幅（考虑跳空），再做平均',
 '常用来定止损距离，比如跌破 2 倍 ATR 就止损。也能用来比较不同股票的波动大小',
 '常用 14', 22),

('std', '标准差', '波动', '价格偏离平均值的程度，越大说明越不稳定',
 '每个价格和均值的差，平方后平均，再开根号',
 '波动率的原始度量。布林带就是它的应用',
 '20 日', 23),

('rsi', 'RSI 相对强弱', '震荡', '衡量最近涨的力量和跌的力量谁更强，0~100',
 '统计一段时间内上涨日的涨幅之和与下跌日的跌幅之和，算成比例',
 '高于 70 认为超买、低于 30 认为超卖。强趋势里会长期钝化，不能单用',
 '常用 6 / 14 / 24', 30),

('kdj', 'KDJ 随机指标', '震荡', '看收盘价在最近一段时间的高低区间里处于什么位置',
 '先算最近 N 天的最高最低价区间，看今天收盘在其中百分位，再做平滑',
 'K 上穿 D 买入、下穿卖出。比 RSI 灵敏，噪音也更多',
 '9 / 3 / 3', 31),

('cci', 'CCI 顺势指标', '震荡', '价格偏离统计平均的异常程度，能识别极端行情',
 '用典型价减去均线，再除以平均绝对偏差',
 '超过 +100 强势、低于 -100 弱势。适合抓突破和极端反转',
 '常用 14 或 20', 32),

('bias', 'BIAS 乖离率', '震荡', '价格离均线有多远，用百分比表示',
 '收盘价减均线，再除以均线',
 '正乖离过大可能回落，负乖离过大可能反弹。本质是均值回归思路',
 '常用 6 / 12 / 24', 33),

('willr', '威廉指标 W%R', '震荡', '和 KDJ 类似，衡量收盘价在高低区间的位置，方向相反',
 '最近 N 天最高价减收盘价，除以最高减最低',
 '低于 -80 超卖、高于 -20 超买。是 RSI 的镜像版本',
 '常用 14', 34),

('volume_ratio', '量比', '量能', '今天的成交量和最近几天平均比，看有没有放量',
 '今天成交量除以过去 5 日平均成交量',
 '放量上涨可信度高，缩量上涨要多留个心眼。突破时尤其要看量',
 '5 日均量', 40),

('obv', 'OBV 能量潮', '量能', '把成交量按涨跌方向累加，看资金是流入还是流出',
 '涨的日子加成交量，跌的日子减成交量，累加起来',
 'OBV 和价格背离时往往预示转折，比如价格新高但 OBV 没跟上',
 '无参数', 41),

('turnover', '换手率', '量能', '一段时间内成交的股数占总股本的比例，看筹码活跃度',
 '成交量除以流通股本',
 '换手率突然放大说明有资金关注。长期低换手的票流动性差，回测收益也难兑现',
 '无参数', 42),

('amt', '成交额', '量能', '用钱衡量的交易活跃度，比成交量更可比',
 '每笔成交价乘以数量，累加',
 '跨股票比较流动性时比成交量准确',
 '无参数', 43),

('cross_up', '上穿形态', '形态', '某个值从下方穿到另一个值的上方',
 '比较相邻两天的两个序列，昨天在前方下方、今天在上方',
 '金叉就是一种上穿。可以套用到价格穿均线、指标穿阈值等任何场景',
 '—', 50),

('cross_down', '下穿形态', '形态', '某个值从上方跌到另一个值的下方',
 '比较相邻两天的两个序列，昨天在前方上方、今天在下方',
 '死叉就是一种下穿。和上穿配合使用',
 '—', 51),

('breakout', '突破新高', '形态', '价格超过最近 N 天的最高点',
 '今天的收盘价或最高价，大于过去 N 天的最高价',
 '突破策略的核心。要配合成交量确认，否则容易假突破',
 '常用 20 / 55 / 120', 52),

('drawdown', '回撤幅度', '形态', '从最近的高点跌了多少，反映持有体验',
 '当前净值相对历史最高净值的跌幅',
 '回撤超过某个阈值就减仓，是最常见的风控手段',
 '常用 10% / 20%', 53);

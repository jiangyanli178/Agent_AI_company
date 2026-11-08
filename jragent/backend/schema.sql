-- ============================================================
-- 积分制 AI 量化策略工作坊 · MySQL 8.0+ / 9.x
-- 执行：mysql -u root -p < schema.sql
-- ============================================================

DROP DATABASE IF EXISTS quant_shop;
CREATE DATABASE quant_shop DEFAULT CHARSET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
USE quant_shop;

-- ------------------------------------------------------------
-- 1. 用户
-- ------------------------------------------------------------
CREATE TABLE users (
  id            BIGINT UNSIGNED PRIMARY KEY AUTO_INCREMENT,
  phone         VARCHAR(20)  NOT NULL COMMENT '登录手机号',
  password_hash VARCHAR(128) NOT NULL COMMENT 'bcrypt',
  nickname      VARCHAR(40)  NOT NULL DEFAULT '',
  status        TINYINT      NOT NULL DEFAULT 1 COMMENT '1正常 0封禁',
  is_paid       TINYINT      NOT NULL DEFAULT 0 COMMENT '是否产生过真实付费，用于看板',
  register_ip   VARCHAR(45)  NOT NULL DEFAULT '' COMMENT '注册 IP，防批量小号薅赠送积分',
  consent_at    DATETIME     NULL COMMENT '勾选《策略来源与责任声明》的时间',
  consent_version VARCHAR(16) NOT NULL DEFAULT '' COMMENT '声明版本号，改版需重新勾选',
  created_at    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  last_login_at DATETIME     NULL,
  UNIQUE KEY uk_phone (phone),
  KEY idx_register_ip (register_ip, created_at)
) COMMENT='用户，保留 phone 作为唯一凭证，不做实名';

-- ------------------------------------------------------------
-- 2. 积分账户（一人一行，余额只在这里）
-- ------------------------------------------------------------
CREATE TABLE credit_accounts (
  user_id        BIGINT UNSIGNED PRIMARY KEY,
  balance        INT NOT NULL DEFAULT 0 COMMENT '当前余额',
  frozen         INT NOT NULL DEFAULT 0 COMMENT '预扣冻结（任务开始扣，失败退回）',
  total_recharge INT NOT NULL DEFAULT 0 COMMENT '累计充值积分',
  total_gift     INT NOT NULL DEFAULT 0 COMMENT '累计赠送积分',
  total_consumed INT NOT NULL DEFAULT 0 COMMENT '累计消耗积分',
  updated_at     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  CONSTRAINT fk_acct_user FOREIGN KEY (user_id) REFERENCES users(id),
  CONSTRAINT ck_balance CHECK (balance >= 0),
  CONSTRAINT ck_frozen  CHECK (frozen  >= 0)
) COMMENT='积分账户，余额变更必须走 credit_transactions';

-- ------------------------------------------------------------
-- 3. 积分流水（唯一可信账本，余额变动必留痕）
-- ------------------------------------------------------------
CREATE TABLE credit_transactions (
  id              BIGINT UNSIGNED PRIMARY KEY AUTO_INCREMENT,
  user_id         BIGINT UNSIGNED NOT NULL,
  change_type     ENUM('RECHARGE','GIFT','CONSUME','REFUND','ADJUST') NOT NULL,
  amount          INT NOT NULL COMMENT '正为入账，负为出账',
  balance_after   INT NOT NULL COMMENT '变更后余额，用于对账',
  biz_type        VARCHAR(32) NOT NULL COMMENT 'AI_CHAT/DIAGNOSE/OPTIMIZE/BACKTEST/DEPLOY/REGISTER',
  biz_id          VARCHAR(64) NOT NULL DEFAULT '' COMMENT '关联业务主键',
  idempotency_key VARCHAR(96) NOT NULL COMMENT '幂等键，重复请求只记一次',
  remark          VARCHAR(200) NOT NULL DEFAULT '',
  created_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uk_idem (idempotency_key),
  KEY idx_user_time (user_id, created_at),
  KEY idx_biz (biz_type, biz_id),
  CONSTRAINT fk_tx_user FOREIGN KEY (user_id) REFERENCES users(id)
) COMMENT='积分流水，append-only，永不 UPDATE';

-- ------------------------------------------------------------
-- 4. 充值套餐
-- ------------------------------------------------------------
CREATE TABLE credit_packs (
  id           INT UNSIGNED PRIMARY KEY AUTO_INCREMENT,
  code         VARCHAR(32) NOT NULL,
  name         VARCHAR(40) NOT NULL,
  price_cny    DECIMAL(10,2) NOT NULL,
  points       INT NOT NULL COMMENT '基础积分',
  bonus_points INT NOT NULL DEFAULT 0 COMMENT '赠送积分',
  tag          VARCHAR(20) NOT NULL DEFAULT '',
  sort_no      INT NOT NULL DEFAULT 0,
  active       TINYINT NOT NULL DEFAULT 1,
  UNIQUE KEY uk_code (code)
) COMMENT='充值档位，改价只改这张表';

INSERT INTO credit_packs (code,name,price_cny,points,bonus_points,tag,sort_no) VALUES
('trial',  '体验包',   9.90, 99,   1,    '先试一下', 1),
('std',    '标准包',  49.00, 490,  50,   '最常买',   2),
('pro',    '专业包', 199.00, 1990, 400,  '省 40 元', 3),
('team',   '团队包', 499.00, 4990, 1500, '省 240 元',4);

-- ------------------------------------------------------------
-- 5. 充值订单（假支付也走这套表，后面接真支付只换回调）
-- ------------------------------------------------------------
CREATE TABLE recharge_orders (
  id          BIGINT UNSIGNED PRIMARY KEY AUTO_INCREMENT,
  order_no    VARCHAR(40) NOT NULL COMMENT '业务单号，展示给用户',
  user_id     BIGINT UNSIGNED NOT NULL,
  pack_id     INT UNSIGNED NOT NULL,
  price_cny   DECIMAL(10,2) NOT NULL,
  points      INT NOT NULL COMMENT '订单应到账积分（含赠送）',
  status      ENUM('PENDING','PAID','CANCELLED','REFUNDED') NOT NULL DEFAULT 'PENDING',
  pay_channel VARCHAR(20) NOT NULL DEFAULT 'MOCK' COMMENT 'MOCK/ALIPAY/WECHAT',
  pay_trade_no VARCHAR(64) NOT NULL DEFAULT '' COMMENT '第三方交易号或付款后6位',
  verify_note  VARCHAR(200) NOT NULL DEFAULT '' COMMENT '人工核销备注，如"微信已核对 9.9 元"',
  paid_at     DATETIME NULL,
  created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uk_order_no (order_no),
  KEY idx_user (user_id, created_at),
  CONSTRAINT fk_order_user FOREIGN KEY (user_id) REFERENCES users(id),
  CONSTRAINT fk_order_pack FOREIGN KEY (pack_id) REFERENCES credit_packs(id)
);

-- ------------------------------------------------------------
-- 6. 标的
-- ------------------------------------------------------------
CREATE TABLE symbols (
  code     VARCHAR(12) PRIMARY KEY COMMENT '600519',
  name     VARCHAR(40) NOT NULL,
  market   VARCHAR(8)  NOT NULL DEFAULT 'SH' COMMENT 'SH/SZ',
  industry VARCHAR(40) NOT NULL DEFAULT '',
  active   TINYINT NOT NULL DEFAULT 1
);

INSERT INTO symbols (code,name,market,industry) VALUES
('600519','贵州茅台','SH','白酒'),('000858','五粮液','SZ','白酒'),
('600036','招商银行','SH','银行'),('601318','中国平安','SH','保险'),
('000001','平安银行','SZ','银行'),('600000','浦发银行','SH','银行'),
('601398','工商银行','SH','银行'),('600900','长江电力','SH','电力'),
('000333','美的集团','SZ','家电'),('000651','格力电器','SZ','家电'),
('002415','海康威视','SZ','电子'),('002594','比亚迪','SZ','汽车'),
('300750','宁德时代','SZ','电池'),('600276','恒瑞医药','SH','医药'),
('000568','泸州老窖','SZ','白酒'),('601888','中国中免','SH','零售'),
('600030','中信证券','SH','券商'),('000002','万科A','SZ','地产'),
('601012','隆基绿能','SH','光伏'),('002304','洋河股份','SZ','白酒'),
('600887','伊利股份','SH','食品'),('603288','海天味业','SH','食品'),
('600309','万华化学','SH','化工'),('601899','紫金矿业','SH','有色'),
('600028','中国石化','SH','石化'),('601857','中国石油','SH','石化'),
('000063','中兴通讯','SZ','通信'),('002475','立讯精密','SZ','电子'),
('300059','东方财富','SZ','券商'),('688981','中芯国际','SH','半导体');

-- ------------------------------------------------------------
-- 7. 日线（预拉本地化，现场断网也能跑）
-- ------------------------------------------------------------
CREATE TABLE daily_bars (
  symbol_code VARCHAR(12) NOT NULL,
  trade_date  DATE NOT NULL,
  open_price  DECIMAL(12,3) NOT NULL,
  high_price  DECIMAL(12,3) NOT NULL,
  low_price   DECIMAL(12,3) NOT NULL,
  close_price DECIMAL(12,3) NOT NULL,
  volume      BIGINT NOT NULL DEFAULT 0,
  amount      DECIMAL(18,2) NOT NULL DEFAULT 0,
  adj_factor  DECIMAL(12,6) NOT NULL DEFAULT 1 COMMENT '前复权因子',
  PRIMARY KEY (symbol_code, trade_date),
  KEY idx_date (trade_date)
) COMMENT='日线，主键天然聚簇，回测按 symbol+date 范围扫描';

-- ------------------------------------------------------------
-- 8. 策略（官方策略库 + 用户自建）
-- ------------------------------------------------------------
CREATE TABLE strategies (
  id            BIGINT UNSIGNED PRIMARY KEY AUTO_INCREMENT,
  code          VARCHAR(48) NOT NULL COMMENT 'ma_cross_5_20 之类',
  name          VARCHAR(60) NOT NULL,
  category      VARCHAR(24) NOT NULL COMMENT '趋势/均值回归/动量/反转',
  strategy_type VARCHAR(24) NOT NULL COMMENT 'ma_cross/boll/momentum/rsi，决定用哪个算法',
  summary       VARCHAR(200) NOT NULL DEFAULT '' COMMENT '一句话人话解释，卡片正面',
  detail        TEXT NULL COMMENT '展开说明，教学用',
  param_schema  JSON NOT NULL COMMENT '参数定义，前端据此渲染表单',
  default_params JSON NOT NULL,
  difficulty    TINYINT NOT NULL DEFAULT 1 COMMENT '1入门 2进阶 3高级',
  tags          VARCHAR(120) NOT NULL DEFAULT '',
  owner_user_id BIGINT UNSIGNED NULL COMMENT 'NULL=官方策略库',
  is_public     TINYINT NOT NULL DEFAULT 1,
  use_count     INT NOT NULL DEFAULT 0 COMMENT '被回测次数，卡片显示热度',
  created_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uk_code (code),
  KEY idx_cat (category, is_public),
  KEY idx_owner (owner_user_id)
) COMMENT='策略库，官方策略是获客素材，也是知识库的可视化';

INSERT INTO strategies
(code,name,category,strategy_type,summary,detail,param_schema,default_params,difficulty,tags) VALUES
('ma_cross_5_20','5/20 均线金叉','趋势','ma_cross',
 '短期均线上穿长期均线买入，下穿卖出。最经典的入门策略。',
 '快线代表近期成本，慢线代表中期成本。上穿说明近期买入者比中期持有者更积极，趋势可能转向。缺点是在震荡市里会反复被打脸。',
 '{"fast":{"type":"int","min":2,"max":60,"default":5},"slow":{"type":"int","min":5,"max":250,"default":20}}',
 '{"fast":5,"slow":20}',1,'入门,趋势,金叉'),
('ma_cross_10_60','10/60 均线金叉','趋势','ma_cross',
 '中期趋势版金叉，信号更少但更稳。',
 '参数放大到 10/60 后，交易次数明显下降，单笔盈亏放大。适合不想频繁操作的持有型选手。',
 '{"fast":{"type":"int","min":2,"max":60,"default":10},"slow":{"type":"int","min":5,"max":250,"default":60}}',
 '{"fast":10,"slow":60}',1,'入门,趋势'),
('ma_cross_20_120','20/120 半年线金叉','趋势','ma_cross',
 '大级别趋势跟踪，一年可能只交易一两次。',
 '120 日约等于半年线，穿越意味着中期资金态度转变。缺点是信号极稀疏，需要足够长的回测区间。',
 '{"fast":{"type":"int","min":2,"max":60,"default":20},"slow":{"type":"int","min":5,"max":250,"default":120}}',
 '{"fast":20,"slow":120}',2,'趋势,低频'),
('boll_revert_20','布林带下轨买入','均值回归','boll',
 '跌破下轨买入，回到中轨卖出。赌超跌反弹。',
 '布林带衡量价格偏离均值的程度。跌破下轨意味着短期跌过头，但请注意：强势下跌中会连续跌破，这是本策略最大的风险。',
 '{"n":{"type":"int","min":10,"max":120,"default":20},"k":{"type":"float","min":1.0,"max":3.0,"default":2.0}}',
 '{"n":20,"k":2.0}',2,'均值回归,超跌'),
('boll_break_20','布林带上轨突破','趋势','boll_break',
 '突破上轨买入，回到中轨卖出。赌强者更强。',
 '和上面正好相反。突破上轨说明多头极强，属于追涨逻辑，在单边行情里表现好，震荡市里会持续亏损。',
 '{"n":{"type":"int","min":10,"max":120,"default":20},"k":{"type":"float","min":1.0,"max":3.0,"default":2.0}}',
 '{"n":20,"k":2.0}',2,'趋势,突破'),
('mom_20','20 日动量','动量','momentum',
 '过去 20 个交易日涨幅超过阈值就持有。',
 '动量效应是最被学术研究支持的异象之一。参数越大越接近趋势跟踪，越小越敏感。',
 '{"lookback":{"type":"int","min":5,"max":120,"default":20},"threshold":{"type":"float","min":-0.1,"max":0.3,"default":0.03}}',
 '{"lookback":20,"threshold":0.03}',2,'动量,学术'),
('mom_60','60 日动量','动量','momentum',
 '季度级别动量，换手极低。',
 '用季度涨幅做判断，一年最多几次信号。适合作为组合里的长期底仓逻辑。',
 '{"lookback":{"type":"int","min":20,"max":250,"default":60},"threshold":{"type":"float","min":-0.1,"max":0.3,"default":0.05}}',
 '{"lookback":60,"threshold":0.05}',2,'动量,低频'),
('rsi_14','RSI 超卖买入','反转','rsi',
 'RSI 低于 30 买入，高于 70 卖出。',
 'RSI 衡量近期涨跌力度。低于 30 说明卖压释放过度。注意：单边下跌时 RSI 会长期趴在 30 以下，止损必须靠仓位而不是靠策略。',
 '{"n":{"type":"int","min":5,"max":60,"default":14},"low":{"type":"float","min":10,"max":45,"default":30},"high":{"type":"float","min":55,"max":90,"default":70}}',
 '{"n":14,"low":30,"high":70}',1,'反转,超卖'),
('rsi_6','RSI 6 日快线','反转','rsi',
 '参数缩短到 6 日，信号频繁，适合短线。',
 '周期越短越敏感，噪音也越大。回测里通常会看到交易次数暴增、胜率下降但单笔盈利可能更高。',
 '{"n":{"type":"int","min":3,"max":30,"default":6},"low":{"type":"float","min":10,"max":45,"default":25},"high":{"type":"float","min":55,"max":90,"default":75}}',
 '{"n":6,"low":25,"high":75}',2,'反转,短线');

-- ------------------------------------------------------------
-- 9. 回测任务
-- ------------------------------------------------------------
CREATE TABLE backtest_jobs (
  id            BIGINT UNSIGNED PRIMARY KEY AUTO_INCREMENT,
  job_no        VARCHAR(40) NOT NULL,
  user_id       BIGINT UNSIGNED NOT NULL,
  strategy_id   BIGINT UNSIGNED NOT NULL,
  symbol_code   VARCHAR(12) NOT NULL,
  start_date    DATE NOT NULL,
  end_date      DATE NOT NULL,
  params        JSON NOT NULL COMMENT '本次实际使用的参数，可被用户改',
  init_cash     DECIMAL(14,2) NOT NULL DEFAULT 100000,
  fee_rate      DECIMAL(6,4) NOT NULL DEFAULT 0.0010 COMMENT '单边费率',
  status        ENUM('QUEUED','RUNNING','DONE','FAILED') NOT NULL DEFAULT 'QUEUED',
  points_cost   INT NOT NULL DEFAULT 10,
  result_json   JSON NULL COMMENT '指标 + 净值曲线数组 + 交易明细',
  share_token   VARCHAR(32) NOT NULL DEFAULT '' COMMENT '公开分享只读链接，获客的关键出口',
  is_public     TINYINT NOT NULL DEFAULT 0 COMMENT '1=允许未登录访问 /s/{share_token}',
  error_msg     VARCHAR(300) NOT NULL DEFAULT '',
  created_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  finished_at   DATETIME NULL,
  UNIQUE KEY uk_job_no (job_no),
  KEY idx_share (share_token),
  KEY idx_user_time (user_id, created_at),
  CONSTRAINT fk_job_user FOREIGN KEY (user_id) REFERENCES users(id),
  CONSTRAINT fk_job_strat FOREIGN KEY (strategy_id) REFERENCES strategies(id),
  CONSTRAINT fk_job_sym FOREIGN KEY (symbol_code) REFERENCES symbols(code)
) COMMENT='回测结果整包存 result_json，少一张曲线表，读一次就能出图';

-- ------------------------------------------------------------
-- 10. 部署（模拟盘，按天扣积分）
-- ------------------------------------------------------------
CREATE TABLE deployments (
  id               BIGINT UNSIGNED PRIMARY KEY AUTO_INCREMENT,
  user_id          BIGINT UNSIGNED NOT NULL,
  strategy_id      BIGINT UNSIGNED NOT NULL,
  symbol_code      VARCHAR(12) NOT NULL,
  params           JSON NOT NULL,
  mode             ENUM('PAPER') NOT NULL DEFAULT 'PAPER' COMMENT '只做模拟盘',
  status           ENUM('RUNNING','PAUSED','STOPPED','EXPIRED') NOT NULL DEFAULT 'RUNNING',
  points_per_day   INT NOT NULL DEFAULT 5,
  balance_floor    INT NOT NULL DEFAULT 0 COMMENT '余额低于此值自动暂停',
  started_at       DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  last_charge_date DATE NULL COMMENT '最后一次扣费成功日，防重复扣',
  expire_at        DATETIME NULL,
  stopped_at       DATETIME NULL,
  KEY idx_user (user_id, status),
  KEY idx_charge (status, last_charge_date),
  CONSTRAINT fk_dep_user FOREIGN KEY (user_id) REFERENCES users(id),
  CONSTRAINT fk_dep_strat FOREIGN KEY (strategy_id) REFERENCES strategies(id),
  CONSTRAINT fk_dep_sym FOREIGN KEY (symbol_code) REFERENCES symbols(code)
) COMMENT='部署=每日跑策略出信号，按天扣分，余额不足自动暂停';

-- ------------------------------------------------------------
-- 11. 信号
-- ------------------------------------------------------------
CREATE TABLE signals (
  id            BIGINT UNSIGNED PRIMARY KEY AUTO_INCREMENT,
  deployment_id BIGINT UNSIGNED NOT NULL,
  trade_date    DATE NOT NULL,
  symbol_code   VARCHAR(12) NOT NULL,
  action        ENUM('BUY','SELL','HOLD') NOT NULL,
  ref_price     DECIMAL(12,3) NOT NULL DEFAULT 0,
  position      TINYINT NOT NULL DEFAULT 0 COMMENT '目标仓位 0/1',
  reason        VARCHAR(300) NOT NULL DEFAULT '' COMMENT '人话解释，前端直接展示',
  created_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uk_dep_date (deployment_id, trade_date),
  KEY idx_date (trade_date),
  CONSTRAINT fk_sig_dep FOREIGN KEY (deployment_id) REFERENCES deployments(id)
) COMMENT='唯一键防同一部署同一天重复出信号';

-- ------------------------------------------------------------
-- 12. AI 会话与消息
-- ------------------------------------------------------------
CREATE TABLE ai_conversations (
  id         BIGINT UNSIGNED PRIMARY KEY AUTO_INCREMENT,
  user_id    BIGINT UNSIGNED NOT NULL,
  title      VARCHAR(80) NOT NULL DEFAULT '新对话',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  KEY idx_user (user_id, updated_at),
  CONSTRAINT fk_conv_user FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE ai_messages (
  id              BIGINT UNSIGNED PRIMARY KEY AUTO_INCREMENT,
  conversation_id BIGINT UNSIGNED NOT NULL,
  role            ENUM('user','assistant','system') NOT NULL,
  content         MEDIUMTEXT NOT NULL,
  points_cost     INT NOT NULL DEFAULT 0,
  tokens_in       INT NOT NULL DEFAULT 0,
  tokens_out      INT NOT NULL DEFAULT 0,
  latency_ms      INT NOT NULL DEFAULT 0,
  created_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  KEY idx_conv (conversation_id, id),
  CONSTRAINT fk_msg_conv FOREIGN KEY (conversation_id) REFERENCES ai_conversations(id)
) COMMENT='tokens_* 用于算真实毛利，答辩时可以直接跑 SQL 佐证';

-- ------------------------------------------------------------
-- 13. 知识库（你的护城河，也是"不是套壳"的证据）
-- ------------------------------------------------------------
CREATE TABLE kb_documents (
  id         BIGINT UNSIGNED PRIMARY KEY AUTO_INCREMENT,
  category   VARCHAR(24) NOT NULL COMMENT '概念/策略/指标/风险/合规',
  title      VARCHAR(120) NOT NULL,
  content    MEDIUMTEXT NOT NULL,
  keywords   VARCHAR(200) NOT NULL DEFAULT '' COMMENT '逗号分隔，用于关键词召回',
  related_strategy_code VARCHAR(48) NOT NULL DEFAULT '',
  sort_no    INT NOT NULL DEFAULT 0,
  active     TINYINT NOT NULL DEFAULT 1,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  KEY idx_cat (category, sort_no),
  FULLTEXT KEY ft_content (title, content) WITH PARSER ngram
) COMMENT='MySQL 原生全文检索，不引入向量库，省 4 小时';

INSERT INTO kb_documents (category,title,content,keywords,related_strategy_code,sort_no) VALUES
('概念','什么是金叉和死叉',
 '金叉指短期均线上穿长期均线，通常被视为买入信号；死叉指短期均线下穿长期均线，通常被视为卖出信号。背后的含义是近期持仓成本开始高于中期成本，说明近期买盘更强。但要注意：在震荡行情中金叉死叉会频繁出现且大量失效，必须结合成交量与趋势环境判断。',
 '金叉,死叉,均线,交叉','ma_cross_5_20',1),
('概念','为什么金叉会失效',
 '金叉只描述了均线的相对位置，没有描述趋势的强度。当价格在窄区间横盘时，短均线会在长均线上下反复穿越，每次穿越都产生买卖信号，交易成本会吃掉全部利润。判断是否失效的两个观察点：一是均线的斜率，二是穿越时是否有成交量配合。',
 '金叉失效,震荡,假信号','ma_cross_5_20',2),
('指标','最大回撤怎么读',
 '最大回撤是从净值最高点回落的最深幅度，衡量"最难受的时候有多难受"。年化收益相同的两个策略，最大回撤 15% 和 40% 是完全不同的产品，后者大多数人拿不住。看回测报告时，先看最大回撤，再看收益。',
 '最大回撤,风险,净值','',3),
('指标','夏普比率是什么',
 '夏普比率等于超额收益除以波动率，衡量"每承担一单位波动能换多少收益"。一般大于 1 算不错，大于 2 要怀疑是否过拟合。单只股票、短区间回测出的高夏普基本都是噪音。',
 '夏普,波动率,风险调整','',4),
('风险','为什么回测好看实盘亏钱',
 '最常见的四个原因：一是未来函数，用了当天收盘价决定当天买点；二是过拟合，参数调到历史最优但换个区间就崩；三是忽略交易成本，频繁交易被手续费吃掉；四是幸存者偏差，只挑了活到今天的股票回测。我们的回测引擎统一采用"信号延后一天执行"来规避第一条。',
 '过拟合,未来函数,交易成本,幸存者偏差','',5),
('策略','动量策略为什么有效',
 '动量效应指过去涨得好的资产在未来一段时间继续跑赢，这是被多市场、多年代数据验证过的异象。它有效的解释包括信息扩散缓慢、投资者处置效应等。但动量策略在剧烈反转时会遭遇"动量崩溃"，所以通常需要配合风控。',
 '动量,异象,趋势','mom_20',6),
('合规','策略由谁负责',
 '平台只提供策略编写、回测验证与运行环境的技术工具。策略由用户自行提供并自行决策，平台不推荐具体买卖、不代客下单、不接触客户资金。所有部署均为模拟盘，不产生真实委托。',
 '合规,模拟盘,责任','',7);

-- ------------------------------------------------------------
-- 14. Agent 运行日志（作品材料的直接来源，别省这张表）
--     用途一：人事档案 —— 每个 agent 的绩效、失败率
--     用途二：事故报告 —— 每次异常的真实留档，评分点
-- ------------------------------------------------------------
CREATE TABLE agent_runs (
  id          BIGINT UNSIGNED PRIMARY KEY AUTO_INCREMENT,
  agent_code  VARCHAR(32) NOT NULL COMMENT 'research/product/content/support/risk/finance',
  task_type   VARCHAR(48) NOT NULL COMMENT 'generate_content/answer_ticket/generate_signal',
  biz_id      VARCHAR(64) NOT NULL DEFAULT '',
  status      ENUM('SUCCESS','RETRY','DEGRADED','FAILED','ESCALATED') NOT NULL,
  retry_count TINYINT NOT NULL DEFAULT 0,
  latency_ms  INT NOT NULL DEFAULT 0,
  tokens_in   INT NOT NULL DEFAULT 0,
  tokens_out  INT NOT NULL DEFAULT 0,
  cost_cny    DECIMAL(10,4) NOT NULL DEFAULT 0,
  output_digest VARCHAR(300) NOT NULL DEFAULT '' COMMENT '产出摘要，用于人事档案展示',
  error_msg   VARCHAR(500) NOT NULL DEFAULT '',
  created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  KEY idx_agent_time (agent_code, created_at),
  KEY idx_status (status, created_at)
) COMMENT='agent 运行日志：成功率算 KPI，FAILED/ESCALATED 直接导出成事故报告';

-- ------------------------------------------------------------
-- 15. 运营看板用的汇总视图（答辩现场直接 SELECT）
-- ------------------------------------------------------------
CREATE VIEW v_agent_kpi AS
SELECT agent_code,
       COUNT(*)                                              AS total_runs,
       SUM(status='SUCCESS')                                 AS success_runs,
       ROUND(SUM(status='SUCCESS')/COUNT(*)*100, 1)          AS success_rate,
       ROUND(AVG(latency_ms))                                AS avg_latency_ms,
       ROUND(SUM(cost_cny), 4)                               AS total_cost_cny
FROM agent_runs
GROUP BY agent_code;

CREATE VIEW v_revenue_daily AS
SELECT DATE(created_at) AS d,
       COUNT(*)                                        AS orders,
       SUM(price_cny)                                  AS gmv_cny,
       SUM(points)                                     AS points_sold
FROM recharge_orders
WHERE status='PAID'
GROUP BY DATE(created_at);

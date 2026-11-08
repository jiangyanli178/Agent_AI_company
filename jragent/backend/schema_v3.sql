-- 我的因子库：保存 AI 聊出来的因子组合
-- 注意 users.id 是 BIGINT UNSIGNED，外键必须完全一致，否则建表会失败
CREATE TABLE IF NOT EXISTS saved_factor_sets (
  id             BIGINT AUTO_INCREMENT PRIMARY KEY,
  user_id        BIGINT UNSIGNED NOT NULL,
  name           VARCHAR(80)  NOT NULL COMMENT '用户起的名字',
  strategy_code  VARCHAR(40)  NOT NULL DEFAULT '' COMMENT '基于哪个基础策略',
  symbol_code    VARCHAR(10)  NOT NULL DEFAULT '',
  params         JSON         NULL COMMENT '基础策略参数',
  conditions     JSON         NOT NULL COMMENT '因子条件数组',
  condition_text VARCHAR(500) NOT NULL DEFAULT '' COMMENT '人话描述',
  snapshot       JSON         NULL COMMENT '保存当时的改善数据',
  note           VARCHAR(500) NOT NULL DEFAULT '',
  created_at     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uk_user_name (user_id, name),
  KEY idx_user_time (user_id, created_at),
  CONSTRAINT fk_sf_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='用户收藏的因子组合';

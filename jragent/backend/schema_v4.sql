-- 策略报告：AI 现场生成的网页报告
CREATE TABLE IF NOT EXISTS strategy_reports (
  id          BIGINT AUTO_INCREMENT PRIMARY KEY,
  token       VARCHAR(32)   NOT NULL COMMENT '公开访问用的短码',
  user_id     BIGINT UNSIGNED NOT NULL,
  title       VARCHAR(120)  NOT NULL DEFAULT '',
  symbol_code VARCHAR(10)   NOT NULL DEFAULT '',
  strategy    VARCHAR(60)   NOT NULL DEFAULT '',
  spec        JSON          NULL COMMENT 'AI 决定的页面结构：模块、标题、结论',
  params      JSON          NULL,
  size_bytes  INT           NOT NULL DEFAULT 0,
  views       INT           NOT NULL DEFAULT 0,
  created_at  DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uk_token (token),
  KEY idx_user_time (user_id, created_at),
  CONSTRAINT fk_report_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='AI 生成的策略体检报告';

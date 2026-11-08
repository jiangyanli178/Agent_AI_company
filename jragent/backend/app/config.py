# -*- coding: utf-8 -*-
"""
配置：全部从 .env 读，代码里不出现任何密钥。
"""
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote_plus

from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/ 目录的绝对路径。
# 关键：不能用相对路径 ".env"，否则从项目根目录或 PyCharm 里启动时会读不到配置，
# 表现为数据库密码为空、MiMo key 为空，接口直接 500。
BASE_DIR = Path(__file__).resolve().parent.parent
ENV_FILE = BASE_DIR / ".env"


class Settings(BaseSettings):
    # 数据库
    DB_HOST: str = "127.0.0.1"
    DB_PORT: int = 3306
    DB_USER: str = "root"
    DB_PASSWORD: str = ""
    DB_NAME: str = "quant_shop"

    # 鉴权
    JWT_SECRET: str = "dev-only-secret"
    JWT_EXPIRE_HOURS: int = 168          # 7 天，避免演示当天 token 过期
    JWT_ALGORITHM: str = "HS256"

    # MiMo
    MIMO_API_BASE: str = "https://api.xiaomimimo.com/v1"
    MIMO_API_KEY: str = ""
    MIMO_MODEL: str = "mimo-v2.6-pro"

    # 联网搜索（做因子研究时用，不配就自动降级为「只用模型知识」）
    WEB_SEARCH_PROVIDER: str = ""        # 空=关闭；tavily / bocha / serper
    WEB_SEARCH_API_KEY: str = ""
    WEB_SEARCH_MAX_RESULTS: int = 5

    # 运营
    ADMIN_PHONE: str = ""
    MERCHANT_QR_IMAGE: str = "/static/pay-qr.png"
    PAY_MODE: str = "qr"                 # qr=收款码人工核销；mock=点按钮直接到账
    TZ: str = "Asia/Shanghai"
    REGISTER_DAILY_LIMIT: int = 10       # 同 IP 24h 注册上限；ADMIN_PHONE 不受限
    # 回测本身是本地计算，成本几乎为 0，所以默认免费。
    # 真正收费的是「AI 帮你解读」「参数优化」「观察列表日租」这些有算力成本的事。
    BACKTEST_FREE: bool = True
    FREE_BACKTEST_PER_DAY: int = 2       # 仅当 BACKTEST_FREE=False 时生效

    # 运行
    CORS_ORIGINS: str = "*"              # 生产环境改成具体域名
    AI_MAX_ROUNDS: int = 6               # 工具循环上限，防空转烧钱

    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def DATABASE_URL(self) -> str:
        # 密码里可能含 @ # / 等字符，必须转义，否则连接串会被解析错
        pwd = quote_plus(self.DB_PASSWORD)
        return (
            f"mysql+pymysql://{self.DB_USER}:{pwd}"
            f"@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}?charset=utf8mb4"
        )

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()

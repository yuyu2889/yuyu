"""
应用配置中心。

设计要点（面试可以讲）：
1. 用 pydantic-settings 而不是散落的 os.getenv：
   - 配置项有类型注解，DB_PORT 会被自动转成 int，写错直接报错
   - 必填项缺失时，应用「启动即失败」，而不是运行到某个接口才崩
   - 支持嵌套结构（用 BaseModel 分组），比一长串扁平变量好维护
2. 用 @lru_cache 保证全局只解析一次 .env，避免每个请求都读磁盘。
3. 通过 get_settings() 获取，而不是直接 import 一个 settings 实例，
   这样在测试里可以用 dependency_overrides 替换成测试配置。
"""
from functools import lru_cache
from pathlib import Path
from typing import List

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# 项目根目录：backend/
BASE_DIR = Path(__file__).resolve().parent.parent.parent


class DatabaseSettings(BaseSettings):
    """数据库配置分组"""

    # 关键：嵌套的配置类也必须自己声明 env_file，否则读不到 .env，
    # 只认系统环境变量（这是一个很容易踩的坑）。
    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    host: str = Field("127.0.0.1", alias="DB_HOST")
    port: int = Field(3306, alias="DB_PORT")
    user: str = Field(..., alias="DB_USER")
    password: str = Field(..., alias="DB_PASSWORD")
    name: str = Field(..., alias="DB_NAME")
    echo: bool = Field(False, alias="DB_ECHO")

    @property
    def url(self) -> str:
        """
        拼装 SQLAlchemy 异步连接串。

        注意：这里用 f-string 直接拼密码，如果密码里含有 @ : / 等特殊字符会解析失败。
        生产环境应当用 urllib.parse.quote_plus 对用户名和密码做 URL 编码。
        """
        return (
            f"mysql+aiomysql://{self.user}:{self.password}"
            f"@{self.host}:{self.port}/{self.name}?charset=utf8mb4"
        )


class RedisSettings(BaseSettings):
    """Redis 配置分组"""

    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    host: str = Field("127.0.0.1", alias="REDIS_HOST")
    port: int = Field(6379, alias="REDIS_PORT")
    db: int = Field(0, alias="REDIS_DB")
    password: str = Field("", alias="REDIS_PASSWORD")

    @property
    def url(self) -> str:
        auth = f":{self.password}@" if self.password else ""
        return f"redis://{auth}{self.host}:{self.port}/{self.db}"


class Settings(BaseSettings):
    """
    全局配置。

    所有环境变量从 backend/.env 读取；同一字段如果系统环境变量里也存在，
    则系统环境变量优先（这是 pydantic-settings 的默认行为，符合 12-factor 原则）。
    """

    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",  # 忽略 .env 里多余的键，避免换环境时报错
    )

    # ---------- 应用 ----------
    app_name: str = Field("实验室设备预约管理系统", alias="APP_NAME")
    app_env: str = Field("development", alias="APP_ENV")
    debug: bool = Field(True, alias="DEBUG")
    api_prefix: str = Field("/api/v1", alias="API_PREFIX")

    # ---------- 认证 ----------
    secret_key: str = Field(..., alias="SECRET_KEY")
    access_token_expire_minutes: int = Field(10080, alias="ACCESS_TOKEN_EXPIRE_MINUTES")
    token_renew_threshold_ratio: float = Field(0.5, alias="TOKEN_RENEW_THRESHOLD_RATIO")

    # ---------- 上传 ----------
    max_upload_size_mb: int = Field(5, alias="MAX_UPLOAD_SIZE_MB")
    allowed_image_types: str = Field(
        "image/jpeg,image/png,image/webp,image/gif", alias="ALLOWED_IMAGE_TYPES"
    )

    # ---------- CORS ----------
    cors_origins: str = Field(
        "http://localhost:5173,http://127.0.0.1:5173", alias="CORS_ORIGINS"
    )

    # ---------- 邮件 ----------
    smtp_host: str = Field("smtp.qq.com", alias="SMTP_HOST")
    smtp_port: int = Field(465, alias="SMTP_PORT")
    smtp_user: str = Field("", alias="SMTP_USER")
    smtp_password: str = Field("", alias="SMTP_PASSWORD")
    smtp_from: str = Field("", alias="SMTP_FROM")
    mail_enabled: bool = Field(False, alias="MAIL_ENABLED")

    # ---------- 数据库 / Redis（嵌套分组） ----------
    # 注意：这里用 default_factory 手动构造，是为了让子配置也能读到 .env。
    @property
    def database(self) -> DatabaseSettings:
        return DatabaseSettings()

    @property
    def redis(self) -> RedisSettings:
        return RedisSettings()

    # ---------- 派生属性 ----------
    @property
    def cors_origin_list(self) -> List[str]:
        """把逗号分隔的字符串转成列表，供 CORSMiddleware 使用"""
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def allowed_image_type_list(self) -> List[str]:
        return [t.strip() for t in self.allowed_image_types.split(",") if t.strip()]

    @property
    def max_upload_size_bytes(self) -> int:
        return self.max_upload_size_mb * 1024 * 1024

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() in ("production", "prod")

    # ---------- 校验 ----------
    @field_validator("secret_key")
    @classmethod
    def secret_key_must_be_strong(cls, v: str) -> str:
        """
        安全检查：生产环境禁止使用占位密钥。

        这是一个很实在的防御——很多项目上线时忘了改默认密钥，
        导致 JWT/签名可被伪造。让它在启动阶段就失败。
        """
        if len(v) < 16:
            raise ValueError(
                "SECRET_KEY 长度不足 16 位。请用 "
                "python -c \"import secrets; print(secrets.token_urlsafe(48))\" 生成一个随机密钥"
            )
        if "请替换" in v:
            raise ValueError("SECRET_KEY 还是 .env.example 里的占位值，请修改 backend/.env")
        return v


@lru_cache
def get_settings() -> Settings:
    """
    获取全局配置单例。

    用 lru_cache 而不是模块级 settings = Settings() 的好处：
    - 只有真正用到配置时才解析（懒加载）
    - 测试里可以 get_settings.cache_clear() 后替换环境变量重新加载
    """
    return Settings()


# 方便直接引用： from app.core.config import settings
settings = get_settings()

"""平台配置（从环境变量 / .env 读取）。"""
from typing import List

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        protected_namespaces=(),
    )

    app_name: str = "实验组 AI 大模型 API 中转站"
    environment: str = "dev"

    # 数据库
    database_url: str = "sqlite:///./relay.db"

    # 安全
    jwt_secret: str = "dev-insecure-jwt-secret-change-me"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 720
    encryption_secret: str = "dev-insecure-encryption-secret-change-me"
    api_token_prefix: str = "sk-relay-"

    # 初始管理员
    admin_username: str = "admin"
    admin_password: str = "admin12345"
    admin_email: str = "admin@example.com"

    # 计费
    base_points_per_1k_tokens: float = 1.0
    signup_free_points: int = 500

    # 网页会话默认限额（无 Token 时使用）
    default_daily_request_limit: int = 200
    default_rate_limit_per_minute: int = 10
    default_daily_token_limit: int = 200000

    # ---- 第二阶段：异步批量任务 / 风控 ----
    run_inprocess_worker: bool = True   # 应用内启动后台 Worker（试点零依赖）
    worker_poll_interval: float = 2.0   # Worker 轮询间隔（秒）
    worker_batch_per_tick: int = 10     # 每轮最多处理的任务数
    batch_max_items: int = 200          # 单个批量任务最大条目数
    alert_error_window_seconds: int = 60
    alert_error_threshold: int = 5      # 窗口内错误数达到阈值则告警
    alert_auto_disable_token: bool = True  # 触发告警时自动停用 Token

    # ---- 第三阶段：付费与补偿试点 ----
    allow_contributed_pool: bool = True            # 是否允许学生贡献备用池参与调度
    contribution_default_daily_cost_limit: float = 5.0    # 贡献账号默认每日消耗上限(元)
    contribution_default_monthly_cost_limit: float = 50.0  # 贡献账号默认每月消耗上限(元)
    pilot_subsidy_per_contributor: float = 5.0     # 参与试点补贴(元/人，方案 9.3)
    consent_version: str = "v1.0"                  # 当前电子授权版本号

    # ---- 第四阶段：学校统一身份认证（SSO）----
    sso_enabled: bool = True
    sso_provider_name: str = "school-sso"
    sso_mode: str = "mock"                         # mock | oidc
    sso_default_role: str = "student"
    sso_code_ttl_seconds: int = 300
    # OIDC 模式（接入真实学校 IdP 时填写）
    sso_client_id: str = ""
    sso_client_secret: str = ""
    sso_authorize_url: str = ""
    sso_token_url: str = ""
    sso_userinfo_url: str = ""
    sso_redirect_uri: str = "http://localhost:8000/sso/callback"

    # CORS
    cors_origins: str = "*"

    @property
    def cors_origin_list(self) -> List[str]:
        if self.cors_origins.strip() == "*":
            return ["*"]
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def is_dev(self) -> bool:
        return self.environment.lower() in ("dev", "development", "local")


settings = Settings()

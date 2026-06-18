"""ORM 模型 —— 对应方案文档第十六节数据库设计。

为便于本地 SQLite 开箱即用，在原 schema 基础上补充了少量实用列
（如 api_key_pool.base_url、usage_logs.model_level/source/token_id）。
"""
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)

from .database import Base


def _now() -> datetime:
    return datetime.utcnow()


class User(Base):
    """用户表 users（方案 16.1）。"""

    __tablename__ = "users"

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    username = Column(String(100), nullable=False, unique=True, index=True)
    email = Column(String(200))
    password_hash = Column(String(255), nullable=False)
    # 角色：student / graduate / group / teacher / admin
    role = Column(String(50), nullable=False, default="student")
    group_id = Column(BigInteger)
    # 状态：active / disabled
    status = Column(String(30), nullable=False, default="active")
    created_at = Column(DateTime, default=_now)
    updated_at = Column(DateTime, default=_now, onupdate=_now)


class UserApiToken(Base):
    """用户内部 API Token 表 user_api_tokens（方案 16.2 / 第七节）。"""

    __tablename__ = "user_api_tokens"

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, ForeignKey("users.id"), nullable=False, index=True)
    token_hash = Column(String(255), nullable=False, unique=True, index=True)
    token_prefix = Column(String(40))  # 仅用于前端展示（如 sk-relay-ab12…）
    token_name = Column(String(100))
    status = Column(String(30), nullable=False, default="active")  # active / disabled
    model_scope = Column(String(100), default="basic")  # 允许的最高模型等级
    daily_request_limit = Column(Integer, default=200)
    daily_token_limit = Column(BigInteger, default=200000)
    monthly_cost_limit = Column(Numeric(10, 2))
    rate_limit_per_minute = Column(Integer, default=10)
    allow_batch = Column(Boolean, default=False)
    expires_at = Column(DateTime)
    created_at = Column(DateTime, default=_now)
    last_used_at = Column(DateTime)


class Model(Base):
    """模型表 models（方案 16.3 / 第十二节）。"""

    __tablename__ = "models"

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    provider = Column(String(100), nullable=False)  # mock / openai / ...
    model_name = Column(String(200), nullable=False)  # 真实供应商模型名
    model_level = Column(String(50), nullable=False)  # basic / standard / advanced
    display_name = Column(String(200))  # 用户端展示名
    context_length = Column(Integer, default=8192)
    input_price = Column(Numeric(10, 6), default=0)  # 每 1K 输入 token 价格(元)
    output_price = Column(Numeric(10, 6), default=0)  # 每 1K 输出 token 价格(元)
    multiplier = Column(Numeric(10, 2), default=1)  # 扣点倍率
    capability_tags = Column(Text)
    enabled = Column(Boolean, default=True)
    created_at = Column(DateTime, default=_now)


class ApiKeyPool(Base):
    """Key 池表 api_key_pool（方案 16.4 / 第八节）。"""

    __tablename__ = "api_key_pool"

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    # school / group / contributed
    resource_pool_type = Column(String(50), nullable=False, default="school")
    provider = Column(String(100), nullable=False)
    account_name = Column(String(200))
    base_url = Column(String(300))  # OpenAI 兼容供应商的 base_url
    encrypted_api_key = Column(Text, nullable=False)  # 加密保存，用户不可见
    supported_models = Column(Text)  # 逗号分隔的模型名；为空表示通配
    status = Column(String(30), nullable=False, default="active")  # active / disabled
    priority = Column(Integer, default=0)  # 数字越小优先级越高
    rpm_limit = Column(Integer)
    tpm_limit = Column(BigInteger)
    daily_token_limit = Column(BigInteger)
    monthly_budget = Column(Numeric(10, 2))
    used_tokens_today = Column(BigInteger, default=0)
    used_budget_month = Column(Numeric(10, 2), default=0)
    last_error = Column(Text)
    created_at = Column(DateTime, default=_now)
    updated_at = Column(DateTime, default=_now, onupdate=_now)


class WalletAccount(Base):
    """用户点数账户表 wallet_account（方案 16.5 / 第十节）。"""

    __tablename__ = "wallet_account"

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, ForeignKey("users.id"), nullable=False, unique=True, index=True)
    free_points = Column(BigInteger, default=0)       # 基础免费额度
    paid_points = Column(BigInteger, default=0)       # 自愿购买额度
    subsidy_points = Column(BigInteger, default=0)    # 学校补贴额度
    project_points = Column(BigInteger, default=0)    # 课题组/项目额度
    total_used_points = Column(BigInteger, default=0)
    updated_at = Column(DateTime, default=_now, onupdate=_now)


class BillingRecord(Base):
    """消费流水表 billing_record（方案 16.6）。"""

    __tablename__ = "billing_record"

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, ForeignKey("users.id"), nullable=False, index=True)
    request_id = Column(String(100), index=True)
    model_level = Column(String(50))
    model_name = Column(String(100))
    task_type = Column(String(100))
    input_tokens = Column(BigInteger, default=0)
    output_tokens = Column(BigInteger, default=0)
    points_used = Column(BigInteger, default=0)
    estimated_cost = Column(Numeric(10, 4), default=0)
    balance_after = Column(BigInteger, default=0)
    created_at = Column(DateTime, default=_now, index=True)


class UsageLog(Base):
    """调用日志表 usage_logs（方案 16.8 / 第十一节审计）。"""

    __tablename__ = "usage_logs"

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, ForeignKey("users.id"), nullable=False, index=True)
    group_id = Column(BigInteger)
    token_id = Column(BigInteger)
    source = Column(String(20), default="api")  # web / api
    provider = Column(String(100))
    model_level = Column(String(50))
    model_name = Column(String(200))
    key_id = Column(BigInteger)
    task_type = Column(String(100))
    input_tokens = Column(BigInteger, default=0)
    output_tokens = Column(BigInteger, default=0)
    estimated_cost = Column(Numeric(10, 4), default=0)
    latency_ms = Column(Integer, default=0)
    status = Column(String(30), default="success")  # success / error
    error_code = Column(String(100))
    created_at = Column(DateTime, default=_now, index=True)


class ContributedApiKey(Base):
    """学生自愿贡献账号授权表 contributed_api_keys（方案 16.7）。

    MVP 阶段保留表结构以便第三阶段直接启用，业务逻辑暂未实现。
    """

    __tablename__ = "contributed_api_keys"

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    contributor_user_id = Column(BigInteger, ForeignKey("users.id"), nullable=False)
    provider = Column(String(100), nullable=False)
    encrypted_api_key = Column(Text, nullable=False)
    allowed_model_levels = Column(String(100))
    daily_cost_limit = Column(Numeric(10, 2))
    monthly_cost_limit = Column(Numeric(10, 2))
    used_cost_today = Column(Numeric(10, 2), default=0)
    used_cost_month = Column(Numeric(10, 2), default=0)
    allowed_task_types = Column(Text)
    allow_sensitive_data = Column(Boolean, default=False)
    status = Column(String(30), nullable=False, default="pending")
    consent_version = Column(String(50))
    consent_time = Column(DateTime)
    revoked_at = Column(DateTime)
    expires_at = Column(DateTime)
    created_at = Column(DateTime, default=_now)
    updated_at = Column(DateTime, default=_now, onupdate=_now)


# ============================ 第二阶段：科研 API 增强 ============================


class ResearchGroup(Base):
    """课题组 / 项目（方案第八节课题组资源池 / 第十节项目额度）。

    持有一份共享点数额度 project_points，供组内成员的科研 API 调用优先扣除。
    """

    __tablename__ = "research_groups"

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    name = Column(String(200), nullable=False, unique=True)
    owner_user_id = Column(BigInteger)
    project_points = Column(BigInteger, default=0)  # 课题组共享额度
    total_used_points = Column(BigInteger, default=0)
    daily_point_limit = Column(BigInteger)  # 可选每日上限（预留）
    status = Column(String(30), nullable=False, default="active")
    created_at = Column(DateTime, default=_now)
    updated_at = Column(DateTime, default=_now, onupdate=_now)


class Job(Base):
    """批量异步任务（方案 13.3 / 第十四节异步任务队列）。"""

    __tablename__ = "jobs"

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, ForeignKey("users.id"), nullable=False, index=True)
    group_id = Column(BigInteger)
    token_id = Column(BigInteger)
    source = Column(String(20), default="api")  # api / web
    job_type = Column(String(50), nullable=False)
    model_level = Column(String(50), nullable=False, default="basic")
    task_type = Column(String(100))
    # pending_confirm / queued / running / completed / failed / canceled
    status = Column(String(30), nullable=False, default="pending_confirm", index=True)
    total_items = Column(Integer, default=0)
    processed_items = Column(Integer, default=0)
    failed_items = Column(Integer, default=0)
    estimated_points = Column(BigInteger, default=0)
    points_used = Column(BigInteger, default=0)
    max_tokens = Column(Integer, default=256)
    error = Column(Text)
    created_at = Column(DateTime, default=_now, index=True)
    updated_at = Column(DateTime, default=_now, onupdate=_now)
    confirmed_at = Column(DateTime)
    finished_at = Column(DateTime)


class JobItem(Base):
    """批量任务的单个条目。"""

    __tablename__ = "job_items"

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    job_id = Column(BigInteger, ForeignKey("jobs.id"), nullable=False, index=True)
    item_ref = Column(String(200))  # 用户提供的条目 id
    seq = Column(Integer, default=0)
    input_text = Column(Text)
    output_text = Column(Text)
    status = Column(String(30), default="pending")  # pending / done / error
    input_tokens = Column(BigInteger, default=0)
    output_tokens = Column(BigInteger, default=0)
    points_used = Column(BigInteger, default=0)
    error = Column(String(255))
    created_at = Column(DateTime, default=_now)
    updated_at = Column(DateTime, default=_now, onupdate=_now)


class Alert(Base):
    """异常调用告警（方案第十五节异常调用告警 / 第二十节风控）。"""

    __tablename__ = "alerts"

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, index=True)
    token_id = Column(BigInteger)
    alert_type = Column(String(50))  # high_error_rate / rate_abuse / budget_breach
    severity = Column(String(20), default="warning")  # info / warning / critical
    message = Column(Text)
    status = Column(String(20), default="open")  # open / resolved
    auto_action = Column(String(50), default="none")  # token_disabled / none
    created_at = Column(DateTime, default=_now, index=True)
    resolved_at = Column(DateTime)

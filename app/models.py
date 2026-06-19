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


# ============================ 第三阶段：付费与补偿试点 ============================


class Package(Base):
    """点数套餐（方案 11.2）。学生自愿购买，不与成绩/考核挂钩。"""

    __tablename__ = "packages"

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    code = Column(String(50), nullable=False, unique=True)
    name = Column(String(100), nullable=False)
    price = Column(Numeric(10, 2), nullable=False, default=0)  # 价格(元)
    points = Column(BigInteger, nullable=False, default=0)
    audience = Column(String(200))  # 适用对象
    application_only = Column(Boolean, default=False)  # 申请制（如高级包）
    enabled = Column(Boolean, default=True)
    sort = Column(Integer, default=0)
    created_at = Column(DateTime, default=_now)


class Order(Base):
    """充值订单（方案 11.1：价格公开、明细可查、走学校正规财务渠道）。"""

    __tablename__ = "orders"

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    order_no = Column(String(64), nullable=False, unique=True, index=True)
    user_id = Column(BigInteger, ForeignKey("users.id"), nullable=False, index=True)
    package_id = Column(BigInteger)
    package_code = Column(String(50))
    amount = Column(Numeric(10, 2), nullable=False, default=0)  # 应付金额(元)
    points = Column(BigInteger, nullable=False, default=0)
    # pending / paid / canceled / refunded
    status = Column(String(20), nullable=False, default="pending", index=True)
    pay_channel = Column(String(50))  # school_finance / mock / ...
    external_ref = Column(String(120))  # 学校财务流水号
    created_at = Column(DateTime, default=_now, index=True)
    paid_at = Column(DateTime)
    refunded_at = Column(DateTime)


# ============================ 第四阶段：学校级扩展预留 ============================


class OrgUnit(Base):
    """组织单元：学院 / 专业 / 课题组（方案第四阶段多级管理）。

    通过 parent_id 形成树：college → major → group。
    """

    __tablename__ = "org_units"

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    name = Column(String(200), nullable=False)
    unit_type = Column(String(20), nullable=False)  # college / major / group
    parent_id = Column(BigInteger, index=True)
    code = Column(String(80))
    status = Column(String(20), nullable=False, default="active")
    created_at = Column(DateTime, default=_now)


class OrgMembership(Base):
    """用户所属组织单元（一人一个叶子单元，祖先通过 parent_id 推导）。"""

    __tablename__ = "org_memberships"

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, ForeignKey("users.id"), nullable=False, unique=True, index=True)
    org_unit_id = Column(BigInteger, index=True)
    created_at = Column(DateTime, default=_now)
    updated_at = Column(DateTime, default=_now, onupdate=_now)


class Budget(Base):
    """预算与熔断（方案第四阶段学校级预算熔断 / 第二十节费用失控应对）。

    scope=school 为全局学校预算；scope=org 绑定某 org_unit。used_points 达到
    limit_points 时 status 置 tripped，平台拒绝新的调用直至管理员调整或重置。
    """

    __tablename__ = "budgets"

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    scope = Column(String(20), nullable=False, default="school")  # school / org
    org_unit_id = Column(BigInteger)
    period_key = Column(String(20))  # 如 2026-06（预留按月滚动）
    limit_points = Column(BigInteger, nullable=False, default=0)
    used_points = Column(BigInteger, nullable=False, default=0)
    status = Column(String(20), nullable=False, default="active")  # active / tripped / disabled
    note = Column(String(200))
    created_at = Column(DateTime, default=_now)
    updated_at = Column(DateTime, default=_now, onupdate=_now)


class AuditLog(Base):
    """管理操作审计（方案第四阶段大规模日志审计 / 第十九节合规）。"""

    __tablename__ = "audit_logs"

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    actor_user_id = Column(BigInteger, index=True)
    actor_username = Column(String(100))
    action = Column(String(80), nullable=False)
    target_type = Column(String(50))
    target_id = Column(String(80))
    detail = Column(Text)
    created_at = Column(DateTime, default=_now, index=True)


class SsoIdentity(Base):
    """学校统一身份认证身份映射（方案第四阶段统一身份认证）。"""

    __tablename__ = "sso_identities"
    __table_args__ = (UniqueConstraint("provider", "subject", name="uq_sso_provider_subject"),)

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    provider = Column(String(50), nullable=False)
    subject = Column(String(120), nullable=False)  # IdP 中的唯一标识 sub
    user_id = Column(BigInteger, ForeignKey("users.id"), nullable=False, index=True)
    created_at = Column(DateTime, default=_now)


# ============================ 共享账户（多人共用一个账户，受聚合速率/并发上限约束）============================


class SharedAccount(Base):
    """共享账户：一个账户凭据可被多名成员共用。

    在 聚合每分钟速率 / 并发 / 每日次数 不超过上限时，允许多名成员并发使用；
    消耗统一计入账户拥有者钱包。库内只存共享 Token 的哈希。
    """

    __tablename__ = "shared_accounts"

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False)
    owner_user_id = Column(BigInteger, ForeignKey("users.id"), nullable=False, index=True)
    token_hash = Column(String(255), nullable=False, unique=True, index=True)
    token_prefix = Column(String(40))
    token_hash = Column(String(255), nullable=False, unique=True, index=True)  # 账户内部标识（成员用各自的 member-token）
    token_prefix = Column(String(40))
    model_scope = Column(String(50), default="basic")
    rate_limit_per_minute = Column(Integer, default=60)   # 聚合每分钟请求上限
    max_concurrency = Column(Integer, default=5)          # 最大并发在途请求（0=不限）
    daily_request_limit = Column(Integer, default=5000)   # 聚合每日请求上限
    daily_token_limit = Column(BigInteger)                # 聚合每日 token 上限（可空=不限）
    default_member_rpm = Column(Integer)                  # 新成员默认每分钟上限
    default_member_daily = Column(Integer)                # 新成员默认每日次数上限
    restrict_members = Column(Boolean, default=False)     # 预留：是否仅允许已登记成员
    status = Column(String(30), nullable=False, default="active")
    created_at = Column(DateTime, default=_now)


class SharedMember(Base):
    """共享账户成员：每人持有独立 member-token，承载每成员限额、偏好与用量统计。"""

    __tablename__ = "shared_members"
    __table_args__ = (UniqueConstraint("shared_account_id", "member_label", name="uq_shared_member"),)

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    shared_account_id = Column(BigInteger, ForeignKey("shared_accounts.id"), nullable=False, index=True)
    member_label = Column(String(120), nullable=False)  # 成员标识（用户名/邮箱/设备号等）
    token_hash = Column(String(255), unique=True, index=True)  # 该成员独立凭据的哈希
    token_prefix = Column(String(40))
    status = Column(String(30), nullable=False, default="active")  # active / disabled
    # —— 拥有者为该成员设置的限额/权限（None 则回退账户默认）——
    rpm_limit = Column(Integer)
    daily_request_limit = Column(Integer)
    token_limit = Column(BigInteger)        # 该成员累计 token 上限
    model_scope = Column(String(50))        # 该成员最高模型等级（≤账户）
    expires_at = Column(DateTime)
    note = Column(String(200))
    # —— 成员自助偏好 ——
    display_name = Column(String(120))
    default_model_level = Column(String(50))
    default_max_tokens = Column(Integer)
    default_temperature = Column(Numeric(4, 2))
    # —— 用量统计 ——
    request_count = Column(BigInteger, default=0)
    token_count = Column(BigInteger, default=0)
    last_used_at = Column(DateTime)
    created_at = Column(DateTime, default=_now)
    updated_at = Column(DateTime, default=_now, onupdate=_now)


class SharedCall(Base):
    """共享账户下按成员隔离的调用/对话记录。

    每条记录绑定 member_id，使服务端能区分不同成员的请求、互不混淆；
    成员只能检索属于自己的历史。
    """

    __tablename__ = "shared_calls"

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    shared_account_id = Column(BigInteger, ForeignKey("shared_accounts.id"), nullable=False, index=True)
    member_id = Column(BigInteger, ForeignKey("shared_members.id"), nullable=False, index=True)
    member_label = Column(String(120), index=True)
    request_id = Column(String(100))
    model_level = Column(String(50))
    model_name = Column(String(120))
    prompt = Column(Text)     # 该成员本次最后一条用户消息
    response = Column(Text)   # 模型回复
    input_tokens = Column(BigInteger, default=0)
    output_tokens = Column(BigInteger, default=0)
    points_used = Column(BigInteger, default=0)
    created_at = Column(DateTime, default=_now, index=True)

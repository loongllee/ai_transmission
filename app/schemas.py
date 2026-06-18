"""Pydantic 请求/响应模型。"""
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field

# pydantic v2 默认对 model_ 前缀字段告警，这里整体关闭保护命名空间。
_CFG = ConfigDict(protected_namespaces=(), from_attributes=True)


# ---------- 认证 ----------
class RegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=100)
    password: str = Field(min_length=6, max_length=128)
    email: Optional[str] = None


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserOut(BaseModel):
    model_config = _CFG
    id: int
    username: str
    email: Optional[str] = None
    role: str
    status: str
    group_id: Optional[int] = None
    created_at: Optional[datetime] = None


# ---------- 聊天 / LLM ----------
class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    model_config = _CFG
    model_level: str = "basic"  # basic / standard / advanced
    task_type: str = "chat"
    messages: List[ChatMessage]
    max_tokens: Optional[int] = 512
    temperature: Optional[float] = 0.7


class CompletionRequest(BaseModel):
    model_config = _CFG
    model_level: str = "basic"
    task_type: str = "completion"
    prompt: str
    max_tokens: Optional[int] = 512
    temperature: Optional[float] = 0.7


class UsageInfo(BaseModel):
    input_tokens: int
    output_tokens: int
    points_used: int
    from_group: int = 0  # 本次从课题组共享额度扣除的点数


class ChatResponse(BaseModel):
    model_config = _CFG
    request_id: str
    model_level: str
    model: str
    content: str
    usage: UsageInfo
    balance_after: int


# ---------- API Token 管理 ----------
class CreateTokenRequest(BaseModel):
    model_config = _CFG
    token_name: Optional[str] = "default"
    model_scope: Optional[str] = "basic"
    allow_batch: Optional[bool] = False


class ApiTokenOut(BaseModel):
    model_config = _CFG
    id: int
    token_prefix: Optional[str] = None
    token_name: Optional[str] = None
    status: str
    model_scope: Optional[str] = None
    daily_request_limit: Optional[int] = None
    daily_token_limit: Optional[int] = None
    rate_limit_per_minute: Optional[int] = None
    allow_batch: Optional[bool] = None
    expires_at: Optional[datetime] = None
    last_used_at: Optional[datetime] = None
    created_at: Optional[datetime] = None


class ApiTokenCreatedOut(ApiTokenOut):
    plaintext_token: str  # 仅创建/重置时返回一次


# ---------- 钱包 / 额度 / 用量 ----------
class WalletOut(BaseModel):
    model_config = _CFG
    free_points: int
    paid_points: int
    subsidy_points: int
    project_points: int
    total_used_points: int
    balance: int


class QuotaOut(BaseModel):
    model_config = _CFG
    role: str
    balance: int
    daily_request_limit: int
    daily_token_limit: int
    rate_limit_per_minute: int


class UsageLogOut(BaseModel):
    model_config = _CFG
    id: int
    source: Optional[str] = None
    model_level: Optional[str] = None
    model_name: Optional[str] = None
    task_type: Optional[str] = None
    input_tokens: int
    output_tokens: int
    estimated_cost: Optional[float] = None
    latency_ms: Optional[int] = None
    status: Optional[str] = None
    created_at: Optional[datetime] = None


# ---------- 管理员 ----------
class ModelIn(BaseModel):
    model_config = _CFG
    provider: str
    model_name: str
    model_level: str
    display_name: Optional[str] = None
    context_length: int = 8192
    input_price: float = 0
    output_price: float = 0
    multiplier: float = 1
    capability_tags: Optional[str] = None
    enabled: bool = True


class ModelOut(ModelIn):
    id: int


class KeyIn(BaseModel):
    model_config = _CFG
    resource_pool_type: str = "school"
    provider: str
    account_name: Optional[str] = None
    base_url: Optional[str] = None
    api_key: str  # 明文，仅写入时提供，后端加密保存
    supported_models: Optional[str] = None
    status: str = "active"
    priority: int = 0
    monthly_budget: Optional[float] = None
    daily_token_limit: Optional[int] = None


class KeyOut(BaseModel):
    """注意：绝不返回明文/密文 Key。"""

    model_config = _CFG
    id: int
    resource_pool_type: str
    provider: str
    account_name: Optional[str] = None
    base_url: Optional[str] = None
    supported_models: Optional[str] = None
    status: str
    priority: int
    monthly_budget: Optional[float] = None
    daily_token_limit: Optional[int] = None
    used_tokens_today: Optional[int] = None
    last_error: Optional[str] = None
    created_at: Optional[datetime] = None


class GrantPointsRequest(BaseModel):
    bucket: str = "subsidy"  # free / paid / subsidy / project
    points: int


class UpdateUserRequest(BaseModel):
    role: Optional[str] = None
    status: Optional[str] = None
    group_id: Optional[int] = None


# ---------- 第二阶段：批量任务 ----------
class BatchItem(BaseModel):
    id: Optional[str] = None
    text: str


class JobEstimateRequest(BaseModel):
    model_config = _CFG
    model_level: str = "basic"
    items: List[BatchItem]
    max_tokens: Optional[int] = 256


class JobEstimateOut(BaseModel):
    items: int
    estimated_input_tokens: int
    estimated_points: int
    model_available: bool


class JobCreateRequest(BaseModel):
    model_config = _CFG
    job_type: str  # batch_summary / batch_translate / batch_classify / batch_code_explain / batch_completion
    model_level: str = "basic"
    task_type: Optional[str] = None
    items: List[BatchItem]
    max_tokens: Optional[int] = 256
    auto_confirm: bool = False


class JobOut(BaseModel):
    model_config = _CFG
    id: int
    job_type: str
    model_level: str
    task_type: Optional[str] = None
    status: str
    total_items: int
    processed_items: int
    failed_items: int
    estimated_points: int
    points_used: int
    error: Optional[str] = None
    created_at: Optional[datetime] = None
    confirmed_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None


class JobItemOut(BaseModel):
    model_config = _CFG
    item_ref: Optional[str] = None
    seq: int
    status: str
    input_tokens: int
    output_tokens: int
    points_used: int
    output_text: Optional[str] = None
    error: Optional[str] = None


class JobResultOut(BaseModel):
    job: JobOut
    items: List[JobItemOut]


# ---------- 第二阶段：课题组 / 项目额度 ----------
class GroupIn(BaseModel):
    name: str
    owner_user_id: Optional[int] = None
    project_points: int = 0


class GroupOut(BaseModel):
    model_config = _CFG
    id: int
    name: str
    owner_user_id: Optional[int] = None
    project_points: int
    total_used_points: int
    status: str


class GroupGrantRequest(BaseModel):
    points: int


class AddMemberRequest(BaseModel):
    user_id: int


class GroupStatsOut(BaseModel):
    group_id: int
    name: str
    members: int
    project_points_remaining: int
    total_used_points: int
    total_calls: int
    total_tokens: int


# ---------- 第二阶段：告警 ----------
class AlertOut(BaseModel):
    model_config = _CFG
    id: int
    user_id: Optional[int] = None
    token_id: Optional[int] = None
    alert_type: Optional[str] = None
    severity: Optional[str] = None
    message: Optional[str] = None
    status: str
    auto_action: Optional[str] = None
    created_at: Optional[datetime] = None


# ---------- 第三阶段：套餐 / 充值订单 ----------
class PackageOut(BaseModel):
    model_config = _CFG
    id: int
    code: str
    name: str
    price: float
    points: int
    audience: Optional[str] = None
    application_only: bool = False
    enabled: bool = True


class PackageIn(BaseModel):
    code: str
    name: str
    price: float = 0
    points: int = 0
    audience: Optional[str] = None
    application_only: bool = False
    enabled: bool = True
    sort: int = 0


class CreateOrderRequest(BaseModel):
    package_code: str


class PayOrderRequest(BaseModel):
    pay_channel: str = "mock"
    external_ref: Optional[str] = None


class OrderOut(BaseModel):
    model_config = _CFG
    id: int
    order_no: str
    user_id: Optional[int] = None
    package_code: Optional[str] = None
    amount: float
    points: int
    status: str
    pay_channel: Optional[str] = None
    external_ref: Optional[str] = None
    created_at: Optional[datetime] = None
    paid_at: Optional[datetime] = None


# ---------- 第三阶段：学生自愿贡献账号 ----------
class ContributionIn(BaseModel):
    model_config = _CFG
    provider: str = "mock"
    api_key: str  # 明文，仅提交时提供，后端加密保存
    allowed_model_levels: Optional[str] = "basic"
    daily_cost_limit: Optional[float] = None
    monthly_cost_limit: Optional[float] = None
    allowed_task_types: Optional[str] = None
    allow_sensitive_data: bool = False
    consent: bool = False  # 知情同意，必须为 true


class ContributionOut(BaseModel):
    """不返回任何明文/密文 Key。"""

    model_config = _CFG
    id: int
    provider: str
    allowed_model_levels: Optional[str] = None
    daily_cost_limit: Optional[float] = None
    monthly_cost_limit: Optional[float] = None
    used_cost_today: Optional[float] = None
    used_cost_month: Optional[float] = None
    allow_sensitive_data: Optional[bool] = None
    status: str
    consent_version: Optional[str] = None
    consent_time: Optional[datetime] = None
    revoked_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    created_at: Optional[datetime] = None


class CompensationOut(BaseModel):
    contributor_user_id: int
    username: Optional[str] = None
    accounts: int
    total_cost: float
    pilot_subsidy: float
    suggested_compensation: float

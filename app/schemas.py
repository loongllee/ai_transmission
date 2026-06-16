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

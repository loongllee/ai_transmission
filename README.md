# 实验组 AI 大模型 API 中转站（Phase 1 MVP）

面向实验组成员的统一 AI 大模型 API 中转与资源管理平台。通过 **统一身份认证、内部 API Token、模型路由、Key 池管理、点数计费、调用审计** 等机制，为组内提供安全可控的大模型调用服务。

本仓库实现方案文档《AI 大模型 API 中转站构建方案》的 **第一阶段：最小可用版本（MVP）**。

技术栈：**FastAPI + SQLAlchemy + SQLite（默认）/ PostgreSQL / MySQL + Redis（可选）**。内置 **mock 供应商**，无需任何真实 API Key 即可跑通全链路。

---

## ✨ 已实现能力（对应方案第二十一节第一阶段验收）

| 能力 | 说明 |
|------|------|
| 用户登录/注册 | JWT 会话认证（方案第七节） |
| 网页基础聊天 | 选择模型等级、实时扣点、余额展示（方案 6.1） |
| 内部 API Token | 发放/重置/停用；库内只存哈希，明文只显示一次（方案第七节） |
| 通用 API 调用 | `/api/v1/llm/chat`、`/completions`（方案第十三节） |
| Key 池管理 | 真实 Key 加密存储、用户不可见、按资源池优先级调度（方案第八节） |
| 点数账户 | 免费/补贴/项目/自购四类额度，按扣费顺序结算（方案第十节） |
| 按量计费 | 按输入/输出 Token × 模型倍率扣点（基础 1x/标准 3x/高级 10x） |
| 调用日志 | 全量审计、错误记录（方案第十六节 usage_logs） |
| 管理员后台 | 用户/模型/Key/日志管理、额度发放、统计（方案第十五节） |
| 限流熔断 | 每分钟/每日请求数、每日 Token 上限（方案第十节，进程内实现） |

> 第二/三/四阶段（异步批量任务队列、课题组额度、学生自愿购买 Token、贡献账号补偿、学校统一认证等）尚未实现；`contributed_api_keys` 表结构已预留。

---

## 🚀 快速开始

### 1. 安装依赖（建议虚拟环境）

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 2. 配置（可选）

```powershell
copy .env.example .env   # 按需修改密钥、数据库、管理员账号
```

不配置也能直接运行：默认 SQLite + 内置 mock 供应商。

### 3. 启动

```powershell
uvicorn app.main:app --reload --port 8000
```

打开浏览器访问 **http://localhost:8000**

- 默认管理员：`admin` / `admin12345`（首次启动自动创建）
- 交互式接口文档（Swagger）：http://localhost:8000/docs

---

## 🧩 科研程序化 API 调用

登录后在「API Token」页创建平台内部 Token（形如 `sk-relay-...`），即可在程序中调用：

```python
import httpx

r = httpx.post(
    "http://localhost:8000/api/v1/llm/chat",
    headers={"Authorization": "Bearer sk-relay-你的Token"},
    json={
        "model_level": "basic",          # basic / standard / advanced
        "task_type": "research_chat",
        "messages": [{"role": "user", "content": "请帮我解释这段实验结果。"}],
    },
)
print(r.json()["content"])
```

接口一览（方案第十三节）：

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/v1/llm/chat` | 通用聊天 |
| POST | `/api/v1/llm/completions` | 文本生成 |
| GET  | `/api/v1/quota/me` | 查询个人额度 |
| GET  | `/api/v1/usage/me` | 查询调用记录 |
| GET  | `/api/v1/wallet/me` | 查询点数账户 |

---

## 🔌 接入真实大模型供应商

平台支持任意 **OpenAI 兼容** 接口（OpenAI / DeepSeek / 通义千问 / 本地 vLLM 等）。

1. 用管理员账号登录 → 「管理后台」→「Key 池管理」→「添加 Key」
   - provider：如 `openai`
   - base_url：如 `https://api.openai.com/v1`
   - 真实 API Key：加密保存，列表与接口均不可见
2. 「模型管理」中新增/启用对应 `provider + model_name`，并设好 `model_level` 与扣点 `multiplier`
3. 用户端只看到 `基础/标准/高级` 三个等级，后端自动映射到真实模型与 Key

> 真实 Key 通过 Fernet 加密（密钥由 `ENCRYPTION_SECRET` 派生），用户与前端永远无法读取明文。

---

## 🗂️ 项目结构

```
app/
  main.py        FastAPI 入口、路由挂载、静态前端
  config.py      配置（.env）
  database.py    SQLAlchemy 引擎/会话
  models.py      ORM 模型（方案第十六节数据库设计）
  schemas.py     Pydantic 请求/响应
  security.py    密码哈希 / JWT / Key 加密 / Token 生成
  deps.py        认证依赖与 Principal（统一网页/API 调用主体）
  ratelimit.py   进程内限流
  providers.py   供应商适配（mock + OpenAI 兼容）
  billing.py     模型路由 / Key 调度 / 点数计费
  chat.py        调用编排：限流→路由→余额→调用→扣费→审计
  seed.py        建表 + 初始管理员 + mock 模型/Key
  routers/
    auth.py      注册/登录
    web.py       网页端（钱包/Token/用量/聊天）
    v1.py        科研 API（方案第十三节）
    admin.py     管理后台（方案第十五节）
frontend/
  index.html     自包含单页前端（聊天/钱包/Token/用量/文档/管理）
```

---

## 🔐 安全与合规要点（方案第十九节）

- 真实供应商 Key 后端加密存储，用户/前端不可见、不可导出
- 用户只持有平台内部 Token，库内只存哈希，泄露可一键重置/停用
- 不提供通用代理、不对外注册、不转发任意 URL
- 生产部署务必修改 `JWT_SECRET`、`ENCRYPTION_SECRET`、管理员密码
- 进程内限流仅适用单实例试点；多实例请替换为 Redis

---

## 🛣️ 后续阶段（Roadmap）

- **第二阶段**：批量任务接口、异步队列（Worker）、课题组/项目额度、费用预估、异常告警
- **第三阶段**：套餐与充值订单、消费流水报表、学生自愿购买 Token、贡献账号授权与补偿
- **第四阶段**：学校统一身份认证、多级（学院/专业/课题组）管理、预算熔断、大规模审计与运维

---

本项目为实验组内部试点，仅供学习与科研辅助使用。

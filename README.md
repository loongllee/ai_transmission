# 实验组 AI 大模型 API 中转站（Phase 1 MVP）

面向实验组成员的统一 AI 大模型 API 中转与资源管理平台。通过 **统一身份认证、内部 API Token、模型路由、Key 池管理、点数计费、调用审计** 等机制，为组内提供安全可控的大模型调用服务。

本仓库实现方案文档《AI 大模型 API 中转站构建方案》的 **第一阶段：最小可用版本（MVP）**。

技术栈：**FastAPI + SQLAlchemy + SQLite（默认）/ PostgreSQL / MySQL + Redis（可选）**。内置 **mock 供应商**，无需任何真实 API Key 即可跑通全链路。

---

## ✨ 已实现能力

### 第一阶段：最小可用版本（方案第二十一节第一阶段验收）

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

### 第二阶段：科研 API 增强（方案第二十一节第二阶段验收）

| 能力 | 说明 |
|------|------|
| 批量任务接口 | `/api/v1/jobs` 提交批量摘要/翻译/分类/代码解释/生成（方案 13.3） |
| 异步任务队列 | 数据库队列 + Worker（应用内线程，或 `python -m app.worker` 独立扩展）（方案第十四节） |
| 费用预估 | `/api/v1/jobs/estimate`，确认后入队（方案第十四节“估算→确认”） |
| 课题组/项目额度 | 课题组共享额度池，科研 API 优先扣除；成员/充值/用量统计（方案第八、十节） |
| 异常告警与封禁 | 滑动窗口错误检测，超阈值告警并自动停用 Token（方案第十五、二十节） |
| 任务状态/结果 | `/api/v1/jobs/{id}`、`/results`，网页端「批量任务」页可视化 |

### 第三阶段：付费与补偿试点（方案第二十一节第三阶段验收）

| 能力 | 说明 |
|------|------|
| 套餐与充值订单 | 套餐管理 + 自愿购买下单 + 支付入账（轻量/标准/科研/高级，方案 11.2） |
| 消费明细 | 订单与点数流水可查；自愿购买不与成绩/考核挂钩（方案 11.1） |
| 付费意愿统计 | 付费人数/转化率/复购/人均/套餐分布，**匿名化汇总**（方案 11.3） |
| 学生自愿贡献账号 | 知情同意提交、加密保存、**可随时撤回**、额度与用途受限（方案第九节） |
| 贡献备用池调度 | 主资源池不可用时降级到学生贡献账号（仅低风险 basic，方案 9.5） |
| 补偿统计 | 按贡献者汇总实际消耗 + 试点补贴 = 建议补偿金额（方案 9.3） |

### 第四阶段：学校级扩展预留（方案第二十一节第四阶段）

| 能力 | 说明 |
|------|------|
| 学校统一身份认证 | SSO（内置 mock IdP 离线可用 / 可切 OIDC），首次登录自动开户 + 绑定组织 |
| 多级组织管理 | 学院/专业/课题组树（`OrgUnit`），成员归属与子树用量汇总 |
| 学校级预算熔断 | 全局点数预算，用量达上限自动熔断、暂停调用，可重置（方案第二十节费用失控应对） |
| 大规模日志审计 | 管理操作审计 `AuditLog`（建组/分配/预算/封禁/退款留痕） |
| 学校级统计报表 | `/reports/overview` 总览 + `/reports/by-org` 按组织汇总 |
| 运维与合规 | 就绪检查 `/api/health/ready`、系统信息、合规声明 `/api/compliance` |

> 四个阶段均已落地。生产化（真实 OIDC、Redis 限流、PostgreSQL、ELK/Prometheus、按月预算滚动等）按方案第十七节部署建议继续演进。

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

## 🐳 Docker 部署

镜像自包含（默认 SQLite + mock 供应商），一条命令即可运行：

```bash
# 方式一：Docker Compose（推荐，含数据持久化卷）
docker compose up -d --build
# 访问 http://localhost:8000，停止：docker compose down

# 方式二：纯 docker
docker build -t ai-transmission:latest .
docker run -d -p 8000:8000 -v relay_data:/data \
  -e JWT_SECRET=请改成长随机串 \
  -e ENCRYPTION_SECRET=请改成另一个长随机串 \
  --name ai-transmission ai-transmission:latest
```

- SQLite 数据落在容器卷 `/data`（`relay_data`），重建容器不丢数据
- 内置 `HEALTHCHECK`，`docker ps` 可见健康状态
- 生产切换 PostgreSQL/Redis：放开 `docker-compose.yml` 中的 `db`/`redis` 服务，
  并把 `DATABASE_URL` 指向 `postgresql+psycopg://...`（需在 `requirements.txt` 启用 `psycopg`）

---

## 🏭 生产化部署

试点用 SQLite + 进程内限流即可；面向学院/学校规模时按方案第十七节加固：

```bash
# 生产编排：app + PostgreSQL + Redis + 独立 Worker（镜像装 requirements-prod.txt）
mkdir -p secrets && openssl rand -hex 32 > secrets/jwt_secret.txt && openssl rand -hex 32 > secrets/encryption_secret.txt
docker compose -f docker-compose.prod.yml up -d --build
```

已内置的生产能力：

| 维度 | 能力 | 开关 |
|------|------|------|
| 数据库 | PostgreSQL / MySQL | `DATABASE_URL=postgresql+psycopg://...`（装 `requirements-prod.txt`）|
| 分布式限流 | Redis 固定窗口，故障自动回退进程内 | `REDIS_URL=redis://...` |
| 监控 | Prometheus 指标 `/metrics`（请求/错误/token/点数）| `METRICS_ENABLED=true` |
| 日志 | 结构化 JSON（ELK/Loki 友好）+ 访问日志中间件 | `LOG_FORMAT=json` |
| 机密托管 | 密钥从挂载文件读取（Vault/KMS/K8s Secret）| `JWT_SECRET_FILE` / `ENCRYPTION_SECRET_FILE` |
| 统一身份 | 真实 OIDC 授权码流程 | `SSO_MODE=oidc` + 端点配置 |
| 就绪探针 | `/api/health/ready`（DB 连通 + 限流后端）| — |
| Worker 扩展 | `python -m app.worker` 独立横向扩展 | `RUN_INPROCESS_WORKER=false` |

> 未配置 `REDIS_URL`/`DATABASE_URL` 时自动用进程内限流 + SQLite，开发零依赖。
> 前置 Nginx/网关做 TLS 与统一入口；监控接 Prometheus + Grafana 抓取 `/metrics`。

---

## ✅ 测试

```powershell
pip install -r requirements-dev.txt
pytest -q
```

端到端冒烟测试（`tests/test_api.py`）覆盖：注册/登录、网页聊天与扣费、模型等级权限、
内部 API Token 全流程、无效 Token 拒绝、管理后台与 Key 加密保密性。

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
| POST | `/api/v1/jobs` | 提交批量任务（需 Token `allow_batch`） |
| POST | `/api/v1/jobs/estimate` | 批量任务费用预估 |
| GET  | `/api/v1/jobs/{id}` | 查询任务状态 |
| GET  | `/api/v1/jobs/{id}/results` | 下载任务结果 |
| POST | `/api/v1/jobs/{id}/confirm` | 确认入队（未自动确认时） |
| POST | `/api/v1/jobs/{id}/cancel` | 取消任务 |
| GET  | `/api/v1/quota/me` | 查询个人额度 |
| GET  | `/api/v1/usage/me` | 查询调用记录 |
| GET  | `/api/v1/wallet/me` | 查询点数账户 |

### 批量任务示例（方案第十四节）

```python
import httpx, time
H = {"Authorization": "Bearer sk-relay-你的Token"}
base = "http://localhost:8000"

# 1) 提交批量摘要（auto_confirm=True 直接入队）
job = httpx.post(f"{base}/api/v1/jobs", headers=H, json={
    "job_type": "batch_summary",          # summary/translate/classify/code_explain/completion
    "model_level": "basic",
    "auto_confirm": True,
    "items": [
        {"id": "paper_001", "text": "Large language models have demonstrated..."},
        {"id": "paper_002", "text": "Experimental results show..."},
    ],
}).json()

# 2) 轮询状态，完成后取结果
while True:
    st = httpx.get(f"{base}/api/v1/jobs/{job['id']}", headers=H).json()
    if st["status"] in ("completed", "failed"):
        break
    time.sleep(2)
results = httpx.get(f"{base}/api/v1/jobs/{job['id']}/results", headers=H).json()
for it in results["items"]:
    print(it["item_ref"], "->", it["output_text"])
```

> **Worker**：应用内默认随服务启动一个后台 Worker 处理队列（`RUN_INPROCESS_WORKER=true`）。
> 生产可关闭它并单独运行 `python -m app.worker` 横向扩展多个 Worker（队列可换成 RabbitMQ/Kafka/Redis Stream，接口不变）。

---

## 👥 共享账户（多人共用一个账户）

适合「一个账户多人共用、按聚合速率/并发上限约束」的产品形态：拥有者建一个共享账户拿到一个共享 Token，多名成员用同一个 Token + 各自的 `X-Member-Id` 调用。**速率不超上限时允许多人并发使用**；**每名成员只能看到自己的历史，服务端按成员区分、互不混淆**。

```python
import httpx
TOKEN = "sk-relay-共享账户Token"            # 拥有者在 /api/web/shared 创建时获得
H = lambda member: {"Authorization": f"Bearer {TOKEN}", "X-Member-Id": member}

# 成员 alice 调用（计入账户聚合速率，计费归账户拥有者）
httpx.post("http://localhost:8000/api/v1/shared/chat", headers=H("alice"),
           json={"model_level": "basic", "messages": [{"role": "user", "content": "你好"}]})

# alice 只会拉到自己的历史，看不到 bob 的对话
httpx.get("http://localhost:8000/api/v1/shared/history", headers=H("alice")).json()
```

| 端点 | 说明 |
|------|------|
| POST `/api/web/shared` | 拥有者创建共享账户（设 `rate_limit_per_minute`/`max_concurrency`/`daily_request_limit`/`restrict_members`），**返回一次性 Token** |
| GET `/api/web/shared/{id}/members` | 拥有者查看各成员用量 |
| POST `/api/v1/shared/chat` | 成员调用（`X-Member-Id` 标识成员）|
| GET `/api/v1/shared/history` | 成员拉取**自己**的历史对话（按成员隔离）|
| GET `/api/v1/shared/me` | 成员查看账户限额与自己的用量 |

- **聚合速率/并发上限**：所有成员共用账户的每分钟速率、并发与每日次数预算；未超上限放行，超过返回 429。
- **成员隔离**：每次调用按 `member_id` 落库（`shared_calls`），成员只能检索自己的记录，服务端据此区分不同对话。
- **白名单**：`restrict_members=true` 时仅允许拥有者登记的成员；否则成员首次出现即自动登记。
- 端到端演示：`python examples/shared_account_demo.py`（3 名成员并发共用、各自历史隔离）。

> 计费与额度统一计入账户拥有者钱包。注意：保存对话内容用于「历史记录」属产品功能，请在你的隐私政策中明示，并仅向对应成员开放。

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

### 关于「共享 ChatGPT Plus 账号」

**ChatGPT Plus（¥/月 网页订阅）≠ OpenAI API**，两者是不同产品：

- Plus 只授权 **个人在 ChatGPT 网页/App** 使用，**不含 API**，也没有官方接口可被程序调用。
- 用脚本/浏览器自动化去「共享」一个 Plus 账号，违反 OpenAI 服务条款（禁止账号共享与自动化访问），有**封号风险**；且与本平台方案（第三节「不允许绕过第三方平台规则」、第十九节合规、禁止「公开共享 API Token / 违反供应商规则」）相冲突——因此平台**不提供** Plus 账号代理。
- **合规且本平台原生支持的共享方式**：在 [platform.openai.com](https://platform.openai.com) 开通 **OpenAI API**（按 token 计费），生成一个 API Key，按上面的「Key 池管理」加入平台。这样**整组成员共用这一个 Key**，且每人有独立 Token、额度、计费与审计——正是中转站要解决的问题。
- 若只想要「多人用网页版、按固定月费」，对应的官方产品是 **ChatGPT Team/Enterprise**（多席位，各自登录），但那是网页端、非 API，无法接入本平台。

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
  billing.py     模型路由 / Key 调度 / 点数计费 / 课题组共享额度
  chat.py        调用编排：限流→路由→余额→调用→扣费→审计
  jobs.py        批量任务服务（创建/估算/确认/取消）
  worker.py      异步队列 Worker（应用内线程 + 独立进程）
  alerts.py      异常调用检测与自动封禁
  store.py       套餐充值/贡献账号/补偿与付费意愿统计
  org.py         多级组织（学院/专业/课题组）与汇总
  governance.py  学校级预算熔断 / 操作审计 / 统计报表
  sso.py         学校统一身份认证（mock IdP / OIDC）
  ratelimit.py   限流（进程内 + Redis 分布式 + 并发槽，故障回退）
  metrics.py     Prometheus 指标
  logging_config.py  结构化 JSON 日志
  shared.py      共享账户（多人共用 + 成员隔离）服务
  seed.py        建表 + 初始管理员 + mock 模型/Key + 默认套餐
  routers/
    auth.py      注册/登录 + SSO
    web.py       网页端（钱包/Token/用量/聊天/批量/充值/贡献/共享账户管理）
    v1.py        科研 API（聊天/批量任务，方案第十三节）
    shared.py    共享账户调用（成员侧：聊天/历史/me）
    admin.py     管理后台（用户/Key/课题组/告警/订单/补偿/组织/预算/审计/报表）
frontend/
  index.html     自包含单页前端（聊天/钱包/Token/批量/充值/贡献/文档/管理 + SSO 登录）
tests/
  test_api.py    第一阶段端到端测试
  test_phase2.py 第二阶段（批量/队列/课题组/告警）
  test_phase3.py 第三阶段（充值/贡献/补偿/备用池）
  test_phase4.py 第四阶段（SSO/组织/预算熔断/审计/报表）
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

- ~~**第一阶段**：登录、网页聊天、内部 Token、通用 API、Key 池、点数计费、日志、管理后台~~ ✅ 已完成
- ~~**第二阶段**：批量任务接口、异步队列（Worker）、课题组/项目额度、费用预估、异常告警~~ ✅ 已完成
- ~~**第三阶段**：套餐与充值订单、付费意愿统计、学生自愿购买 Token、贡献账号授权与补偿~~ ✅ 已完成
- ~~**第四阶段**：学校统一身份认证、多级（学院/专业/课题组）管理、预算熔断、大规模审计与运维~~ ✅ 已完成

四个阶段全部完成。后续为生产化加固：真实 OIDC 对接、Redis 分布式限流、PostgreSQL 主从、ELK/Loki 日志、Prometheus+Grafana 监控、Vault/KMS 密钥托管（方案第十七节）。

---

本项目为实验组内部试点，仅供学习与科研辅助使用。

# QiZhiDa Code Platform — Backend

FastAPI 后端脚手架，采用**简化分层架构（Service Layer）**。

## 分层结构

```text
HTTP 请求
   ↓
api/v1/xxx.py        接口层（Controller）—— 只做参数接收与响应转换
   ↓
services/xxx.py      服务层（Service）—— 业务规则与事务边界，核心层
   ↓
repositories/xxx.py  仓储层（Repository）—— 只做数据存取
   ↓
models/xxx.py        模型层（ORM）
   ↓
db/session.py        引擎与会话
```

横向支撑：

- `core/`：配置、异常、日志
- `schemas/`：Pydantic 请求/响应模型
- `api/deps.py`：依赖注入（DB 会话 → Service 实例）

### 几条硬约定

1. Service 不依赖 FastAPI 的 `Request` / `Response`，需要 HTTP 语义时抛 `core.exceptions.AppError` 子类，由全局异常处理器翻译成 HTTP 响应。
2. 一个 Service 方法 = 一个业务用例 = 一个事务边界（`commit()` 在 Service 里）。
3. Controller 不直接访问数据库。
4. 所有写入接口通过 `schemas/` 做入参校验，通过 `schemas/` 做响应序列化。

### 响应格式

成功：

```json
{ "code": 0, "message": "ok", "data": { "id": 1, "name": "demo" } }
```

失败（由全局异常处理器统一产出）：

```json
{
  "code": 404,
  "error_code": "NOT_FOUND",
  "message": "示例 1 不存在",
  "details": null
}
```

## 目录结构

```text
backend/
├── app/
│   ├── main.py              # 应用工厂 + 全局异常处理 + lifespan
│   ├── core/                # config / exceptions / logging / crypto（API Key 加密）
│   ├── api/
│   │   ├── deps.py          # 依赖注入
│   │   ├── router.py        # 路由聚合
│   │   └── v1/              # 版本化接口
│   │       ├── health.py
│   │       ├── example.py
│   │       ├── agent.py        # 编码助手（LangChain）
│   │       └── llm_provider.py # 模型接入配置（供应商 CRUD）
│   ├── agents/              # Agent 层：模型 + 工具 + 循环
│   │   ├── llm.py           # 模型工厂（环境变量 / 数据库供应商配置两种来源）
│   │   ├── providers.py     # 供应商预设表（兼容主流大模型）
│   │   ├── prompts.py       # 系统提示词
│   │   ├── builder.py       # create_agent 组装
│   │   ├── runner.py        # 执行器（同步一轮 / 流式）
│   │   ├── checkpointer.py  # 多轮对话上下文持久化
│   │   └── tools/           # 工具集（文件读写 / 代码执行）
│   ├── skills/              # 技能能力层：Tool / MCP / Skill 的执行
│   │   ├── builtin/         # 内置工具白名单（read_file / write_file / run_python…）
│   │   ├── executor.py      # 数据库定义 → 可执行 LangChain 工具
│   │   ├── mcp_client.py    # MCP 连接与工具拉取
│   │   ├── context.py       # 内置工具的运行时上下文
│   │   └── prompt.py        # 技能提示词拼装
│   ├── schemas/             # 请求 / 响应模型
│   ├── services/            # 业务层
│   ├── repositories/        # 数据访问层
│   ├── models/              # ORM 模型
│   └── db/                  # 引擎与会话
├── scripts/
│   ├── check_syntax.py      # 不装依赖也能跑的语法自检
│   └── gen_encryption_key.py # 生成 API Key 加密主密钥
├── tests/
├── .env.example
├── pyproject.toml
└── requirements.txt
```

## 编码助手（LangChain / LangGraph）

基于 **LangChain v1** 的 `create_agent`（内部是一张 LangGraph 图）：模型思考 → 调工具 → 结果回灌 → 再思考。

```text
POST /api/v1/agent/chat     跑完一轮再整体返回
POST /api/v1/agent/stream   SSE 流式推送
```

配置（`.env`）：

```bash
LLM_PROVIDER="openai"        # openai | anthropic | ollama
LLM_MODEL="gpt-4o-mini"
LLM_API_KEY="sk-..."
LLM_BASE_URL=""              # 国产模型走这个：https://api.deepseek.com/v1
SANDBOX_TYPE="docker"        # local（开发）| docker（生产）
WORKSPACE_ROOT="./workspace"
```

### ⚠️ 沙箱是这个平台的安全底线

agent 会执行 **LLM 生成的代码**，而 LLM 可以被提示词注入诱导。所以：

- `SANDBOX_TYPE=local` **没有任何隔离** —— 生成的代码能读写本机任意文件、能联网。仅限本地开发。
- `SANDBOX_TYPE=docker` 才是生产可用形态：容器内执行、`--network none` 断网、限内存与进程数、`--cap-drop ALL`、只挂载当前会话的工作区。
- 所有文件路径都经 `agents/tools/paths.py` 校验：`..` 穿越、绝对路径、指向工作区外的符号链接一律拒绝。
- 每个会话一个独立工作区（`WORKSPACE_ROOT/<thread_id>`），互不串味。

> **macOS 注意**：Docker Desktop 默认只把 `/Users` 共享给虚拟机。工作区放在 `/tmp` 或
> `/var/folders` 下时，容器会挂到一个空目录，表现为"脚本文件不存在"。把
> `WORKSPACE_ROOT` 设在 `/Users` 下，或在 Docker Desktop → Settings → Resources →
> File Sharing 里共享该目录。检测到这种情况时接口会直接返回提示，不会让 agent 去猜。

## 模型接入配置（供应商管理）

平台正式接模型走这套接口，`.env` 里的 `LLM_*` 只作本地兜底。

```text
GET    /api/v1/llm-providers/presets   支持的供应商预设（前端下拉用）
GET    /api/v1/llm-providers            分页查询
POST   /api/v1/llm-providers            新增
GET    /api/v1/llm-providers/{id}       详情
PUT    /api/v1/llm-providers/{id}       更新
DELETE /api/v1/llm-providers/{id}       删除
POST   /api/v1/llm-providers/{id}/test  连通性测试（真实发一次最小请求）
```

对话时用 `provider_id` 指定模型；不传则用标记为「默认」的那条，都没有则读 `.env`。

### 兼容哪些模型

`app/agents/providers.py` 里维护供应商预设。绝大多数厂商都提供 OpenAI 兼容端点，
所以底层统一走 `langchain-openai` + 自定义 base_url，不需要为每家装一个集成包：

| 预设 | 底层 | 默认地址 |
| --- | --- | --- |
| openai | openai | <https://api.openai.com/v1> |
| anthropic | anthropic | <https://api.anthropic.com> |
| deepseek | openai | <https://api.deepseek.com/v1> |
| moonshot（Kimi） | openai | <https://api.moonshot.cn/v1> |
| qwen（百炼） | openai | <https://dashscope.aliyuncs.com/compatible-mode/v1> |
| zhipu（智谱） | openai | <https://open.bigmodel.cn/api/paas/v4> |
| doubao（火山方舟） | openai | <https://ark.cn-beijing.volces.com/api/v3> |
| siliconflow | openai | <https://api.siliconflow.cn/v1> |
| openrouter / groq / mistral / together / xai / gemini | openai | 见代码 |
| azure_openai | azure_openai | 需自填 endpoint |
| ollama / vllm | openai | 本地地址，无需 API Key |
| custom | openai | 自填 |

### API Key 怎么存的

- 数据库里**只存密文**（Fernet，AES-128-CBC + HMAC-SHA256），主密钥来自环境变量
  `API_KEY_ENCRYPTION_KEY`，不进数据库。
- 另外存一份脱敏串（`sk-t***cdef`），所以**列表接口不需要解密、不需要主密钥也能展示**。
- 明文只在真正调模型前解密，绝不进响应体、日志。
- 主密钥生成：`python scripts/gen_encryption_key.py`。**换密钥会让已存的 key 解不开，只能重录。**

## 技能模块（Tool / MCP / Skill）

三类实体，各自独立管理，最终组合成 agent 能用的能力包。

```text
Skill（技能）= 一组 Tool + 一段提示词
Tool（工具）  = builtin（内置函数）| http（外部接口）| mcp（来自 MCP server）
MCP Server    = stdio（本地命令）| sse | streamable_http
```

### 接口

```text
GET    /api/v1/tools/builtins        内置工具白名单
GET    /api/v1/tools                 分页查询工具
POST   /api/v1/tools                 新增工具
GET    /api/v1/tools/{id}            详情
PUT    /api/v1/tools/{id}            更新
DELETE /api/v1/tools/{id}            删除（同时解除技能引用）
POST   /api/v1/tools/{id}/test       测试工具配置能否被构造出来

GET    /api/v1/mcp-servers           查询 MCP 服务器
POST   /api/v1/mcp-servers           新增
GET    /api/v1/mcp-servers/{id}      详情
PUT    /api/v1/mcp-servers/{id}      更新
DELETE /api/v1/mcp-servers/{id}      删除（有工具引用时拒绝）
GET    /api/v1/mcp-servers/{id}/tools  探测该服务器上可用的工具

GET    /api/v1/skills                分页查询技能
POST   /api/v1/skills                新增
GET    /api/v1/skills/{id}           详情
PUT    /api/v1/skills/{id}           更新
DELETE /api/v1/skills/{id}           删除
```

对话时用 `skill_ids` 挂载技能，技能里的工具与提示词会加到 agent 上：

```json
POST /api/v1/agent/chat
{ "message": "帮我审查这段代码", "skill_ids": [1], "provider_id": 2 }
```

### ⚠️ 内置工具是白名单，不是任意入口点

`builtin` 类型的工具只能引用 `app/skills/builtin/__init__.py` 里登记过的
`模块:工厂函数`。**如果允许任意 `模块:函数`，任何能调工具管理接口的人都能让平台
import 并执行任意 Python 代码** —— 等价于远程代码执行。所以越界的 source 会在
schema 校验层直接 422。

新增内置工具的步骤：在 `builtin/__init__.py` 登记 → 写一个
`factory(context) -> Callable` 的工厂函数（`context` 由运行时注入，不会进 LLM 的
tool schema）→ `GET /tools/builtins` 就能看到它。

### 工具的三种来源怎么用

| 类型 | source | config | parameters |
| --- | --- | --- | --- |
| builtin | 白名单里的入口点 | 额外参数 | 自动推断 |
| http | 留空 | `method` / `url` / `headers` / `body` | **必填**，JSON Schema |
| mcp | MCP 服务器名 | `tool_name` | 自动取自 MCP |

http 类型的 body 模板里可以用 `{占位符}` 引用模型给的参数；GET 类请求的参数进
query string，其余进 JSON body。

## 启动

需要 Python 3.11+（FastAPI 0.16x / SQLAlchemy 2.1 要求 3.11 以上）。
仓库根目录已有 `.venv`（Python 3.13，依赖已装好），直接用它即可：

```bash
cd backend
source ../.venv/bin/activate
cp .env.example .env

# 生成 API Key 加密主密钥，填进 .env 的 API_KEY_ENCRYPTION_KEY
python scripts/gen_encryption_key.py

uvicorn app.main:app --reload
# 或 python3 -m app.main
```

自己新建环境的话：

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

想让 `app` 包在仓库根目录也能直接导入（不必 `cd backend`），把 backend 以可编辑模式装进环境：

```bash
.venv/bin/pip install -e ./backend
```

- Swagger：<http://localhost:8000/docs>
- 健康检查：<http://localhost:8000/api/v1/health>

## 测试

```bash
cd backend
source ../.venv/bin/activate
pytest -q          # 44 passed
```

只想快速检查语法（不装依赖也能跑）：

```bash
python3 scripts/check_syntax.py
```

## 新增一个业务模块的顺序

1. `models/` 加 ORM 模型
2. `schemas/` 加请求/响应模型
3. `repositories/` 加仓储（继承 `BaseRepository`）
4. `services/` 加服务（继承 `BaseService`），写业务规则
5. `api/v1/` 加接口，依赖用 `app/api/deps.py` 里的 `Depends`
6. 在 `app/api/router.py` 挂载路由

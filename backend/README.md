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
│   │   └── checkpointer.py  # 多轮对话上下文持久化
│   ├── capabilities/         # 能力层：tool / mcp / skill 的统一体系
│   │   ├── builtin/          # 内置能力出厂源（白名单 + 出厂规格）
│   │   │   ├── __init__.py   # 出厂规格清单（白名单）
│   │   │   ├── paths.py      # 工作区路径校验（防穿越）
│   │   │   ├── fs.py         # 文件类能力（读 / 写 / 改 / 列 / glob / grep）
│   │   │   ├── sandbox.py    # 执行类能力（Python / shell，沙箱隔离）
│   │   │   └── web.py        # 网络类能力（抓取 / 搜索）
│   │   ├── registry.py       # 统一能力清单的唯一读取入口
│   │   ├── entry.py          # 清单条目模型
│   │   ├── adapter.py        # 执行适配器（builtin / http / mcp）
│   │   ├── result.py         # 执行结果与失败分级
│   │   ├── orchestration.py   # 编排层：检索 / 参数校验 / middleware
│   │   ├── prompt.py         # 提示词组装器
│   │   ├── context.py        # 执行上下文（工作区、沙箱、MCP 通道）
│   │   └── mcp_client.py     # MCP 连接与能力拉取
│   ├── schemas/             # 请求 / 响应模型
│   ├── services/            # 业务层
│   ├── repositories/        # 数据访问层
│   ├── models/              # ORM 模型
│   └── db/                  # 引擎与会话
├── alembic/                # 数据库迁移（versions/ 里是版本脚本）
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

WEB_SEARCH_PROVIDER=""       # tavily | serper | bing（联网搜索用）
WEB_SEARCH_API_KEY=""        # 对应服务商的 API Key
DB_AUTO_CREATE_TABLES=false  # 用了 Alembic 就必须关掉
```

### ⚠️ 沙箱是这个平台的安全底线

agent 会执行 **LLM 生成的代码**，而 LLM 可以被提示词注入诱导。所以：

- `SANDBOX_TYPE=local` **没有任何隔离** —— 生成的代码能读写本机任意文件、能联网。仅限本地开发。
- `SANDBOX_TYPE=docker` 才是生产可用形态：容器内执行、`--network none` 断网、限内存与进程数、`--cap-drop ALL`、只挂载当前会话的工作区。
- 所有文件路径都经 `capabilities/builtin/paths.py` 校验：`..` 穿越、绝对路径、指向工作区外的符号链接一律拒绝。
- 每个会话一个独立工作区（`WORKSPACE_ROOT/<thread_id>`），互不串味。

> **macOS 注意**：容器只能看到宿主共享出去的路径。Docker Desktop 默认共享 `/Users`，
> colima 默认共享 `$HOME`。工作区放在 `/tmp` 或 `/var/folders` 下时，容器会挂到一个
> 空目录，表现为"脚本文件不存在"。把 `WORKSPACE_ROOT` 设在 `$HOME` 下，或在
> Docker Desktop → Settings → Resources → File Sharing 里共享该目录。
>
> 另外，用 colima 时 docker 的 socket 不在 `/var/run/docker.sock`，
> 沙箱会自己向 docker CLI 问出当前 context 的 endpoint 并以 `DOCKER_HOST`
> 传给容器进程（子进程的 `HOME` 被换成了工作区，读不到 `~/.docker/config.json`）。

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

## 能力层（注册 / 编排 / 执行）

能力层把 tool / mcp / skill 收成一套统一体系：

```text
        skill（能力组合）= 知识 + 一组原子能力
              │ 引用
              ▼
        tool（原子能力）  ── 执行方式 ──▶ builtin / http / mcp
                                        （mcp 的供给通道是一张独立的表）
```

- **tool** 是原子能力，最小可执行单元；
- **mcp** 是供给通道，一个外部 server 提供的一组能力；
- **skill** 是能力组合，**选择**的粒度是它，**执行**的粒度是 tool。

### 三层怎么分工

| 层 | 职责 | 关键约束 |
| --- | --- | --- |
| **注册层** `app/capabilities/` | 声明、编目、安装 —— 产出**统一能力清单** | **唯一入口**：agent 只从注册表拿能力 |
| **编排层** `app/capabilities/orchestration.py` | 检索技能 → 收敛工具 → 校验参数 → 组装提示词 | 选择粒度是 skill，暴露粒度是 tool |
| **执行层** `app/capabilities/adapter.py` | 受控执行 → 结构化结果 → 失败分级 | 不做决策 |

### 内置能力是"出厂源"，不是运行时直连

`app/capabilities/builtin/` 是白名单，启动时**同步进数据库**（`origin=builtin`）。
之后数据库就是唯一事实来源：运营可以改描述、调参数、单独禁用某个内置能力；
同名冲突会**显式报错**，而不是像以前那样被 LangGraph 静默覆盖。

同步只补空字段 —— 运营改过的内容不会被启动流程冲掉。

### 内置能力清单

内置能力对标 Claude Code 的内置工具，全部限制在会话工作区内：

| key | 名称 | 参数 | 权限 | 说明 |
| --- | --- | --- | --- | --- |
| `read_file` | 读取文件 | `path` `offset` `limit` | low | 读文本文件，带行号；offset/limit 用于分段读大文件 |
| `write_file` | 写入文件 | `path` `content` | high | 写入（覆盖），自动建父目录 |
| `edit_file` | 编辑文件 | `path` `old_string` `new_string` `replace_all` | high | 精确字符串替换；**原文必须唯一**，不唯一就报错并列出所有匹配行号 |
| `list_files` | 列出文件 | `path` | low | 列目录下的文件与子目录 |
| `glob` | 按模式找文件 | `pattern` `path` | low | 按 glob 模式找文件，如 `**/*.py` |
| `grep` | 搜索文件内容 | `pattern` `path` `glob_pattern` | low | 按正则搜内容，返回 `文件:行号:内容` |
| `run_python` | 执行 Python | `code` | high | 在沙箱里跑一段 Python，返回退出码 / stdout / stderr |
| `run_bash` | 执行命令 | `command` | high | 在沙箱里跑一条 shell 命令 |
| `web_fetch` | 抓取网页 | `url` | high | 抓 http/https 页面并转纯文本（去脚本、去样式） |
| `web_search` | 联网搜索 | `query` `max_results` | high | 走 Tavily / Serper / Bing 搜索，需配 API Key |

几个设计取舍：

- **文件类只认相对路径**。绝对路径、`..` 穿越、指向工作区外的符号链接一律拒绝
  （`builtin/paths.py`）。
- **写与改是"高权限"能力**，写入类操作在 `tool.permission` 上标成 `high`，
  便于后面接权限确认。
- **执行类一律进沙箱**，且命令写进脚本文件再执行，不做 `sh -c "用户输入"` 拼接。
- **联网搜索没配 Key 就明说**，返回"请设置 `WEB_SEARCH_PROVIDER` 和
  `WEB_SEARCH_API_KEY`"，而不是静默失败。

### 接口

```text
GET    /api/v1/capabilities             统一能力清单（跨三类成员，可按 kind/tag 过滤）
GET    /api/v1/capabilities/{key}        按 key 取任意成员
POST   /api/v1/capabilities/{key}/invoke  直接调一次能力（调试用，返回结构化结果）

GET    /api/v1/tools/builtins           内置能力白名单
GET    /api/v1/tools                    分页查询工具
POST   /api/v1/tools                    新增工具
GET    /api/v1/tools/{id}               详情
PUT    /api/v1/tools/{id}               更新
DELETE /api/v1/tools/{id}               删除（同时解除技能引用）
POST   /api/v1/tools/{id}/test          测试工具配置能否被构造出来

GET    /api/v1/mcp-servers              查询 MCP 服务器
POST   /api/v1/mcp-servers              新增
GET    /api/v1/mcp-servers/{id}         详情
PUT    /api/v1/mcp-servers/{id}         更新
DELETE /api/v1/mcp-servers/{id}         删除（有工具引用时拒绝）
GET    /api/v1/mcp-servers/{id}/tools   探测该服务器上可用的工具

GET    /api/v1/skills                   分页查询技能
POST   /api/v1/skills                   新增
GET    /api/v1/skills/{id}              详情
PUT    /api/v1/skills/{id}              更新
DELETE /api/v1/skills/{id}              删除
GET    /api/v1/skills/{id}/preview      预览挂载后带来哪些能力、什么提示词
```

对话时用 `skill_ids` 挂载技能；不传则按用户输入自动检索相关技能：

```json
POST /api/v1/agent/chat
{ "message": "帮我审查这段代码", "skill_ids": [1], "provider_id": 2 }
```

### ⚠️ 内置能力是白名单，不是任意入口点

`builtin` 类型的工具只能引用 `app/capabilities/builtin/__init__.py` 里登记过的
`模块:工厂函数`。**如果允许任意 `模块:函数`，任何能调工具管理接口的人都能让平台
import 并执行任意 Python 代码** —— 等价于远程代码执行。所以越界的 source 会在
schema 校验层直接 422。

新增内置能力的步骤：在 `builtin/__init__.py` 登记出厂规格 → 写一个
`factory(context: ExecutionContext) -> Callable` 的工厂函数（`context` 由运行时
注入，不会进 LLM 的 tool schema）→ 重启即自动安装。

### 工具的三种来源怎么用

| 类型 | source | config | parameters |
| --- | --- | --- | --- |
| builtin | 白名单里的入口点 | 额外参数 | 自动推断 |
| http | 留空 | `method` / `url` / `headers` / `body` | **必填**，JSON Schema |
| mcp | MCP 服务器名 | `tool_name` | 自动取自 MCP |

http 类型的 body 模板里可以用 `{占位符}` 引用模型给的参数；GET 类请求的参数进
query string，其余进 JSON body。

### 失败分级

执行层不返回裸字符串，而是结构化结果，失败按类型分级 —— 模型据此判断
"改参数重试"还是"换条路"：

| error code | retryable | 含义 |
| --- | --- | --- |
| `CAPABILITY_NOT_FOUND` | false | 能力不存在 |
| `INVALID_ARGUMENTS` | true | 参数不合法，修正后可重试 |
| `EXECUTION_ERROR` | false | 代码本身错了 |
| `SANDBOX_TIMEOUT` | true | 超时，缩小范围重试 |
| `NETWORK_ERROR` | true | 外部服务问题 |
| `PERMISSION_DENIED` | false | 越权，需人工介入 |

参数校验发生在**进执行层之前**（编排层的 middleware），所以模型拿到的是
`[INVALID_ARGUMENTS] 缺少必填参数：path（可以修正参数后重试）`，
而不是一轮白跑的 traceback。

## 数据库迁移（Alembic）

迁移脚本在 `alembic/versions/`，连接串从应用配置读（不进版本库）。

```bash
alembic upgrade head              # 应用所有迁移
alembic downgrade -1              # 回退一版
alembic downgrade base            # 回到空库
alembic revision --autogenerate -m "描述"   # 模型改了之后生成迁移
alembic current / history / show  # 看状态
```

迁移用**异步引擎**跑（项目只装了 asyncpg，没有 psycopg2）；SQLite 走同步引擎。

### 存量库怎么升级

现在有两版迁移：

| 版本 | 内容 |
| --- | --- |
| `0001_baseline` | 脚手架初期的表结构（引入 Alembic 之前的快照） |
| `0002_capability_layer` | 能力层重构：skill.prompt → knowledge、新增若干列、新建 execution_log |

按你手上数据库的状态选一条：

```bash
# A. 全新库
alembic upgrade head

# B. 跑过旧版本、想保住数据（schema 还是 0001 那个样子）
alembic stamp 0001_baseline
alembic upgrade head

# C. 已经手动改过库、表结构已经是新的（跳过后续迁移避免重复执行）
alembic stamp head
```

B 路径下 `0002` 会把 `skill.prompt` 改名成 `knowledge`、给 `tool` / `skill` /
`mcp_server` 补上新列并回填默认值、创建 `execution_log` —— **已有数据不丢**。

### 和自动建表的关系

开发期 `init_db()` 会用 `create_all` 建表。但 `create_all` **不会给已存在的表加列**，
所以一旦开始用 Alembic，就要把它关掉，否则两者会打架：

```bash
# .env
DB_AUTO_CREATE_TABLES=false
```

然后启动前先 `alembic upgrade head`。

> 忘了关会怎样：`create_all` 建的表结构是"当前模型的最新样子"，而 Alembic 认为
> 数据库停在旧版本，`upgrade` 时会因为"表已存在"而报错。看到这个错误就
> `alembic stamp head` 再重试。

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
pytest -q          # 105 passed
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

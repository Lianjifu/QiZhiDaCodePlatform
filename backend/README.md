# QiZhiDa Code Platform — Backend

FastAPI 后端脚手架，采用**简化分层架构（Service Layer）**。

前端控制台在同级的 [`../frontend/`](../frontend)，后端起法见下面的「启动」——
**必须在 `backend/` 目录起**，`.env` 在这儿。

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
│   ├── core/                # config / exceptions / logging / crypto / workspace（工作区路径）
│   ├── api/
│   │   ├── deps.py          # 依赖注入
│   │   ├── router.py        # 路由聚合
│   │   └── v1/              # 版本化接口
│   │       ├── health.py
│   │       ├── example.py
│   │       ├── agent.py        # 编码助手（deepAgents + 审批）
│   │       ├── files.py        # 文件（上传 / 列表 / 下载 / 删除）
│   │       ├── environments.py # 工作目录（仓库 / worktree / 绑定）
│   │       └── llm_provider.py # 模型接入配置（供应商 CRUD）
│   ├── agents/              # Agent 层：模型 + 工具 + 循环
│   │   ├── llm.py           # 模型工厂（环境变量 / 数据库供应商配置两种来源）
│   │   ├── providers.py     # 供应商预设表（兼容主流大模型）
│   │   ├── prompts.py       # 系统提示词
│   │   ├── builder.py       # create_deep_agent 组装（含子代理规格）
│   │   ├── runner.py        # 执行器（同步一轮 / 流式）
│   │   ├── worker.py        # 子 agent（在自己的 worktree 里干活，免审批）
│   │   └── checkpointer.py  # 多轮对话上下文持久化
│   ├── environments/        # 工作目录（环境）：白名单、绑定、worktree
│   │   ├── paths.py         # 路径规则（谁能当工作目录 / worktree 落哪）
│   │   ├── store.py         # 会话 ↔ 环境的绑定（文件，跨进程可见）
│   │   └── service.py       # 环境的发现、绑定、worktree 的建与删
│   ├── git/                 # 宿主侧的 git 操作（worktree / 提交 / 合并）
│   ├── capabilities/         # 能力层：tool / mcp / skill 的统一体系
│   │   ├── errors.py          # 错误码与 CapabilityError（三层共用）
│   │   ├── prompt.py          # 提示词组装器
│   │   ├── mcp_client.py      # MCP 连接与能力拉取
│   │   ├── skills_export.py   # 数据库技能 → SKILL.md（deepAgents 渐进披露）
│   │   ├── backends/          # deepAgents 的 backend
│   │   │   ├── assembly.py    # CompositeBackend 组装（文件本地 / 命令进沙箱）
│   │   │   └── sandbox_backend.py # docker 沙箱（BaseSandbox 子类）
│   │   ├── registration/      # 注册层：声明、编目、安装
│   │   │   ├── spec.py        # 能力声明（契约 + 策略）
│   │   │   ├── policy.py      # 风险等级 / 权限要求 / 重试策略 / 工具注解
│   │   │   ├── validator.py   # JSON Schema 校验（意图层与执行层共用）
│   │   │   ├── args_model.py  # JSON Schema → 给模型看的参数模型
│   │   │   ├── registry.py    # 统一能力清单的唯一读取入口
│   │   │   ├── tool_policy.py # 文件工具的治理策略映射表（deepAgents 的工具）
│   │   │   └── builtin/       # 内置能力出厂源（白名单 + 出厂规格）
│   │   │       ├── __init__.py# 出厂规格清单（白名单）
│   │   │       ├── sandbox.py # 执行类能力（run_python，沙箱隔离）
│   │   │       ├── web.py     # 网络类能力（抓取 / 搜索）
│   │   │       ├── git.py     # git 能力（状态 / worktree / 提交 / 合并）
│   │   │       ├── delegate.py# 派子 agent（多智能体协作）
│   │   │       └── memory.py  # 记忆类能力（召回 / 读取 / 写入）
│   │   ├── intent/            # 意图层：模型的一次 tool call → 一条意图
│   │   │   ├── intent.py      # CapabilityIntent（与执行层的唯一契约）
│   │   │   ├── resolver.py    # tool call → 意图（含前置参数校验）
│   │   │   └── middleware.py  # 治理中间件（工具收敛 / 参数级风险 / 留痕）
│   │   ├── backends/          # deepAgents 后端：文件从哪来、命令在哪跑
│   │   │   ├── assembly.py    # 后端组装（路由表）
│   │   │   ├── platform_backend.py # 包装层：ls("/") 补虚拟目录项
│   │   │   ├── sandbox_backend.py  # DockerSandbox（只提供 execute）
│   │   │   └── container_pool.py    # 容器生命周期（复用 / 回收 / 容量上限）
│   │   └── execution/         # 执行层：七阶段流水线
│   │       ├── pipeline.py    # 校验→授权→风险→审批→调用→验证→回写
│   │       ├── adapters.py    # 执行适配器（builtin / http / mcp）
│   │       ├── tooling.py     # 能力声明 → 模型可调用的工具
│   │       ├── risk.py        # 参数级风险评估（SSRF / 破坏性命令）
│   │       ├── redaction.py   # 结果脱敏
│   │       ├── result.py      # 执行结果
│   │       └── context.py     # 执行上下文（工作区、沙箱、MCP 通道）
│   ├── schemas/             # 请求 / 响应模型
│   ├── files/               # 文件模块：上传 / 列表 / 下载 / 删除 + 图片内联
│   │   ├── storage.py       # 存储布局：uploads 目录、文件名清洗
│   │   ├── service.py       # 文件服务（路径安全交给 FilesystemBackend）
│   │   └── attachments.py   # 附件处理：图片内联 + 上传件清单
│   ├── context/             # 上下文模块（装配 / 配额 / 压缩）
│   │   ├── blocks.py        # ContextBlock 抽象（标题、优先级、溢出策略）
│   │   ├── budget.py        # 预算计算（按模型窗口的比例）
│   │   ├── assembler.py     # 装箱：按优先级与配额拼成提示词
│   │   ├── compaction.py    # 接管 deepAgents 的摘要中间件
│   │   ├── skills_quota.py  # 技能清单配额
│   │   └── service.py       # 对外入口：collect + assemble
│   ├── memory/              # 记忆模块（短期落表 / 长期落 RAG）
│   │   ├── models.py        # MemoryEntry、scope / kind、评分口径
│   │   ├── store.py         # MemoryStore 协议（两种存储的统一接口）
│   │   ├── session_store.py # 短期：memory_item 表（关键词检索）
│   │   ├── long_term_store.py # 长期：RAG 集合（混合检索）
│   │   ├── catalog.py       # 目录组装 + 预算装箱 + MMR 去冗余
│   │   └── service.py       # 召回 / 读取 / 写入校验
│   ├── rag/                 # RAG 服务层（通用：语料 → 分块 → 向量 → 检索）
│   │   ├── models.py        # 集合 / 文档 / 分块
│   │   ├── chunking.py      # 分块策略（Markdown / 代码 / 纯文本）
│   │   ├── embedding.py     # 向量化（复用模型接入配置）
│   │   ├── vector_store.py  # 存储抽象
│   │   ├── stores/sql.py    # SQL 实现（SQLite / Postgres 同一套）
│   │   ├── retriever.py     # 混合检索（向量 + 关键词，RRF 融合）
│   │   └── service.py       # 业务：入库 / 检索
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

## 编码助手（deepAgents / LangGraph）

基于 **deepAgents** 的 `create_deep_agent`（内部是一张 LangGraph 图）：模型思考 → 调工具 → 结果回灌 → 再思考。

deepAgents 额外带来规划（`write_todos`）、子代理（`task`）、技能（`SKILL.md`）、
记忆（`AGENTS.md`）、上下文摘要与 prompt caching。

```text
POST /api/v1/agent/chat             跑完一轮再整体返回
POST /api/v1/agent/stream           SSE 流式推送（一帧一种事件）
GET  /api/v1/agent/{thread_id}/history   读会话历史
GET  /api/v1/agent/{thread_id}/status    会话状态（环境/上下文/产出/改动量）
GET  /api/v1/agent/commands         斜杠命令清单（输入框敲 `/` 弹的那份）
DELETE /api/v1/agent/{thread_id}    清空会话的对话记录（工作区文件不删）
POST /api/v1/agent/{thread_id}/regenerate  重新生成上一轮（SSE，帧格式同 /stream）
POST /api/v1/agent/{thread_id}/fork        从某条消息分支出新会话
```

### 重新生成 与 分支

两个能力都作用在 **checkpoint 的历史**上，只用 LangGraph 的公开接口
（`update_state` + `RemoveMessage`），**不碰 checkpoint 表里的 blob**：

- **重新生成**（`app/agents/runner.py` 的 `regenerate()`）：从最后一条用户消息
  起（含它）往后全删，再用那条用户消息重跑一遍。帧格式与 `/stream` 完全一致。
- **分支**（`fork()`）：新会话用 `update_state` 播种 —— 写入切到分支点为止的
  消息，**不跑模型**；工作区文件也复制过去（只带对话不带文件的话，新会话是
  空的）。分支点靠**消息 id** 定位，历史接口现在会带 `message.id`。

> **这两个接口也没有鉴权** —— 知道 `thread_id` 就能改别人会话的历史、重跑别人的
> 对话。跟文件接口一样，上线前必须补鉴权。

流式接口的帧有六种类型（`type` 字段决定）：`text`（文本增量）、
`tool_call`（模型决定调工具）、`tool_result`（工具跑完）、
`pending_approval`（这一轮停在审批上）、`error`（这一轮炸了，附原因）、
`done`（收尾）。帧尾是 `data: [DONE]`。

**`error` 帧是必须的**：SSE 一旦开始推数据就改不了 HTTP 状态码了，错误只能
走帧。少推这一帧，前端就永远停在「思考中…」—— 它只认得 `done` 收尾。

### 改动量统计（`+N −M`）

工具块上那个「+12 −3」是**执行前后各读一次文件**算出来的 —— deepAgents 的
`write_file` / `edit_file` 自己不返回 diff，成功时只说"Updated file xxx"。

统计落在治理中间件（`app/capabilities/intent/middleware.py`）：执行前后读
`file_path` 的内容，用 `difflib` 按行比。统计结果挂在工具消息的
`additional_kwargs["diff"]`，随事件流推给前端，也写进调用记录 —— 会话级的
「本次改了多少行」从调用记录汇总。

### ⚠️ 工具是并行的，数据库不是

**模型一次回复里可以带多个工具调用**，LangGraph 会并发执行它们。但
SQLAlchemy 的 `AsyncSession` 不允许并发使用 —— 两个调用同时 `flush` 会炸：

```text
sqlalchemy.exc.InvalidRequestError: Session is already flushing
```

这一炸整轮对话就没了。所以数据库那一段必须串行：锁挂在 `ExecutionContext`
上（`app/capabilities/execution/context.py`），用 `async with context.db():`
包住查、写、flush。**工具本身的执行不锁** —— 沙箱里跑命令那几十秒不能被
锁住，不然并行就白并行了。

锁是**可重入**的（`app/core/locking.py`）：记忆的写入会层层调用
（写 → 取集合 id → 写），不可重入的锁在那里会死锁。

回归测试在 `tests/test_parallel_tools.py`。

### 调用记录要当场提交

每条调用记录**自己提交**，不等整轮结束 —— 用户点「停止」时这一轮会被取消，
整轮末尾的提交不会执行。不当场提交的话，文件已经改了、记录却查不到。

### 并发闸门与审批过期

两个以前**只在配置里、代码里没人读**的项，现在都接上了：

- `capability_max_concurrency`（默认 4）—— 会话级并发闸门
  （`app/capabilities/execution/concurrency.py`）。模型一次回复里的多个工具
  调用是并行跑的，没有闸门就是一次回复起 N 个沙箱。拿不到名额排队等，
  等不到按"执行层繁忙"失败。**进程内计数** —— 多副本部署不是全局配额。
- `capability_approval_timeout`（默认 300s）—— 后台任务把没人处理的 pending
  审批记录置为 `expired`。以前 `STATUS_EXPIRED` 定义了但没人用，记录就一直挂着。

配置（`.env`）：

```bash
LLM_PROVIDER="openai"        # openai | anthropic | ollama
LLM_MODEL="gpt-4o-mini"
LLM_API_KEY="sk-..."
LLM_BASE_URL=""              # 国产模型走这个：https://api.deepseek.com
SANDBOX_TYPE="docker"        # docker（默认，有隔离）| local（开发用，无隔离）
WORKSPACE_ROOT="./workspace"

WEB_SEARCH_PROVIDER=""       # tavily | serper | bing（联网搜索用）
WEB_SEARCH_API_KEY=""        # 对应服务商的 API Key
DB_AUTO_CREATE_TABLES=false  # 用了 Alembic 就必须关掉

CAPABILITY_TRUST_MODE="balanced"   # strict | balanced | permissive
CAPABILITY_APPROVAL_TIMEOUT=300    # 审批请求超时自动拒绝（秒）
```

### ⚠️ 沙箱是这个平台的安全底线

agent 会执行 **LLM 生成的代码**，而 LLM 可以被提示词注入诱导。所以：

- 默认就是 `SANDBOX_TYPE=docker`：容器内执行、`--network none` 断网、限内存与进程数、`--cap-drop ALL`、只挂载当前会话的工作区。
- `SANDBOX_TYPE=local` **没有任何隔离** —— 生成的代码能读写本机任意文件、能联网。仅限本地开发，而且要**显式**打开。
- 这条规则**一处**说了算：`execute`（deepAgents 的命令工具）与 `run_python`（我们的执行能力）都看它。两边各写一套就会跑偏。
- 文件路径由 deepAgents 的 `FilesystemBackend`（虚拟模式）把关：`..` 穿越一律拒绝，绝对路径映射进工作区（`/etc/passwd` → `<工作区>/etc/passwd`），resolve 后必须仍在工作区内。
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
| deepseek | openai | <https://api.deepseek.com> |
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

## 后端存储（deepAgents backends）

后端决定**文件从哪来、命令在哪跑**。deepAgents 提供六种 backend，我们的选型：

| backend | 存哪 | 跨会话 | execute | 我们的判断 |
| --- | --- | --- | --- | --- |
| `FilesystemBackend` | 宿主文件系统 | ✅ | ❌ | ✅ **主力**（文件操作） |
| `DockerSandbox`（我们的） | 工作区挂载进容器 | ✅ | ✅ | ✅ **命令执行** |
| `LocalShellBackend` | 宿主 + subprocess | ✅ | ✅ | ⚠️ 仅本地开发（无隔离） |
| `StateBackend` | LangGraph state | ❌ | ❌ | ❌ **不用**（文件进 checkpoint，体积爆炸） |
| `StoreBackend` | LangGraph BaseStore | ✅ | ❌ | ⏸ 暂不上（与记忆模块职责重叠） |
| `ContextHubBackend` | LangSmith Hub | ✅ | ❌ | ❌ 不用（依赖 LangSmith） |

### 路由结构

```text
CompositeBackend(
    default=DockerSandbox(...),                    # 只提供 execute
    routes={
        "/": FilesystemBackend(工作区),              # 文件操作走宿主（快）
        "/skills/": FilesystemBackend(技能目录),      # 工作区外，agent 改不到
        "/.artifacts/": FilesystemBackend(产物目录),  # 对话历史外置，不给 agent 看
    },
)
```

外面包一层 `PlatformBackend` —— 补 `ls("/")` 看不到子路由的问题（`CompositeBackend`
的 route `/` 命中后走单 backend 分支，而 `glob`/`grep` 会搜所有 backend，三种操作
三种可见性）。

### 沙箱容器按会话复用

容器**常驻**，按会话命名。收益是**装好的依赖、生成的文件跨轮次保留** ——
agent 装完 pnpm，下一轮还能用。

```text
第一次: export FOO=hello; cd /tmp; pwd  ->  /tmp  FOO=hello
第二次: echo FOO=$FOO; pwd              ->  FOO=（空）  /workspace
```

> ⚠️ **`export` 和 `cd` 不跨命令** —— `docker exec` 每次都是新进程。容器复用保住
> 的是**文件系统**（装好的包、生成的文件），不是 shell 状态。要设变量就 export 和
> 使用写在同一条命令里（系统提示词准则 8 说了）。

**回收**：平台里没有"会话结束"这个事件，所以靠空闲超时（默认 30 分钟）+
容器数上限（默认 32）+ 启动时清理残留。

实现细节见 [`docs/implementation/backend-strategy.md`](docs/implementation/backend-strategy.md)。

## 能力层（注册 / 意图 / 执行）

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
| **注册层** `app/capabilities/registration/` | 声明、编目、安装 —— 产出**统一能力清单** | **唯一入口**：agent 只从注册表拿能力 |
| **意图层** `app/capabilities/intent/` | 检索技能 → 收敛工具 → 把模型的一次 tool call 变成一条意图 | 模型提议，意图层收敛；**不产生副作用** |
| **执行层** `app/capabilities/execution/` | 七阶段流水线：校验 → 授权 → 风险 → 审批 → 调用 → 验证 → 留痕 | **所有调用的必经之路** |

三层都是动词，正好是同一条流水线上的三步：**注册**声明能力，**意图**形成这次调用意图，
**执行**真正动手。

能力与实现梳理见 [`docs/implementation/capability-module.md`](docs/implementation/capability-module.md)：
统一能力清单、声明式契约、工具清单与逐段实现、治理能力。
已知限制与待办见 [`docs/plans/capability-followups.md`](docs/plans/capability-followups.md)。
deepAgents 迁移的方案与决策见 [`docs/plans/agent-migration-deepagents.md`](docs/plans/agent-migration-deepagents.md)。

### 执行层是唯一的执行路径

早先 agent 内部的工具调用**直接命中 LangChain 的工具函数**，绕开了执行层 ——
于是没有日志、没有超时、没有重试、没有策略检查。现在工具的 coroutine 走
`execution/pipeline.py`，agent 内部调用和直接调 API 走同一条路，留同一份痕。

七个阶段的顺序是刻意的：便宜的确定性检查在前，昂贵的人介入在中，副作用在后。

```text
① schema 校验 → ② 权限校验 → ③ 风险评估 → ④ 审批等待
→ ⑤ 实际调用 → ⑥ 结果验证 → ⑦ 结果回写
```

### 声明：契约与策略

每个能力在注册时声明清楚：输入输出 schema、风险等级、权限要求、超时、重试策略、
幂等标记。**风险与权限是两个维度**：

| 字段 | 取值 | 含义 |
| --- | --- | --- |
| `risk` | read / write / execute / network / destructive | 最坏能造成什么 |
| `permission` | none / confirm / admin | 谁能放行这次调用 |

**审批按风险档位判**（用户定的口径：**只有中高档才需要审批**）：

| 档位 | 风险等级 | 要不要人批 |
| --- | --- | --- |
| 低 | read / write | 自动放行 —— 改错了能改回来 |
| 中 | execute / network | 要人批 —— 会在进程外产生副作用 |
| 高 | destructive | **永远**要人批 —— 不可逆 |

`permission` 是档位之上的两个口子：`confirm` = 运营额外要求人确认，
`admin` = 管理员放行（信任模式放宽不了）。出厂能力的 `permission` 一律 `none`
—— 档位说了算，声明不重复表达一遍。

信任模式（`CAPABILITY_TRUST_MODE`）只决定**中档**放不放心行：

| 模式 | 放行范围 | 用途 |
| --- | --- | --- |
| `strict` | 中高档要人批；标了 `confirm` 的低档能力也要人批 | 生产 |
| `balanced`（默认） | 中高档要人批 | 默认 |
| `permissive` | 只有高档要人批（中档也放行） | 本地开发 |

**不可逆的操作归高档**，不管信任模式：删文件（`delete`）、删 worktree
（`git_worktree_remove`）、合并分支（`git_merge`）。派子 agent
（`delegate_worker`）归**中档** —— 它会在自己的 worktree 里跑一整轮 agent
（写文件 + 执行命令）。

风险还会被**参数级规则上调**：`execute` 命中 `rm -rf`、`dd`、`curl … | sh`，
或 `web_fetch` 的 URL 指向内网地址（SSRF），都会把风险顶到 `destructive` 并要求
管理员放行。

### 内置能力是"出厂源"，不是运行时直连

`app/capabilities/registration/builtin/` 是白名单，启动时**同步进数据库**
（`origin=builtin`）。之后数据库就是唯一事实来源：运营可以改描述、调参数、单独禁用
某个内置能力；同名冲突会**显式报错**，而不是像以前那样被 LangGraph 静默覆盖。

同步只补空字段 —— 运营改过的内容不会被启动流程冲掉。

### 工具清单

**文件工具由 deepAgents 提供**（8 个），全部限制在会话工作区内：

| key | 名称 | 参数 | 风险 | 说明 |
| --- | --- | --- | --- | --- |
| `ls` | 列出文件 | `path` | read | 列目录下的文件与子目录 |
| `read_file` | 读取文件 | `file_path` `offset` `limit` | read | 读文本文件，带行号 |
| `write_file` | 写入文件 | `file_path` `content` | write | 写入（覆盖），自动建父目录 |
| `edit_file` | 编辑文件 | `file_path` `old_string` `new_string` | write | 精确字符串替换；**原文必须唯一** |
| `delete` | 删除 | `file_path` | destructive | **永远要人批**；不可重试 |
| `glob` | 按模式找文件 | `pattern` `path` | read | 按 glob 模式找文件，如 `**/*.py` |
| `grep` | 搜索文件内容 | `pattern` `path` | read | 按正则搜内容 |
| `execute` | 执行命令 | `command` | execute | 在沙箱里跑一条 shell 命令 |

**我们自己的内置能力**（3 个）：

| key | 名称 | 参数 | 风险 | 档位 | 说明 |
| --- | --- | --- | --- | --- | --- |
| `run_python` | 执行 Python | `code` | execute | 中 | 在沙箱里跑一段 Python，返回退出码 / 输出 |
| `web_fetch` | 抓取网页 | `url` | network | 中 | 抓 http/https 页面并转纯文本（去脚本、去样式） |
| `web_search` | 联网搜索 | `query` `max_results` | network | 中 | 走 Tavily / Serper / Bing 搜索，需配 API Key |

出厂能力的权限要求一律 `none` —— 审批按档位判，声明不重复表达一遍。

几个设计取舍：

- **路径一律相对工作区根目录**。"当前目录"这个概念整体消失 —— 会话级 `cd`
  没有了（`mv` / `cp` / `mkdir -p` 靠 `execute` 跑）。
- **文件工具的治理策略用一张映射表补上**（`registration/tool_policy.py`）——
  它们由 deepAgents 注册，我们手里没有声明，但风险、幂等、要不要人批都得有，
  否则治理就漏了一块。
- **能力返回结构化结果**：dict，其中 `content` 是给模型看的文本，其余字段给程序看
  （校验、日志、自动化消费）。失败抛 `CapabilityError`，带错误码和 `retryable`
  标记，不再返回"看起来像成功"的字符串。
- **执行类一律进沙箱**，且命令写进脚本文件再执行，不做 `sh -c "用户输入"` 拼接。
- **联网搜索没配 Key 就明说**，报"请设置 `WEB_SEARCH_PROVIDER` 和
  `WEB_SEARCH_API_KEY`"，而不是静默失败。

### 审批

需要审批的调用**挂起整张图**，等人工处理：

```text
模型调工具 → 命中 interrupt_on → 图挂在 checkpoint 里
                             ↓
                     响应 status = "pending_approval"
                             ↓
        人工批准 ─┬─ approve → 恢复图 → 工具执行 → agent 自己接着往下跑
                   └─ reject  → 恢复图 → 工具不执行 → agent 收到拒绝消息
```

审批走 **LangGraph 的中断机制**，批准后用同一个 `thread_id` 恢复图（`Command(resume=…)`）。
**结果直接回到 agent 的上下文** —— 不需要"下一轮再补给它"那套回流机制。

审批状态存在 **checkpoint 里**，不在调用记录表里：查待审批列表用
`GET /api/v1/agent/{thread_id}/pending`。

直接调 API 的那条路径不走图，仍然是"生成 pending 记录 →
`POST /api/v1/invocations/{id}/approve` 执行"。

另外，agent 响应里的 `tool_calls` 带**执行摘要**（成功 / 失败 + 调用记录 ID + 耗时），
前端不用再查一遍调用记录才知道这次调用成没成。

### 失败分层

失败按类型分层处理 —— 不同类型的失败，模型该有的反应不一样：

| 类型 | 错误码 | 怎么处理 |
| --- | --- | --- |
| **格式类** | `SCHEMA_INVALID` | 回灌给模型自我修正（改正参数后重试），连续 3 次就阻塞转人工 |
| **语义类** | `INVALID_ARGUMENTS` / `EXECUTION_ERROR` | 同样回灌，但**扣任务置信度**；置信度低于 40 就阻塞转人工 |
| **权限类** | `PERMISSION_DENIED` / `RISK_BLOCKED` | **不许模型重试**，直接终止；能力只是"未授权"时转人工授权流程 |
| **审批类** | `APPROVAL_REQUIRED` | 挂起等人工，批准后自动执行 |
| **熔断** | `CIRCUIT_OPEN` | 该能力近期连续失败，快速失败让模型换条路 |

任务置信度从调用记录算出来（格式类扣 10、语义类扣 25、成功回补 5），
不是内存里的状态 —— 跨进程、跨请求都一致。

### 超时、重试与熔断

- **超时按能力分级**：每个能力声明自己的 `timeout_s`（联网 20s、执行命令 60s），
  执行层再套一层兜底。
- **重试**：指数退避（`backoff_s × 2^(n-1)`，封顶 30s），**且只对幂等能力自动重试**
  —— 不幂等的操作（`execute`、`run_python`）重试可能把副作用做两遍，
  超时只说明"不知道执行完没有"。这类失败直接转人工。
- **熔断**：按能力统计滑动窗口内的错误率与延迟，超过阈值就快速失败一段时间，
  别把时间耗在一个已经坏掉的能力上。

### 接口

```text
GET    /api/v1/capabilities             统一能力清单（跨三类成员，可按 kind/tag 过滤）
GET    /api/v1/capabilities/{key}        按 key 取任意成员
GET    /api/v1/capabilities/{key}/spec   能力的完整声明（契约 + 策略）
POST   /api/v1/capabilities/{key}/invoke  直接调一次能力（调试用，返回结构化结果）

GET    /api/v1/invocations               调用记录分页
GET    /api/v1/invocations/{id}          调用详情（含各阶段结论）
POST   /api/v1/invocations/{id}/approve  批准并执行待审批的调用
POST   /api/v1/invocations/{id}/deny     拒绝待审批的调用
POST   /api/v1/invocations/{id}/cancel   取消待审批的调用

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

GET    /api/v1/agent/{thread_id}/pending  待审批的调用
POST   /api/v1/agent/{thread_id}/approve 批准挂起的调用，恢复图
POST   /api/v1/agent/{thread_id}/reject  拒绝挂起的调用，恢复图
```

对话时用 `skill_ids` 指定要挂载的技能；不传则导出全部启用的技能，由模型按
描述自己挑（渐进披露）：

```json
POST /api/v1/agent/chat
{ "message": "帮我审查这段代码", "skill_ids": [1], "provider_id": 2 }
```

### ⚠️ 内置能力是白名单，不是任意入口点

`builtin` 类型的工具只能引用 `app/capabilities/registration/builtin/__init__.py` 里登记过的
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

失败文本由 `CapabilityError.to_text()` 统一渲染，结尾一定带一句"该怎么做"：
可重试的说"（可以修正参数后重试）"，不可重试的说"（换个思路，不要重复同样的调用）"。
模型拿到的是 `[INVALID_ARGUMENTS] 缺少必填参数：path（可以修正参数后重试）`，
而不是一轮白跑的 traceback。

## RAG 服务层

RAG 是**独立的一层** `app/rag/`，不依赖能力层、也不依赖记忆模块 ——
记忆模块（短期 / 长期两层）是它的一个消费者。

> 这一层**不套用**「models / repositories / services」那套目录约定 ——
> 那是给"业务模块"用的（一个业务用例一个 Service、仓储只管存取）。
> RAG 是一套自成体系的检索设施，把分块、向量、检索策略各自成文件，
> 比拆进 repositories/services 更好读也好改。
> ORM 表仍然注册在 `Base.metadata` 里（`app/models/__init__.py` 统一导入），
> 所以建表和迁移不会漏。

```text
        记忆模块（短期 / 长期）        其他语料检索场景
                  │                            │
                  └──────────┬─────────────────┘
                             ▼
                    ┌──────────────────┐
                    │   RAG 服务层     │
                    └──────────────────┘
```

### 三个概念

```text
collection（一个语料集合，绑定一套向量化配置）
  └─ document（一篇原始文档，按内容哈希去重）
       └─ chunk（检索单元，带向量）
```

**集合必须绑定固定的向量化配置**：不同模型的向量不在同一个空间里，
混着比出来的相似度是噪声。所以模型名和维度记在集合上，建集合时探一次维度，
入库时校验维度 —— **换模型 = 重建集合**，不是改个字段。

### 分块

| 类型 | 切法 | meta 里带什么 |
| --- | --- | --- |
| Markdown | 按标题层级切 | `heading` `level` `path` |
| 代码 | 按顶层定义切（函数 / 类） | `symbol` `kind` |
| 纯文本 | 固定窗口 + 重叠 | `position` `overlap` |

分块按**字符数**算不按 token 数（分块在调模型之前，用不着先分词）；
一次入库的内容不丢。

### 向量化：复用模型接入配置

绝大多数厂商在同一个 `base_url` 上同时提供对话和向量化接口，差别只是模型名。
所以供应商配置表加了两列 `embedding_model` / `embedding_dim`，
向量化请求直接打 `{base_url}/embeddings`（OpenAI 兼容格式）——
**不引入任何新基础设施，密钥继续走 Fernet 加密存储**。

没配 `embedding_model` 的供应商不影响对话，只是不参与向量化。
**集合不配向量模型也能用**（纯关键词检索）。

### 检索：必须混合

- 只上向量会漏 —— 向量把下划线、驼峰抹平了，查 `DATABASE_URL` 召不回；
- 只上关键词也会漏 —— 问"怎么配置数据库"召不回那段 `DATABASE_URL=postgres://…`。

两路用 **RRF（倒数排名融合）** 合并：`score(d) = Σ 权重 / (60 + rank(d))`。
用排名不用分数，是因为余弦在 0-1、BM25 无上界，分数不可比。

关键词一路用 **BM25**，中文按**字符二元组**分词，标识符额外拆开
（`foo_bar` → `foo` + `bar`）。

| mode | 行为 |
| --- | --- |
| `hybrid`（默认） | 向量 + 关键词，RRF 融合 |
| `vector` | 只走向量 |
| `keyword` | 只走关键词 |

集合没启用向量化时自动退化成关键词，并在结果的 `degraded` 字段里说明原因。

v1 的向量存在 SQL 的 JSON 列里，检索在进程内算余弦 —— 几千条分块毫秒级，
**不引入新依赖**。规模上去了在 `VectorStore` 这一层后面换 pgvector / FTS5，
接口不变。

设计细节见 [`docs/implementation/rag-architecture.md`](docs/implementation/rag-architecture.md)，
待办见 [`docs/plans/rag-followups.md`](docs/plans/rag-followups.md)。

### RAG 接口

```text
GET    /api/v1/rag/collections                   集合列表
POST   /api/v1/rag/collections                   新建集合（配了模型会探维度）
GET    /api/v1/rag/collections/{id}              集合详情
DELETE /api/v1/rag/collections/{id}              删集合（连同文档与分块）

GET    /api/v1/rag/collections/{id}/documents    集合里的文档
POST   /api/v1/rag/collections/{id}/documents    入库一篇文档
DELETE /api/v1/rag/documents/{id}                删文档（连同分块）

POST   /api/v1/rag/search                       检索
```

```json
POST /api/v1/rag/collections/1/documents
{ "title": "数据库配置", "content": "用 DATABASE_URL 连库…", "format": "auto" }

POST /api/v1/rag/search
{ "query": "数据库怎么连", "collection_id": 1, "top_k": 5, "mode": "hybrid" }
```

## 记忆模块

记忆模块 `app/memory/` 记的是**关于用户与项目的经验**（偏好、已定结论、
项目约定）。两层，不设第三层：

> 与 RAG 一样，记忆模块**不套用**「models / repositories / services」那套目录
> 约定 —— 它是一套自成体系的存储设施（存储抽象 + 召回策略 + 写入校验）。
> ORM 表仍然注册在 `Base.metadata` 里，所以建表和迁移不会漏。

| 层 | 范围 | 存储 | 生命周期 |
| --- | --- | --- | --- |
| 短期 | 会话内 | `memory_item` 表 | 随 thread 存活，删会话才清 |
| 长期 | 跨会话 | RAG 的专用集合 | 永久，可标记取代 |

**记忆 ≠ 语料 ≠ 工作区说明**：普通文档（文档 / 代码库内容）走 RAG 的集合与
`/api/v1/rag/*` 接口；AGENTS.md 是工作区级的说明文件（类似 CLAUDE.md），
每轮全量注入，不做召回。

### 渐进式披露：上下文里只放目录

模型上下文有限，长期记忆会越攒越多。所以分三层，借鉴 deepAgents 技能的
渐进披露：

```text
L0 目录    每条一行摘要 —— 每轮自动注入上下文
L1 摘要    命中条目的正文前段 —— memory_search 返回
L2 全文    完整正文 —— memory_read 显式取
```

目录自己也会撑爆，所以目录再分两层（主题目录 → 条目目录）。目录有**预算**
（默认 1200 字符），装不下就截断；条目用 **MMR** 去冗余，避免同一主题的十几条
变体占满 top-k。

### 降权 ≠ 淘汰

「很久没用」**不等于**「已过时」（项目用 pnpm 不用 npm —— 第一天说了一次，
一周后才需要它）。所以：

- 降权只影响**排序**，不影响召回资格 —— 没被删除的记忆永远在候选池里；
- 时间衰减限制在 **[0.7, 1.0]**、使用次数限制在 **[0.85, 1.0]**，
  相关性主导，合计最多打三折；
- **命中即重新激活** —— 衰减从最后一次被召回重新算，不是从创建时间算；
- 真正的淘汰只有三个来源：用户手动删、被取代、运营清理。

### agent 怎么用上记忆

| 环节 | 在哪 |
| --- | --- |
| 三个记忆能力变成模型能调的工具 | 出厂内置 → 同步进数据库 → `build_tools()` |
| 每轮自动把**目录**注入系统提示词 | `runner._memory_block()`，独立成块，不混进"运行环境" |
| 告诉模型什么时候查、什么时候写 | 系统提示词准则 7 |
| 审批恢复那一轮也带目录 | `runner.resume()`，检索词取会话最后一条用户消息 |

`memory_search` 的 `scope` 默认 `all`（会话内 + 跨会话都搜）；`memory_write` 的
默认 `session`（不默认污染长期记忆）。**长期记忆集合没配时会降级成会话内**，
而不是报错 —— 模型碰到硬错误往往不会重试，而是自己找个别的办法记（比如改写
工作区的 AGENTS.md），那比记在会话里更糟。

### 写入：什么该记，什么不该记

模型经 `memory_write` 写入，走能力层的七阶段流水线（风险判定、审批、留痕）。
平台侧兜底做硬校验：含凭据**直接拒**、空内容拒、超长拒、同内容合并、
同主题冲突要求显式覆盖。

冲突判据是「类型相同 + 有共同标签」，而且是**两步协议**：先拒绝并把旧记忆的
内容给模型看，模型确认要取代才带 `supersedes` 重试。取代是**标记不是删除**
—— 模型的判断可能错，删了就找不回来。

### 用起来

```bash
# 会话内记忆（不需要额外配置）
curl -X POST '/api/v1/capabilities/memory_write/invoke?thread_id=t1' \
  -H 'Content-Type: application/json' \
  -d '{"content":"项目用 pnpm 而不是 npm","kind":"preference","tags":["pnpm"]}'

# 召回 / 读取
curl -X POST '/api/v1/capabilities/memory_search/invoke?thread_id=t1' \
  -H 'Content-Type: application/json' -d '{"query":"包管理器"}'

# 管理接口：查看 / 目录预览 / 删除
curl 'http://127.0.0.1:8000/api/v1/memory?scope=session&thread_id=t1'
curl 'http://127.0.0.1:8000/api/v1/memory/catalog?query=pnpm&thread_id=t1'
```

**长期记忆需要先配集合**：在 RAG 界面建好集合、选好 embedding 模型，再把集合名
填到 `MEMORY_COLLECTION_NAME`。平台不自动建 —— 集合的向量化配置是建集合时锁死的，
建错了只能重建。

已配好的示例：

```bash
curl -X POST http://127.0.0.1:8000/api/v1/rag/collections \
  -H 'Content-Type: application/json' \
  -d '{"name":"agent-memory","description":"长期记忆集合","scope":"private","embedding_model":""}'
# 然后在 .env 里：MEMORY_COLLECTION_NAME="agent-memory"
```

（`embedding_model` 留空 = 只走关键词检索。想要向量检索就填模型名 —— 但
**集合的向量化配置建时就锁死**，换模型要重建集合、重新灌记忆。）

> ⚠️ 记忆接口**当前没有鉴权**（整个平台都没做）。任何人都能读别人的会话内记忆、
> 读长期记忆集合、往里塞东西。上线前必须补。

实现细节见 [`docs/implementation/memory-module.md`](docs/implementation/memory-module.md)。

## 上下文模块

上下文模块 `app/context/` 决定**一次模型调用看到什么**：把多来源的内容
（基准提示词、运行环境、策略说明、记忆目录）按配额装成一份提示词，并在对话
历史过长时压缩。

**它不负责**：召回到什么（记忆模块）、怎么执行（能力层）、对话历史存哪
（checkpointer）。

### 块与配额

各来源的内容登记成**块**，每块自带优先级与溢出策略：

| 块 | 优先级 | 装不下时 |
| --- | --- | --- |
| 基准提示词 | 0（必装） | 截断 |
| 运行环境 | 10 | 丢 |
| 策略说明（哪些工具要人批） | 20 | 丢（**会警告**） |
| 记忆目录 | 40 | 丢 |

预算按**模型窗口的比例**算（默认 50%），另一半留给输出、工具 schema 和
deepAgents 自己追加的提示词 —— 那部分我们控制不了。

### 压缩：接管，不重造

deepAgents 自带摘要中间件，而且比 LangChain 原版多做了一件事：**被压缩掉的
历史先落到文件里**，agent 需要时能 `read_file` 回去翻。

我们**同名替换**它（`name = "SummarizationMiddleware"`），只改阈值 —— 压缩的
机制沿用框架的。

> ⚠️ **这里有个容易忽略的坑**：模型的 `profile.max_input_tokens` 没告诉框架时，
> deepAgents 的阈值会退化成固定的"170000 token 触发、保留 6 条消息"。我们用
> OpenAI 兼容端点挂的模型**默认没有 profile** —— 不显式设的话，1M 窗口的
> DeepSeek 只用不到两成窗口就开始压缩，压完只剩 6 条（一轮工具调用就有好几条）。
>
> 修法：给模型挂上 `profile`（`app/agents/llm.py` 的 `_apply_context_window`），
> 窗口大小来自供应商配置的 `context_window` 字段（默认取厂商预设）。

### 排查

```bash
# 预览这次会给模型看什么，以及哪些块被丢了 / 截断了
curl 'http://127.0.0.1:8000/api/v1/context/preview?query=pnpm&workspace=/tmp/ws'
```

实现细节见 [`docs/implementation/context-module.md`](docs/implementation/context-module.md)。

## 工作目录与多智能体协作

会话默认在平台自带的**会话工作区**（一个空白目录）里干活。这套能力让会话可以
绑一个**真实的代码仓库**当工作目录，还能把任务拆开、派子 agent 并行做。

```text
GET    /api/v1/environments                    白名单下的目录、仓库、已有 worktree
GET    /api/v1/environments/browse             浏览目录下的子目录（选文件夹用）
POST   /api/v1/environments                    从仓库派生 worktree（独立检出 + 新分支）
DELETE /api/v1/environments?path=...           删掉平台创建的 worktree
GET    /api/v1/environments/worktrees          列出 worktree
GET    /api/v1/environments/binding/{thread_id}  会话当前绑的工作目录
POST   /api/v1/environments/binding/{thread_id}  绑定（body: path）
DELETE /api/v1/environments/binding/{thread_id}  解绑
```

### 两个根目录

| 配置 | 是什么 |
| --- | --- |
| `REPO_ROOTS` | 允许作为工作目录的根目录，逗号分隔。**默认空 = 一个都不许** |
| `WORKTREE_ROOT` | 平台派生 worktree 的落点。默认 `<工作区根>/worktrees` |

`REPO_ROOTS` 默认留空是刻意的：平台没有鉴权，白名单是唯一的闸门 —— 不配置就
完全不能用这个功能，而不是"默认什么都能用"。

`WORKTREE_ROOT` 单独配，是因为 **worktree 要挂进沙箱容器**：容器运行时
（Colima 只共享 `$HOME`、Docker Desktop 默认共享 `/Users`）只能挂载被共享的
路径。放在工作区根下面，默认就落在共享区里；放在仓库旁边则大概率挂不进去。

### 绑定之后，两个"根"分开了

```text
agent 的工作目录  = 绑定的环境（可能是真实仓库）
会话工作区        = 平台自己的目录（上传件、技能导出、产物、文件页）
```

**上传件不会落进用户的仓库里** —— 它们留在会话工作区，以 `/uploads` 路由挂给
agent；记忆文件同理走 `/.memory` 路由。见 `app/capabilities/backends/assembly.py`。

### 选中仓库 = 自动开一个 worktree

**不用先填分支名** —— 分支由平台生成（`qzd/<仓库>-<随机>`）。用户选一个仓库，
平台就开一份独立检出并绑上。**没有「新建 worktree」这个单独的入口** ——
独立检出不是一个需要单独配置的动作。

绑定的环境也可以**起个名字**（`label`）—— 界面上显示的是那个名字，不是目录名。

### 选本机的文件夹

「新建工作区」里的目录选择器**能往下钻**（点子目录进去）、**能往回退**（上一级），
也能把路径直接粘进去（在访达里按 `⌘⌥C` 复制路径，回车跳过去）—— 白名单下可能
有几十层目录，一个固定的下拉列表挑不动。

底下是 `GET /api/v1/environments/browse`：**目录浏览也是读宿主机的文件系统**，
所以它跟绑定一样受白名单约束（解析真实路径之后比较，软链接指向外面的一律拒绝）。

### git 是平台能力，不是 agent 自己敲的命令

沙箱镜像里没有 git，而且 worktree 里的 `.git` 是个指向主仓库的**指针文件**，
容器里解析不了。所以 git 一律是**宿主侧**的能力（`app/git/service.py`）：

| 能力 | 做什么 | 档位 |
| --- | --- | --- |
| `git_status` | 当前分支、未提交的改动 | 低 |
| `git_worktree_add` | 开一个新的独立检出（独立分支） | 低 |
| `git_worktree_list` | 列 worktree | 低 |
| `git_worktree_remove` | 删平台创建的 worktree | **高**（永远要人批） |
| `git_commit` | 提交当前目录的改动 | 低 |
| `git_merge` | 把分支合进来（有冲突就报告冲突文件） | **高**（永远要人批） |

**会话没绑工作目录时，这些能力根本不会出现在模型的工具列表里** —— 给模型一批
用不了的工具，它会试了再报错，白烧轮次。

### 多智能体：派子 agent（`delegate_worker`）

任务能拆成互不相干的几块时，主 agent 可以调 `delegate_worker` 把一块交给子
agent：它在**自己的 worktree**（独立检出 + 独立分支）里干活，干完提交。

`delegate_worker` 是**中档**（跟 `execute` 一样）：worker 会在自己的 worktree 里
跑一整轮 agent，写文件 + 执行命令 —— 所以派活之前要人点一次批准。

```text
主 agent（工作区 A）
   │  delegate_worker(任务)
   ▼
worker（worktree B，分支 qzd/xxx）  ← 独立目录、独立分支、独立沙箱容器
   │  做完提交
   ▼
主 agent 拿到分支与结论 → 分支不会自动合回主线，由人手动合
```

worker 与主 agent 的三处不同（`app/agents/worker.py`）：

| | 主 agent | worker |
| --- | --- | --- |
| 工作目录 | 会话绑定的环境 | 自己的 worktree |
| 人工审批 | 有（前端点批准） | **无**（没有界面，挂起就等不到人批） |
| 历史 | 有 checkpointer，多轮 | 无，一次性执行 |

> ⚠️ **worker 里不挂人工审批**是明确的安全取舍：worker 的写操作与命令执行
> 没人批。唯一的防线是**物理隔离** —— 它只在自己的 worktree 里干活，命令在
> 断网容器里跑、只挂载那个 worktree。主仓库碰不到。

递归到此为止：worker 的能力清单里**没有** `delegate_worker` 本身，深度封顶两层。

派 worker 有三处容易踩的坑，实现里都处理了（细节见 `app/agents/worker.py`）：

- **checkpoint 隔离**：worker 显式用自己的 `thread_id`，不继承外层图的 ambient
  config —— 否则 worker 的状态会写进主会话的 checkpoint 谱系里，**把主会话冲掉
  而且不报错**；
- **并发闸**：worker 用自己的执行标识，不跟外层抢名额（抢就是自己堵死）；
- **幂等**：同一个分支再建 worktree 会复用，不会多出一份。

> ⚠️ **这组接口同样没有鉴权** —— 知道 `thread_id` 就能把会话绑到白名单内的任意
> 目录、让平台在宿主机上建分支跑 git、派子 agent 在仓库里写代码执行命令。
> 涉及的是**源码仓库**，比现有的文件接口更严重。白名单是唯一的闸门，上线前
> 必须补鉴权。

### 触发方式

**由模型自己判断**（提示词准则 10）：能不能拆、拆成几块、什么时候切分支，都是
模型的判断。平台不提供"并行派 N 个"的控件 —— 拆任务本来就是模型擅长的部分。


用户在对话里上传文件、给图片提问；agent 做出来的东西用户下载得走。

```text
POST   /api/v1/files              上传（multipart），落进会话工作区的 uploads/
GET    /api/v1/files              列出工作区文件
GET    /api/v1/files/tree         列出工作区的文件**与目录**（@ 提及面板用）
GET    /api/v1/files/archive      打包下载（整个工作区打成一个 zip）
GET    /api/v1/files/download     按虚拟路径下载
PUT    /api/v1/files              保存文件内容（覆盖写，只处理文本）
PATCH  /api/v1/files              重命名 / 移动
DELETE /api/v1/files              删除
```

写入与重命名的路径安全比读更严：除了词法挡 `..`，还把路径 `resolve` 之后
跟工作区比一遍（中间某段是软链接指向外面就拒掉），写文件时最后一段是软链接
也不跟（`O_NOFOLLOW`）。

> **这些接口仍然没有鉴权** —— 知道 `thread_id` 就能读、**改**、删别人工作区里
> 的文件。上线前必须补鉴权。

上传件落**工作区里** —— agent 用现成的 `read_file` / `grep` / `glob` 就能处理，
不需要新增任何 agent 能力。

图片另外**内联进用户消息**（`image_url`），模型这一轮就能"看见"：

```bash
# 1. 先传，拿回虚拟路径
curl -F 'file=@screenshot.png' 'http://127.0.0.1:8000/api/v1/files?thread_id=t_1'
# → {"data": {"path": "/uploads/20261102_143000_screenshot.png", ...}}

# 2. 再带附件提问
curl -X POST 'http://127.0.0.1:8000/api/v1/agent/chat' \
  -H 'Content-Type: application/json' \
  -d '{"message": "这张图里有什么问题？", "thread_id": "t_1",
       "attachments": ["/uploads/20261102_143000_screenshot.png"]}'
```

**能不能看图取决于模型**：供应商配置上的 `vision_enabled`（默认按厂商预设）。
不支持就只把路径告诉 agent，不会让整轮对话报错。

> ⚠️ **文件接口目前没有鉴权** —— 知道 `thread_id` 就能下载别人会话里的所有
> 文件。这是"记忆接口无鉴权"那条风险的更严重版本，上线前必须补。

实现细节见 [`docs/implementation/file-module.md`](docs/implementation/file-module.md)。

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

现在有七版迁移：

| 版本 | 内容 |
| --- | --- |
| `0001_baseline` | 脚手架初期的表结构（引入 Alembic 之前的快照） |
| `0002_capability_layer` | 能力层重构：skill.prompt → knowledge、新增若干列、新建 execution_log |
| `0003_capability_v2` | 声明式契约：tool 补 risk/output_schema/timeout_s/retry_policy/annotations；permission 取值域 low→none、high→confirm；execution_log 改名 invocation 并扩成完整生命周期 |
| `0004_builtin_specs` | 内置能力出厂规格升级：模块路径迁移到注册层下，补齐风险等级与策略 |
| `0005_invocation_confidence` | 调用记录补 `confidence`（任务置信度，从记录算出来的那个分数存下来方便回溯） |
| `0006_rag_layer` | RAG 层：新建 rag_collection / rag_document / rag_chunk；llm_provider 补 embedding_model / embedding_dim |
| `0007_agent_capability_flow` | agent 侧能力调用闭环：invocation 补 `notified_at`（审批结果是否已回流给 agent） |

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
pytest -q          # 215 passed
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

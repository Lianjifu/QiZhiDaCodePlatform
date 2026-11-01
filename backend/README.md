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
│   │       ├── agent.py        # 编码助手（deepAgents + 审批）
│   │       └── llm_provider.py # 模型接入配置（供应商 CRUD）
│   ├── agents/              # Agent 层：模型 + 工具 + 循环
│   │   ├── llm.py           # 模型工厂（环境变量 / 数据库供应商配置两种来源）
│   │   ├── providers.py     # 供应商预设表（兼容主流大模型）
│   │   ├── prompts.py       # 系统提示词
│   │   ├── builder.py       # create_deep_agent 组装（含子代理规格）
│   │   ├── runner.py        # 执行器（同步一轮 / 流式）
│   │   └── checkpointer.py  # 多轮对话上下文持久化
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
│   │   │       └── web.py     # 网络类能力（抓取 / 搜索）
│   │   ├── intent/            # 意图层：模型的一次 tool call → 一条意图
│   │   │   ├── intent.py      # CapabilityIntent（与执行层的唯一契约）
│   │   │   ├── resolver.py    # tool call → 意图（含前置参数校验）
│   │   │   └── middleware.py  # 治理中间件（工具收敛 / 参数级风险 / 留痕）
│   │   └── execution/         # 执行层：七阶段流水线
│   │       ├── pipeline.py    # 校验→授权→风险→审批→调用→验证→回写
│   │       ├── adapters.py    # 执行适配器（builtin / http / mcp）
│   │       ├── tooling.py     # 能力声明 → 模型可调用的工具
│   │       ├── risk.py        # 参数级风险评估（SSRF / 破坏性命令）
│   │       ├── redaction.py   # 结果脱敏
│   │       ├── result.py      # 执行结果
│   │       └── context.py     # 执行上下文（工作区、沙箱、MCP 通道）
│   ├── schemas/             # 请求 / 响应模型
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
POST /api/v1/agent/chat     跑完一轮再整体返回
POST /api/v1/agent/stream   SSE 流式推送
```

配置（`.env`）：

```bash
LLM_PROVIDER="openai"        # openai | anthropic | ollama
LLM_MODEL="gpt-4o-mini"
LLM_API_KEY="sk-..."
LLM_BASE_URL=""              # 国产模型走这个：https://api.deepseek.com/v1
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

实际要不要审批，由**信任模式**（`CAPABILITY_TRUST_MODE`）在运行时决定：

| 模式 | 放行范围 | 用途 |
| --- | --- | --- |
| `strict` | 完全按 `permission` 声明 | 生产 |
| `balanced`（默认） | 读写自动放行；执行命令 / 联网抓取需确认 | 默认 |
| `permissive` | 只有 `destructive` 需确认 | 本地开发 |

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
| `delete` | 删除 | `file_path` | write | **永远要人批**；不可重试 |
| `glob` | 按模式找文件 | `pattern` `path` | read | 按 glob 模式找文件，如 `**/*.py` |
| `grep` | 搜索文件内容 | `pattern` `path` | read | 按正则搜内容 |
| `execute` | 执行命令 | `command` | execute | 在沙箱里跑一条 shell 命令 |

**我们自己的内置能力**（3 个）：

| key | 名称 | 参数 | 风险 | 权限 | 说明 |
| --- | --- | --- | --- | --- | --- |
| `run_python` | 执行 Python | `code` | execute | confirm | 在沙箱里跑一段 Python，返回退出码 / 输出 |
| `web_fetch` | 抓取网页 | `url` | network | confirm | 抓 http/https 页面并转纯文本（去脚本、去样式） |
| `web_search` | 联网搜索 | `query` `max_results` | network | confirm | 走 Tavily / Serper / Bing 搜索，需配 API Key |

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
记忆模块（短期 / 长期两层）是它的一个消费者，尚未实现。

> 这一层**不套用**「models / repositories / services」那套目录约定 ——
> 那是给"业务模块"用的（一个业务用例一个 Service、仓储只管存取）。
> RAG 是一套自成体系的检索设施，把分块、向量、检索策略各自成文件，
> 比拆进 repositories/services 更好读也好改。
> ORM 表仍然注册在 `Base.metadata` 里（`app/models/__init__.py` 统一导入），
> 所以建表和迁移不会漏。

```text
        记忆模块（短期 / 长期）        知识库 / 文档问答
                  │                            │
                  └──────────┬─────────────────┘
                             ▼
                    ┌──────────────────┐
                    │   RAG 服务层     │
                    └──────────────────┘
```

### 三个概念

```text
collection（一个知识库，绑定一套向量化配置）
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

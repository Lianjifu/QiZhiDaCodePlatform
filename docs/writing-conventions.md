# 文档写作约定

写这个仓库的文档时照这份约定走。它同时也是**跨模块共享的事实**清单 ——
权限模式、审批的两级、风险分档、沙箱、记忆分层这几件事贯穿所有模块，
每篇文档里写到的都要对得上。

## 语气

中文。**说"为什么"，不只说"是什么"**。一段话如果只是在复述代码里能直接看到的东西，就删掉。

判据：读者是**三个月后的自己**（或者一个刚接手的人）。他需要知道的是：
- 这块为什么这么做（不看代码就猜不到的那部分）
- 哪些地方改错了会出事（不变量）
- 哪些地方是明知有问题的（已知限制），以及影响面

## 每篇文档的结构

```markdown
# <模块名>

> 一句话：<这个模块是什么>
>
> 前端入口：<页面路由 / 组件>，或"纯后端，无独立页面"

## 1. 这个模块是什么

## 2. 接口清单

| 方法 | 路径 | 说明 |
| --- | --- | --- |

## 3. 实现要点

### 3.x 小节

## 4. 已知限制

## 5. 相关文件
```

## 硬性要求

1. **接口清单必须与代码逐条对得上**。写之前用
   `grep -rn "@router\." app/api/v1/<file>.py` 核一遍，方法、路径、参数一个都不能错。
2. **函数名、文件路径、配置项名一律照抄代码**。不要改写、不要意译成别的名字。
3. **不变量单列出来**并说明"改错了会怎样"。这一节是文档里最值钱的部分 ——
   这些坑是**实测复现过的**，不是设想。
4. **已知限制只列现在还成立的**。已经修好的不要写进去。
   每条写清"现状是什么 + 影响面"，**不写打算怎么做**（那是计划，不进本文档）。
5. **不要出现"将要""计划""后续""建议""待办"这类词**。这份文档只描述现在。
6. 不确定的事**不要编**。要么去代码里确认，要么不写。

## 跨模块共享的事实（写任何一篇都要对得上）

### 权限模式

五个，会话级。语义与 Claude Code 对齐。

| 模式 | 行为 |
| --- | --- |
| `auto`（默认） | 按风险档位：只读 / 写入自动放行，执行命令 / 联网 / 破坏性要人批 |
| `manual` | 任何会改东西的操作都要人批，只读不问 |
| `accept_edits` | 文件编辑与命令都自动放行，只有破坏性要人批 |
| `plan` | 只读：写类与执行类工具**不暴露给模型** |
| `bypass` | 全放行（含破坏性），受进程级总闸 `capability_allow_bypass` 约束 |

- **判定只有一个函数**：`app/capabilities/registration/policy.py::needs_approval()`。
  中间件告诉模型的那段说明、执行层的判定、审批闸的谓词，三处都用它。
  三处各写一套 = 批准过了又被拦一次。
- 模式存**文件**（`<workspace_root>/.qzd/permissions/<会话>.json`），不是表 ——
  审批恢复可能落在另一个进程上，文件跨进程可见。
  单一事实来源：`app/agents/permissions.py::resolve_permission_mode(thread_id)`。

### 审批闸的两级

| 级 | 看什么 | 挂起点 |
| --- | --- | --- |
| **声明级** | 工具本身（"execute 这个工具要人批"） | 工具执行**之前**（deepAgents 的 HITL） |
| **参数级** | 这次调用的参数（"这条命令里有 rm -rf"） | 工具执行**中**（我们自己的 `interrupt()`） |

**一次调用只让人批一次**。声明级先于工具执行发生 —— 工具能跑到参数级检查，
说明人已经批过了。

### 审批闸的位置不能变（2026-11-05 修过的一个真 bug）

**闸挂在哪些工具上，不能随权限模式变。** 变了的时候：挂起期间用户把模式切到
「绕过权限」，图被重建成没有闸的样子，checkpoint 里那条挂起的中断就"看不见"了，
恢复变成空转 —— 接口返回 200，工具根本没跑，界面表现是"审批后无法继续"。

- 结构固定：`app/agents/builder.py::_interrupt_on()` 永远挂全部**候选**工具
  （`tool_policy.approval_candidate_tools()`）。
- 拦不拦运行时判：`when` 谓词 → `_needs_approval_now()` → 每次都重读
  `resolve_permission_mode(thread_id)`。
- 没有待批的可批时**报错**（409），不静默空转。

### 沙箱

- 默认 `docker`。`execute`（命令）与 `run_python` 走**同一套**判断 ——
  两边各写一套迟早跑偏。
- 容器**按会话复用**（`docker exec`，不是每次 `docker run --rm`）。
  保留：装好的包、容器里生成的文件、后台进程。
  **不保留**：环境变量、当前目录（`docker exec` 每次是新进程）。
- 隔离参数：`--network none --memory 512m --cpus 1 --pids-limit 256
  --cap-drop ALL --security-opt no-new-privileges`，只挂载会话工作区。
- 文件操作走**宿主** `FilesystemBackend`（快，路径已经虚拟），
  `DockerSandbox` **只提供 execute**：改单个文件的操作全部
  `NotImplementedError`，查找类（`ls`/`glob`/`grep`）返回**空结果** ——
  容器里的文件树不是平台的文件树。
- **沙箱不可用时不静默降级** —— 宁可 agent 直接失败。

### 平台无鉴权（平台级的既有决策）

所有接口都不校验身份：知道 `thread_id` 就能读写别人的会话、文件、记忆。
用户已明确推迟鉴权。涉及"这个接口能动宿主机的什么"的地方要写清楚。

### 风险与审批

- 风险等级：`read` < `write` < `execute` < `network` < `destructive`。
- 分档：read / write → **低**（自动放行）；execute / network → **中**（要人批）；
  destructive → **高**（永远要人批，只有 `bypass` 放它）。
- 参数级规则**只能上调风险，不能下调**。
- 参数级的挂起只由**威胁信号**触发（`RiskAssessment.threat`）——
  "分类调整"（比如平台的向量化端点会把数据发出去）不算威胁，
  否则每查一次知识库都要人工审批。

### 记忆

**只有两层**，不要"情景记忆"、不要"知识"那一层、不要三层。

| 层 | 范围 | 落在哪 |
| --- | --- | --- |
| 短期 | 一个 thread 内 | `memory_item` 表 |
| 长期 | 跨会话 | RAG 的专用集合（名字 `agent-memory`） |

**AGENTS.md 不是记忆** —— 它是工作区级、agent 自己维护的说明文件，
每轮全量注入、不受预算控制、不做相关性排序。

## 框架的红线

写新代码时**能用框架就用框架** —— deepAgents / LangChain / LangGraph 里已有的
东西不要自己再写一遍。但平台有几条硬规矩。

判据只有一条：

> **凡是会让工具调用绕过七阶段能力流水线的框架件，一律不能拿来替换自研实现。**
> 只能在流水线**之下**复用（当执行器用），不能在上游接管。

三条安全底线同样不可让步：**沙箱隔离**、**参数级风险判定**（`rm -rf` / SSRF）、
**调用留痕**。

### 绝对不能引进来的框架件

| 框架件 | 为什么不行 |
| --- | --- |
| `langgraph/prebuilt/chat_agent_executor.py` `create_react_agent` / `langgraph/prebuilt/tool_node.py` `ToolNode` | 会让工具脱离平台流水线 —— 无留痕、无审批、无风险判定。**注意 LangChain 1.x 的 `langchain/agents/factory.py` 只有 `create_agent`，`create_react_agent` 已经搬到 LangGraph 的 prebuilt 里** |
| `langchain/agents/middleware/shell_tool.py` `ShellToolMiddleware` + `DockerExecutionPolicy` | 绕开沙箱隔离与能力流水线；框架的沙箱没有平台的 `--network none` / `--cap-drop ALL` / `no-new-privileges`，而且**每条命令起一个新容器**（没有复用） |
| `langgraph/store/` 当长期记忆 | 放弃 hybrid 召回 + 记忆写入硬校验（凭据拦截、冲突拒绝、取代语义） |
| `langgraph/types.py` `Send` / 框架原生 handoff | 会让子代理调度绕开平台的 `delegate_worker`（独立 worktree + 递归封顶 + git 能力收窄） |
| 框架 `permissions=[FilesystemPermission]` | 在可执行后端上直接抛 `NotImplementedError`；且会引入第二个审批事实源，与 `policy.py::approval_decision` 打架 |
| `langchain/agents/middleware/tool_retry.py` `ToolRetryMiddleware` | 重试包在工具调用**外层** → 连整个七阶段流水线一起重跑 → **每次重试多一条 `Invocation` 留痕**，审计失真。平台的重试在阶段⑤内部，留痕只一条 |

### 框架有、但给的是"另一层"的

| 框架的 | 平台的 | 为什么不是一回事 |
| --- | --- | --- |
| `FilesystemPermission`（`deepagents/middleware/filesystem.py`） | `app/agents/permissions.py` + `app/capabilities/registration/policy.py` | 框架按**路径模式** allow/deny/interrupt；平台按**风险档位 × 会话模式** |
| `ContextEditingMiddleware` | `app/context/{blocks,assembler,budget}.py` | 框架是"清掉旧工具结果"；平台是"按优先级重装上下文块 + 外置历史" |
| `langgraph/store/` | `app/memory/` | 框架是通用 KV + 向量索引；平台是 RAG 集合 + hybrid 召回 + 写入硬校验 |
| `DockerExecutionPolicy` | `sandbox_backend.py` + `container_pool.py` | 框架版每次命令起新容器；平台的容器按会话复用（框架无 session_id / 复用 / 池钩子） |
| `langchain_text_splitters` | `app/rag/chunking.py` | 切法不同 —— 平台的切法**为记忆层服务**（分块 meta 带 `kind` / `memory_kind` / `tags`，记忆层按它过滤） |
| `langchain_core` 的 `VectorStore` | `app/rag/vector_store.py` | 框架是**文档导向**（字符串 / `Document`）；平台是**分块导向 + 集合作用域**（三个检索接口都带 `collection_id`） |
| `LLMToolSelectorMiddleware` | `app/capabilities/intent/middleware.py`（plan 模式裁工具集） | 框架用 LLM 挑工具 = 模型**先看见全部工具名**再筛；平台在 plan 模式下**直接把写类工具摘掉** |

### 已经在用框架的

| 平台用的 | 框架提供方 |
| --- | --- |
| `app/context/compaction.py` `PlatformSummarizationMiddleware` | 继承 deepAgents 的 `SummarizationMiddleware`，同名替换 |
| `app/context/skills_quota.py` `QuotaSkillsMiddleware` | 继承框架 `SkillsMiddleware`，只覆写清单配额 |
| `app/agents/builder.py` 的 `MemoryMiddleware` / `TodoListMiddleware` / `interrupt_on` | 框架的 |
| `app/agents/checkpointer.py` `AsyncSqliteSaver` | LangGraph 的 |
| `app/capabilities/backends/assembly.py` `FilesystemBackend(virtual_mode=True)` | deepAgents 的 |
| `app/capabilities/mcp_client.py` `MultiServerMCPClient` | `langchain_mcp_adapters` 的 |

**框架默认自动生效、平台没意识到自己在受益的**：提示词缓存（`_prompt_caching.py`）、
二进制外置（`_blob_offload.py`，**默认关着**，平台也没开）、大结果预览替换与
溢出裁尾（`_message_eviction.py` / `_overflow_clip.py`）、tool call 修补
（`patch_tool_calls.py`）、按模型 profile 过滤 content block
（`unsupported_content.py`）。

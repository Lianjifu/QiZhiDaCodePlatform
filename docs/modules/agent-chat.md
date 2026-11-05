# 编码助手（对话模块）

> 一句话：平台的对话入口 —— 用户发一句话，agent 在工作区里思考、调工具、回话；
> 撞上需要人批的操作就把这一轮挂起，等人批完再接着跑。
>
> 前端入口：`/chat` 与 `/chat/:threadId`（`frontend/src/App.tsx`），
> 代码在 `frontend/src/features/agent/`。

## 1. 这个模块是什么

编码助手是**平台唯一的多轮对话界面**。它自己不发消息、不做检索、不执行工具 ——
它做的是三件事：

1. **把一轮用户输入跑成一次完整的 agent 循环**（模型思考 → 调工具 → 结果回灌 →
   再思考），直到模型给出最终回复；
2. **在人需要拍板的地方停下来** —— 挂起这一轮、把人看到的待批清单交出去，
   批完再从挂起处恢复，让 agent 自己接着往下跑；
3. **把整个过程如实呈现** —— 正在想什么、正在跑哪个工具、跑完了改了哪几行、
   这一轮占了多少上下文。

模块的边界在 `backend/app/agents/`（编排）、`backend/app/services/agent.py`
（业务入口）、`backend/app/api/v1/agent.py`（HTTP 路由）、
`frontend/src/features/agent/`（界面）四处。

**它管什么，不管什么**：

| 归本模块 | 不归本模块 |
| --- | --- |
| 一轮对话的编排与三种结束状态 | 能力清单与工具怎么执行（能力模块） |
| 审批的挂起 / 恢复 / 图的续跑 | 审批的风险分档与判定规则（能力模块） |
| 会话锁、图缓存、checkpointer 生命周期 | 上下文预算与提示词装配（上下文模块） |
| 斜杠命令、重新生成、分支、清空 | 记忆的写入判据与召回（记忆模块） |
| 对话界面：状态行、工具块、审批卡片 | 沙箱隔离参数、文件读写（后端与工作区模块） |

### 1.1 平台没有会话表

这一点最容易误解，先说清楚：**平台没有"会话"这张表。**

- `thread_id` 是**客户端自己带的字符串**。前端新建会话时生成一个，挂在 URL 上
  （`/chat/:threadId`）；发请求时带上。`AgentChatRequest.thread_id` 不传时
  后端会用 `uuid4().hex` 现生成一个。
- 它的作用只有一个：**当 LangGraph checkpoint 的 key**。多轮对话之所以接得上，
  是因为同一个 `thread_id` 命中同一份 checkpoint。
- **对话历史全部在 checkpoint 里**，不在数据库。数据库里只有三类与会话相关的
  东西，且都不是"会话"本身：
  - `memory_item` 表 —— 会话内短期记忆（随会话存活，删会话才清）；
  - `invocation` 表 —— 工具调用记录，按 `thread_id` 归集；
  - `retrospective` 表 —— 复盘记录，`thread_id` 可空。

因此「删掉一个会话」不是一个 `DELETE FROM sessions`，而是**分别去几处清理**：
checkpoint、权限模式文件、环境绑定、短期记忆、工作区目录（见 §3.17）。

推论：**知道 `thread_id` 就能读别人的会话、删别人的会话**。平台目前无鉴权
（见 §4）。

## 2. 接口清单

全部挂在 `app/api/v1/agent.py` 的 router 上，router prefix 是 `/agent`，
加上全局前缀是 `/api/v1`。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/v1/agent/chat` | 跑完一轮整体返回（脚本调用用） |
| POST | `/api/v1/agent/stream` | SSE 流式（前端用） |
| GET | `/api/v1/agent/commands` | 斜杠命令清单（后端定，前端只渲染） |
| GET | `/api/v1/agent/{thread_id}/pending` | 这个会话当前挂着哪些待审批的调用 |
| POST | `/api/v1/agent/{thread_id}/approve` | 批准挂起的调用，恢复图继续跑 |
| POST | `/api/v1/agent/{thread_id}/reject` | 拒绝挂起的调用，恢复图继续跑 |
| GET | `/api/v1/agent/{thread_id}/permission` | 查这个会话的权限模式 |
| POST | `/api/v1/agent/{thread_id}/permission` | 设这个会话的权限模式 |
| GET | `/api/v1/agent/{thread_id}/status` | 会话状态（环境 / 上下文占用 / 产出 / 改动量 / 连接器 / 协作 / 任务） |
| GET | `/api/v1/agent/{thread_id}/history` | 读历史消息（前端刷新后还原对话用） |
| DELETE | `/api/v1/agent/{thread_id}` | 清空对话记录 + 权限模式；**工作区文件保留** |
| DELETE | `/api/v1/agent/{thread_id}/session` | 彻底删：checkpoint、短期记忆、权限、环境绑定、**工作区目录** |
| POST | `/api/v1/agent/{thread_id}/regenerate` | 重新生成上一轮，SSE 流式 |
| POST | `/api/v1/agent/{thread_id}/fork` | 从某条消息分支出新会话 |

补充几点（都是容易写错的地方）：

- **没有 `/api/v1/agent/{thread_id}/environment` 这个路由**。`backend/README.md`
  里写过这条，实际不存在。**绑定工作目录走 `/api/v1/environments/binding/{thread_id}`**
  （GET / POST / DELETE），属于工作区模块。
- `approve` / `reject` 的请求体是可选的（`AgentDecisionIn`）。带
  `{"decisions": ["approve", "reject"]}` 就是逐条决定，一条挂起的调用一个决定，
  顺序与 `pending` 清单一致；**不带 = 全部批准（或全部拒绝，取决于调的是哪个接口）**。
- `regenerate` 的请求体只有 `provider_id` 与 `skill_ids`，**不需要传消息** ——
  它用的是历史里最后一条用户消息。
- `fork` 的请求体是 `{"message_id": "..."}`，那个 id 是 checkpoint 里的消息 id
  （`GET /history` 返回的 `messages[].id`），不是渲染数组的下标。
- `DELETE /api/v1/agent/{thread_id}/session` **前端目前没接**（`frontend/src/api/resources.ts`
  里没有对应调用），只有后端接口。

## 3. 实现要点

### 3.1 一轮对话的生命周期

以 `POST /agent/chat` 为例（`services/agent.py::AgentService.chat`）：

```text
POST /api/v1/agent/chat
  │
  ▼ ① resolve_agent_workspace(thread_id)
  │     算出两个目录：agent 工作目录 + 会话工作区（§3.2）
  │
  ▼ ② turn_lock(thread_id)            ← 会话级独占（§3.4）
  │
  ▼ ③ open_checkpointer()             ← 每次请求新开一个 SQLite 连接（§3.5）
  │
  ▼ ④ _resolve_model(provider_id)     ← 数据库里的供应商配置，没配就退回 .env
  │
  ▼ ⑤ load_capabilities() + _visible_specs()
  │     没绑工作目录时剔掉 delegate_worker 与 git_*（§3.3）
  │
  ▼ ⑥ _build_context()                ← 执行上下文 + MCP 供给通道 + DB 会话
  │
  ▼ ⑦ AgentRunner.run()
  │     ├ build_attachments()         ← 图片按 supports_vision 内联
  │     ├ _build()                    ← 查 GRAPH_CACHE，命中就复用（§3.14）
  │     ├ _fill_holder()              ← 每轮重填记忆目录 / 上传件清单 / 反思块
  │     ├ agent.ainvoke()
  │     └ _result_from()              ← 收尾，整理成 AgentRunResult
  │
  ▼ ⑧ 提交数据库会话（执行层写的调用记录要落库）
  │
  └→ AgentChatOut { thread_id, content, steps, tool_calls, status, pending }
```

一轮只有**三种结束状态**（`runner.py` 顶部常量）：

| status | 含义 | content | pending |
| --- | --- | --- | --- |
| `completed` | 跑完了，模型给了最终回复 | 有 | 空 |
| `pending_approval` | 中途撞上人工审批，图挂在 checkpoint 里 | **空** | 待批清单 |
| `failed` | 这一轮炸了（只有 SSE 的 `done` 帧用得上） | 空 | 空 |

`pending_approval` 与 `completed` 都是**一轮正常结束**，区别只是这一轮有没有产出
最终回复。挂起的那一轮不算失败，也不重跑 —— 恢复之后 agent 自己接着往下跑。

### 3.2 两个工作区

`resolve_agent_workspace(thread_id)` 返回两个路径，**绑了工作目录之后两者不同**：

| | 是什么 | 装什么 |
| --- | --- | --- |
| **agent 工作目录** | 用户绑的环境（可能是真实的代码仓库）；没绑就是会话目录 | agent 的文件工具与命令执行都在这里 |
| **会话工作区** | 平台自己的目录 | 上传件、技能导出、产物、文件页看到的东西 |

**上传件不能落进用户的仓库里** —— 这条是硬要求。会话工作区在
`<workspace_root>/<workspace_dir_name(thread_id)>`，目录名 = 可读前缀 + `_` +
`thread_id` 的 sha256 前 8 位（`app/core/workspace.py::workspace_dir_name`），
哈希保证不同 `thread_id` 一定落到不同目录。

绑定失效（目录被人删了、仓库被移走）时**退回会话工作区**，不让整个会话因为一个
失效的绑定瘫掉。

### 3.3 能力清单按绑定情况裁剪

`_visible_specs()`：没绑工作目录（agent 目录 == 会话目录）时，把
`delegate_worker` 与所有 `git_*` 能力从清单里剔掉。

**为什么**：那些能力只在 git 仓库里才有意义。给模型一批它用不了的工具，它会
先试一遍再报错，白烧轮次。提示词也跟着裁 —— `prompts.py::base_prompt_for()`
只在真的还有 git / 派活能力时才把 `COLLABORATION_PROMPT` 拼进去。

两处必须一起裁。只裁工具不裁提示词，模型照着提示词教的方法去调不存在的工具；
只裁提示词不裁工具，模型还能看见工具说明。

### 3.4 会话锁：同一会话不许有两轮在跑

`turn_lock(thread_id)` 是一个**进程内**的 `asyncio.Lock`，按会话索引，
默认等 30 秒（`agents/turn_lock.py::DEFAULT_TIMEOUT`）。

**为什么需要**：checkpoint 存在一份共享的 SQLite 文件里。同一 `thread_id` 上两个
请求并发跑，轻则互相覆盖（历史凭空少一段），重则撞上 SQLite 写锁。
审批恢复更麻烦 —— 图挂在 checkpoint 里等人批，这时候再来一轮对话会把那段历史搅乱。

**粒度按会话**，不是全局一把锁：不同会话本来就该能并行。

流式接口要特别小心：锁要**覆盖整个生成过程**，不是只覆盖第一个 `yield`。
所以 `AgentService.stream()` 在生成器里 acquire、在 `finally` 里 release ——
客户端中途断开时生成器被关闭，锁照样还回去。

**锁不清理**：某个请求还持有旧锁时把它删掉，下一个请求会拿到**新锁**，两个请求
都认为自己独占，互斥就失效了。

只读接口（`history` / `pending` / `status`）**不持锁**。

拿不到锁时抛 `TimeoutError`（消息是「会话 xxx 正忙（另一轮还没跑完），稍后再试」）。
它不是 `AppError`，走的是框架兜底的 500；在 SSE 路径上它发生在第一个 `yield`
之前，客户端拿到的是一个立刻断开的 200 流，前端会显示「连接中断」。

### 3.5 checkpointer：文件，每请求开一次

`open_checkpointer()`（`agents/checkpointer.py`）是上下文管理器，每次请求打开一次
`AsyncSqliteSaver`、用完就关。数据存在 `settings.agent_checkpointer_db`
（默认 `./agent_checkpoints.db`）。

**必须是文件**：审批恢复是**另一次 HTTP 请求**，两次请求之间进程可能已经重启、
也可能落在另一个 worker 上。内存里的 saver 撑不住。

**连接不能跨请求复用**（SQLite 连接不适合长持有），但数据是持久的。

checkpointer **不编进图里**。图要跨请求复用，而 checkpointer 是每请求新开的连接
（用完就关），编进去的话复用时会拿着上个请求那个已经关掉的连接写 checkpoint。
它靠配置里的 `CONFIG_KEY_CHECKPOINTER`（`__pregel_checkpointer`）在调用时注入 ——
这个键名是 langgraph 的内部常量，代码里用注释盯着它。

### 3.6 图的组装顺序

`agents/builder.py::build_agent()` 的顺序是**硬要求**：

```text
1. context.approval_handled = True   ← 必须最先
2. build_tools(...)                   ← 工具构造时就把这个标记读进去了
3. 能力名查重                         ← 同名直接报错，不让 LangGraph 静默覆盖
4. CapabilityMiddleware + TodoListMiddleware
5. DynamicContextMiddleware           ← 插到列表最前（最外层）
6. build_backend(...)
7. 压缩中间件                          ← 同名替换 deepAgents 默认那份
8. QuotaSkillsMiddleware              ← 有技能时才加
9. MemoryMiddleware                   ← 同名替换框架默认那份
10. _interrupt_on(...)                ← 声明级审批闸
11. _general_subagent(...)            ← 子代理显式挂同一套治理
12. create_deep_agent(...)
```

第 1 步为什么必须最先：**审批已经由 deepAgents 的 HITL 负责**，工具能跑到执行层
就说明人已经批过了。执行层如果再问一遍，就出现「批准了等于没批准」。而工具构造时
就把「要不要自己审批」固化进参数里了 —— 所以标记必须在建工具**之前**设上。

第 5 步插到最前：列表里越靠前越外层。先填占位符，治理中间件追加策略说明时看到的
就是填好的提示词。

三处**同名替换**（deepAgents 按 `name` 替换默认中间件）：

- 压缩中间件 → `PlatformSummarizationMiddleware`（见 §3.13）；
- 记忆中间件 → `MemoryMiddleware`，`system_prompt` 换成只有 `{agent_memory}` 的
  `WORKSPACE_RULES_PROMPT`。框架默认那份有四千多字符（每次请求都带），而且教模型
  「用 `edit_file` 更新记忆」，跟我们自己的 `memory_write` 规则直接打架；
- `write_todos` 的说明换成 `TODO_TOOL_DESCRIPTION`（框架默认三千八百多字符）。

**名字对不上就变成"多挂了一个"**，两份中间件同时跑。这三处依赖 deepAgents 的同名
替换约定，升级框架时会静默失效。

### 3.7 审批的两级与「闸的位置不能变」

审批的两级语义与判定规则归能力模块（`app/capabilities/registration/policy.py`），
本模块只关心**怎么挂起与恢复**：

| 级 | 挂起点 | 载荷形状 |
| --- | --- | --- |
| 声明级 | 工具执行**之前**（deepAgents 的 HITL，`interrupt_on`） | `{"action_requests": [...]}` |
| 参数级 | 工具执行**中**（我们自己的 `interrupt()`） | `{"kind": "param_risk", "tool": ..., "arguments": ...}` |

`runner.py::_pending_entries()` 把两种中断归一成同一种条目，前端拿到一个统一的
待批列表。**一个挂起的调用一条** —— 模型一轮里可以并行发好几个工具调用，它们会
一起挂起。

**闸挂在哪些工具上，不能随权限模式变**（2026-11-05 修过的一个真 bug）：

- 结构固定：`builder.py::_interrupt_on()` 永远挂全部**候选**工具
  （`tool_policy.approval_candidate_tools()`）；
- 拦不拦运行时判：`when` 谓词 → `_needs_approval_now()` → 每次都重读
  `resolve_permission_mode(thread_id)`；
- 没有待批的可批时**报错**（`ConflictError`，409），不静默空转。

变了的场合：挂起期间用户把模式切到「绕过权限」，图被重建成没有闸的样子，
checkpoint 里那条挂起的中断就"看不见"了，恢复变成空转 —— 接口返回 200，
工具根本没跑，界面表现是「审批后无法继续」。

恢复时谓词**会再跑一次**：挂起期间把模式改成绕过权限，恢复时谓词返回 False，
工具直接跑 —— 这正是「绕过权限就是不问人」的意思。

### 3.8 权限模式在本模块里的语义

模式存**文件**（`<workspace_root>/.qzd/permissions/<会话目录名>.json`），不是表 ——
审批恢复可能落在另一个进程上，文件跨进程可见。单一事实来源是
`app/agents/permissions.py::resolve_permission_mode(thread_id)`。

五个模式（完整语义见能力模块）：

| 模式 | 对话里表现为什么 |
| --- | --- |
| `auto`（默认） | 低档（只读 / 写入）自动放行；执行命令、联网、破坏性要人批 |
| `manual` | 任何会改东西的操作都要人批，只读不问 |
| `accept_edits` | 文件编辑与命令自动放行，只有破坏性要人批 |
| `plan` | 只读：写类与执行类工具**不暴露给模型** |
| `bypass` | 全放行（含破坏性），受进程级总闸 `capability_allow_bypass` 约束 |

本模块特有的三条语义：

1. **切换后下一轮生效。** 模式进了图缓存键（`_graph_key` 里的 `trust_mode`），
   模式一变图就重建。
2. **当前挂起的那次调用还是要点批准。** 模式决定的是「下一次要不要拦」，
   已经挂起的那条中断必须由人给出决定 —— 否则 HITL 的「决定数 = 挂起数」对不上。
   但如果挂起期间切到 `bypass`，恢复时谓词返回 False，工具直接跑。
3. **模式存文件，跨进程可见。** 后端返回的 `SessionPermissionOut` 里
   `session_override` 表示这是不是会话自己设的（不是全局默认），
   `allow_bypass` 表示平台有没有开总闸 —— 前端据此把「绕过权限」标成不可选，
   别让用户点了才被拒。

`"auto"` 是**显式写出来的，不是"恢复默认"**。全局配置可能是 `bypass`，那时候把
会话设成 `auto` 必须是「按风险档位」，而不是「跟随全局」。

### 3.9 SSE 帧格式

`POST /agent/stream` 与 `POST /agent/{thread_id}/regenerate` 帧格式完全一致：

```text
data: {"type":"text","text":"增量"}\n\n
data: {"type":"tool_call","tool_call":{...}}\n\n
data: {"type":"tool_result","tool_result":{...}}\n\n
data: {"type":"pending_approval","pending":[...]}\n\n
data: {"type":"error","error":"..."}\n\n
data: {"type":"done","status":"completed"}\n\n
data: [DONE]\n\n
```

一帧一种事件，`type` 决定它是什么：

| type | 载荷 | 前端拿它做什么 |
| --- | --- | --- |
| `text` | `text` | 打字机渲染 |
| `tool_call` | `tool_call` | 工具折叠块亮起 |
| `tool_result` | `tool_result` | 工具折叠块补成完成态 |
| `pending_approval` | `pending` | 弹审批卡片 |
| `error` | `error` | 这一轮炸了，把原因显示出来 |
| `done` | `status` | 收尾 |

**SSE 一旦开始推数据就改不了 HTTP 状态码了**，所以异常也走 `error` 帧 ——
而且必须走 `error` 帧再走一帧 `done`。`_stream_locked()` 里连 `AppError` 也这么处理
（供应商配置不存在、已停用这些是"用户请求不对"，但它们照样发生在流已经开始之后；
直接抛出去的话连接断掉，前端既收不到 error 帧也收不到 done 帧，界面上只剩一个
空气泡）。

`tool_result.summary` 是**截断后的正文**（`TOOL_RESULT_SUMMARY_MAX = 400` 字符）。
工具输出可能有几万 token（读了整个仓库），原样塞进 SSE 会把前端撑爆；要全文去
查调用记录。

图的流式读取开了**两个模式**：`stream_mode=["messages", "updates"]`。
`messages` 只给文本（打字机），工具调用在节点的 state 更新里（`updates`）。
两个一起开才既看得到打字机、又看得到工具块。

### 3.10 待批清单与恢复：决定数必须等于挂起数

`GET /{thread_id}/pending` 的数据源是 LangGraph 的 checkpoint（`aget_state` 的
`interrupts`）—— **图挂在哪儿只有它知道**，不在调用记录表里。

恢复用 deepAgents 约定的载荷：

```python
Command(resume={"decisions": [{"type": "approve"}]})   # 或 "reject"
```

**决定数 = 挂起的调用数**。模型一轮里并行发好几个工具调用时它们会一起挂起，
HITL 会校验这个数：少给一个报
`Number of human decisions (1) does not match number of hanging tool calls (2)`，
整轮直接失败。

`runner.py::_build_resume_value()` 负责把逐条决定切回每个中断：

- 待批清单是按「中断顺序 → 该中断里的条目顺序」摊平的，切片时用同样的顺序；
- **多个挂起中断时必须按 interrupt id 分别给值** —— LangGraph 看到不止一个待答
  中断就会要求 "you must specify the interrupt id when resuming"；
- 但同一个节点里的多个 interrupt 共享同一个 id，这时按 id 给值会把决定发到同一个
  地方去，退回「一刀切」的写法。

恢复这一轮**没有新的用户输入**。记忆目录仍然要带 —— agent 还在接着做同一件事，
没有目录等于凭空丢了记忆。检索词取会话里最后一条用户消息，
**直接从 checkpoint 里读，不用为此先建一张图**。

### 3.11 斜杠命令

清单在后端（`agents/commands.py::COMMANDS`），12 条，由
`GET /api/v1/agent/commands` 下发。**前端只渲染，不硬编码** —— 后端加了命令，
前端自动就有。

| 类别 | 条数 | 行为 |
| --- | --- | --- |
| `prompt` | 7（help / plan / files / capabilities / skills / mcp / status） | 把 `template` 填进输入框，**用户改完再发** |
| `action` | 5（model / mention / new / clear / rename） | 前端本地执行，**不进对话** |

`prompt` 类用模板而不是直接执行，是因为带上下文的任务里"方案是什么"取决于用户
接下来补什么。

`action` 类的 `new` / `clear` / `rename` 由 `ChatPage.tsx::runCommandAction` 处理。

### 3.12 子代理：worker

`agents/worker.py::run_worker()` 派 worker 时给它**自己的 git worktree + 自己的
分支**，容器只挂那个 worktree。

worker 与主 agent 的三处不同：

| | 主 agent | worker |
| --- | --- | --- |
| 工作目录 | 会话绑定的环境 | 自己的 worktree |
| 人工审批 | 有（前端点批准） | **无**（`allow_interrupt=False`） |
| 历史 | 有 checkpointer，多轮 | 无（`checkpointer=None`），一次性执行 |

**递归到此为止**：worker 的能力清单里没有 `delegate_worker` 本身，深度封顶两层。

**worker 不能碰的 git 能力**（`WORKER_BLOCKED_GIT_KEYS`）：`git_worktree_add`、
`git_worktree_remove`、`git_merge`。主 agent 走人工审批才放行它们，但 worker 跑在
permissive 上且没有界面 —— 那三个到了 worker 手里就是**无人拦的破坏性操作**
（worker 能删掉同仓库**别的 worker** 的 worktree，能把别的分支的 ref 往前推）。
worker 只留 `git_status` 与 `git_commit`，正是它提示词里教的那两个。

**沙箱里敲不了 git**：容器只挂载 worktree，里面的 `.git` 是指向主仓库的指针文件，
容器里解析不了。要看状态用 `git_status`，要提交用 `git_commit` —— 平台在宿主机上
代做。worker 自己没提交时，平台还会替它补一次提交（脏工作区代提交）。

worker 的分支**不会自动合回主线**，由人决定。

### 3.13 上下文压缩

`context/compaction.py::PlatformSummarizationMiddleware` 继承 **deepAgents 版**
（不是 LangChain 版），按 name 同名替换默认那份。

差别正好是压缩最要紧的两件事：

- **被压缩的历史**：LangChain 版**直接丢**（`RemoveMessage` 重写 state）；
  deepAgents 版先**外置成文件**（`{artifacts_root}/conversation_history/{id}.md`），
  摘要里带上这个路径，agent 能用 `read_file` 回去翻；
- **改 state 的方式**：LangChain 版在 `before_model` 里改写；deepAgents 版在
  `wrap_model_call` 里记一个私有事件，`state["messages"]` **原样不动**。

改了三处：

| 参数 | 默认 | 改成 | 为什么 |
| --- | --- | --- | --- |
| `model` | 主模型 | 可配的独立模型 | 压缩是纯信息搬运，用旗舰模型不划算 |
| `trigger` | 看 profile | 按窗口比例 | 框架那份是固定的「170000 token / 6 条」 |
| `keep` | 比例 0.1 | 比例 0.1（`KEEP_FRACTION`）；**窗口未知时**退回绝对值，保留 20 条 | 6 条对代码 agent 太少 |

**`trim_tokens_to_summarize=None`** —— 框架默认只送最后 4000 token 进摘要，
更早的那部分**既不进摘要也不被保留**，直接消失。设成 `None` = 全量送进摘要。

**两个阈值，不是一个**：框架只有一个阈值，可它要同时跟两种度量比 ——
`token_counter(messages)` 只算对话，`usage_metadata["total_tokens"]` 是整个请求。
所以 `_should_summarize()` 拆成两个：对话超过「对话的份额」就压缩，整个请求超过
0.85 窗口也压缩。

**`_fix_artifacts_root()` 是硬要求**：deepAgents 算 `artifacts_root` 用的是
`isinstance(backend, CompositeBackend)`，而我们传进去的 `PlatformBackend` 只是
包着 CompositeBackend，不是它的子类，于是判定为 False，产物目录退化成 `"/"`。
那个 `"/"` 落在用户的工作区里 —— 被压缩掉的对话历史（含用户说过的话、agent 读到的
代码）会明晃晃地落进用户的仓库，可能被 `git commit` 一起提交上去。所以这里显式
把它指回 `/.artifacts/`（挂在工作区外面）。

### 3.14 图缓存

建一张图不便宜：十几个工具、十来个中间件，再加一次编译，实测 ~100ms。同一个
会话每轮都要建一次，而这一轮里其实什么都没变（工具集、技能、后端、权限模式一样），
变的只有提示词里的两块内容 —— 那两块已经挪到调模型时才注（见 §3.16）。

`agents/graph_cache.py::GRAPH_CACHE` 是进程内的 LRU，**最多 16 张**。

**缓存键必须盖住所有影响图行为的东西**（`runner.py::_graph_key`）：

| 键 | 漏掉的后果 |
| --- | --- |
| `thread_id` / `workspace` / `session_root` | 文件工具指着别的目录 |
| `trust_mode` | 用户刚收紧的闸门又被打开 |
| `model_identity(model)` | 换了供应商还在用旧的 |
| 能力清单（key / kind / enabled / risk / source_type） | 新装的能力模型看不见 |
| `skill_sources` / `memory_source` | 技能与记忆文件换了图还用旧的 |
| 提示词规则（id + updated_at） | 复盘刚学到的准则看不见 |
| 沙箱配置 / MCP 通道 | agent 连的是老地方 |

漏掉一项，后果不是「慢一点」，而是「用错了规则」。

**复用时必须刷新执行上下文**（`GraphEntry.refresh()`）：执行上下文里混着两类东西 ——
跟会话走的（工作区、信任模式，变了就该重建）与跟请求走的（数据库会话、能力清单、
模型、MCP 通道）。只换后者，不换就会往**上一次请求那个已经关掉的数据库会话**里
写调用记录。

只读操作（读历史、看待批清单）用 `attach=False` —— 不接管这张图的执行上下文。

缓存是**进程内**的：多进程部署时每个进程各有一份，谁也不用等谁 ——
checkpoint 在文件里，图重建也就多花 100ms。

### 3.15 每轮变化的三块提示词

系统提示词里有三块内容每轮都不一样：记忆目录、上传件清单、反思记录。它们编进图里
图就不能跨轮复用 —— 复用等于让 agent 一直看着第一轮的旧记忆。

所以编译期只留**占位符**（`dynamic_context.py`）：

| 占位符 | 内容 |
| --- | --- |
| `<!--qzd:memory-catalog-->` | 按本轮这句话召回的记忆目录 |
| `<!--qzd:uploads-->` | 本轮用户传了哪些文件 |
| `<!--qzd:reflection-->` | 本会话最近几次没成功的调用 |

真正的内容在 `_fill_holder()` 里填（`runner.py`），填进 `PromptHolder`，
由 `DynamicContextMiddleware.awrap_model_call` 在调模型时替换。

**为什么是替换占位符，不是追加到末尾**：上下文模块是按优先级装箱的，追加会打乱
顺序。占位符让装配器照常排位，中间件只换内容。代价是装配器算预算时看到的是
占位符的长度（几十个字符），不是真实目录的长度 —— 这块的溢出风险由记忆模块自己
管召回条数与预算兜着。

空块直接丢掉 —— 这一轮没有记忆、没传附件时，留着空块就是提示词里几行空行。

### 3.16 前端：这一轮正在发生什么

**状态行**（`StatusLine.tsx` + `turnStatus.ts`）—— 助手正在输出时，正文**开头**
领着一行：

```text
🍀   7s · ~275 tokens · 思考中
```

四叶草呼吸动画（橙色 `--clover`），计时器每 250ms 刷一次，且**挂在组件自己身上**
—— 只重渲染这一行，不带着整条消息的 Markdown 一起重渲染。

阶段由事件流**推**出来，不靠猜（`TurnPhase = 'thinking' | 'writing' | 'tool'`）：
来了文本就是在写，来了工具调用就是在跑工具，一帧都没有就是在想。
**没有「等待审批」这一档** —— 审批卡片自己就写着「需要你批准」；等批的时候这一轮
马上就结束，状态行会跟着消失。

token 数是**估算**（4 字符约 1 token），口径与后端 `status.py::estimate_tokens`
一致 —— 两边算法不一样的话，状态行上的数字会跟右侧状态面板对不上。

**工具调用块**（`ToolCallBlock.tsx`）折叠时一行：图标 + 人话标签 + 目标 + 改动量 +
耗时 + 状态点。人话标签与目标由 `toolSummary.ts` 算（纯函数，可单测）。
`pending` 状态（历史回填专用）标成灰点并写「未完成」—— **没有结果的调用不是
「成功」**，标成完成是在骗人。

**审批卡片**（`ApprovalCard.tsx`）直接弹在对话里，挂在**最后一条助手消息**上
（挂到"最后一条消息"是不行的：最后一条可能是工具消息，卡片就不见了）。
多个调用时逐条给「批准 / 拒绝」开关，默认全批准。批过的卡片换成一句交代
（"已批准，agent 继续执行中…"），因为后端 resume 是同步跑完整张图的，
可能几十秒，只把按钮置灰用户会以为页面卡死了。

**上下文占用指示器**在输入框底栏、模型选择左边，点开看各部分占比
（`features/sessions/ContextUsageIndicator.tsx`）。**是估算**（4 字符约 1 token），
不是精确 token 数。压缩缓冲是**预留**不是占用 —— 算成占用的话，一个刚开的会话
就显示 15%，看着像已经用掉了一块。

**批准 / 拒绝之后要重读历史**（`useChat.ts::approve`）—— 图恢复后 agent 可能又
撞上新的审批，只关当前这张卡会丢掉新的待批，页面看着像卡住了。失败时也要重读
历史：卡片可能是旧的（已经批过一遍，或者权限模式变了导致那张图不再挂着它）。

### 3.17 重新生成、分支、清空、彻底删除

**重新生成**（`POST /{thread_id}/regenerate`）：

- 怎么删的：LangGraph 的 `update_state` 支持 `RemoveMessage` —— 按消息 id 把它们
  从 checkpoint 的历史里摘掉。摘掉**最后一条用户消息及其之后的所有消息**
  （助手回复 + 工具消息），然后拿那条用户消息重新跑。
- **为什么连用户消息一起删**：留着它再发一遍，历史里就会出现两条一样的用户消息。
- 帧格式与 `/stream` 完全一致，前端不用为它单开一条路。
- 前端也得跟着删 —— 不删的话屏幕上留着旧回复，新回复又追加在后面，看着像两轮。
  但**用户消息要保留**（后端是拿那条用户消息重跑的，它还在历史里）。

**分支**（`POST /{thread_id}/fork`）：

- **不搬 checkpoint** —— 新会话直接 `update_state` 播种：把原会话的消息列表切到
  分支点之前，写进新会话的第一个 checkpoint。比复制 checkpoint 表里的 blob 稳。
- **工作区也带过去** —— 只带对话不带文件的话，分支出来的会话是空的，agent
  看不见之前的代码。复制失败不算致命，新会话照样能聊，只是工作区是空的。
- 绑定了工作目录的话，新会话**继承同一个环境**。
- 分支失败会**回滚**刚建出来的目录与绑定 —— 不撤的话会留下孤儿目录与孤儿绑定：
  会话根本不存在，但它的目录还在、绑定还在，文件接口还能读到。

**清空**（`DELETE /{thread_id}`）删的是 checkpoint 里的历史消息 + 会话级权限模式。
**工作区里的文件不删** —— 上传件和产物都还在，下一轮 agent 照样能用。

**彻底删除**（`DELETE /{thread_id}/session`）抹掉全部痕迹：checkpoint、会话内短期
记忆、会话级权限模式、环境绑定（只删绑定关系，**不删用户的仓库**）、会话工作区
目录（上传件、技能导出、产物一并没了）。返回体列出清理了哪些东西。

## 4. 已知限制

- **平台无鉴权。** 所有接口都不校验身份：知道 `thread_id` 就能读写别人的会话、
  删别人的会话、删别人的工作区文件。`DELETE /api/v1/agent/{thread_id}/session`
  能把别人的会话连工作区文件一起删掉。鉴权补上之前不要暴露到公网。
- **worker 里的破坏性操作无人审批。** worker 没有界面，挂起就永远等不到人批，
  所以 `allow_interrupt=False`。唯一防线是物理隔离：独立 worktree + 断网容器 +
  收窄掉三个破坏性 git 能力。主 agent 那条路上的审批对 worker 不起作用。
- **待批载荷明文落库。** `invocation.payload`（JSON 列）里存着工具的调用参数。
  一条 `rm -rf` 的完整命令就在里面，谁有数据库读权限谁能看到。
- **已挂掉的老会话救不回来。** 决定数与挂起数对不上时，LangGraph 会把错误的恢复值
  写进 checkpoint —— 之后再批准多少次都还是它。唯一出路是删掉这一轮重来
  （界面上是「重新生成」）。`_readable_error()` 里专门给这个情况写了一句用户提示。
- **上下文占用是估算，不是精确 token 数。** 4 字符约 1 token
  （`agents/status.py::CHARS_PER_TOKEN`）。不同厂商的分词器不一样，
  面板上要写清楚，别拿它当精确值看。
- **任务状态只在进程内存。** `agents/tasks.py::REGISTRY` 是模块级单例，
  进程重启就清空，多 worker 部署下每个 worker 只看得见自己那份。
  它是给用户看的实时视图，不是审计记录 —— 要审计级别的看 `invocation` 表。
- **turn_lock 是进程内锁。** 多副本部署时每个副本各管各的，跨副本的并发要靠
  数据库层的隔离。
- **压缩中间件的接管依赖 deepAgents 的同名替换约定。** 名字对不上就变成「多挂
  了一个」，两份摘要中间件同时跑，谁先谁后说不清；升级框架时会**静默失效**。
  `build_compaction_middleware()` 里有一道自检（名字不对就记 error 日志）。
- **`_fix_artifacts_root()` 现在是兜底，不是必需。** 产物目录的落点由框架用
  `isinstance(backend, CompositeBackend)` 判定 —— `PlatformBackend` **是**
  `CompositeBackend` 的子类，所以判定为真，落点是 `/.artifacts/`（工作区外面）。
  `_fix_artifacts_root()` 只是万一框架换了判定方式时的保险。
  **改 `PlatformBackend` 的继承关系时必须一起看这里** —— 那会让被压缩的对话
  历史落进用户仓库。
- **图的缓存键是手写的。** 新增一个影响图行为的东西（比如又一种沙箱配置）却忘了
  加进 `_graph_key`，后果不是"慢一点"，而是"用错了规则"，而且不报错。
- **历史里的工具结果靠特征识别失败。** deepAgents 的文件工具与 `execute` 失败时
  **不抛异常**，只是把 `Error: ...` 或 stderr 写进结果里。前端
  `toolSummary.ts::isFailedResult` 靠前缀与几条 shell 错误特征来判，
  认不出的失败会显示成绿勾。
- **重新生成会重复执行有副作用的工具。** `regenerate()` 用 `RemoveMessage` 从
  checkpoint 里摘掉最后一条用户消息及其之后的消息，**但撤不消已经发生的副作用**
  —— 文件写入、沙箱命令、外部调用都留下了。重跑同一轮 = 第二次执行 `write_file`、
  第二次 `execute`。当前**没有**"这一轮已产生副作用"的检查。
- **分支可能切出"有 tool_calls 但没有结果"的历史。** `fork()` 按消息 id 定位
  分支点，取 `messages[: index + 1]`。如果分支点正好落在一个带 `tool_calls`
  的 AIMessage 上，而对应的 ToolMessage 被裁在边界之后，新会话的第一次请求
  就会带着一条**有发起、无结果**的 AIMessage —— 兼容端点会 400。
- **模型调用层没有重试与回退。** `app/agents/llm.py` 建模型时不传 `max_retries`，
  全仓也没有任何重试中间件 —— 一次网络抖动或厂商 5xx 就是整轮失败，
  模型换不动。框架的 `ModelRetryMiddleware` / `ModelFallbackMiddleware` 能补，
  但回退要先有多厂商的 key（平台现在是单模型单厂商）。
- **派子 agent 会阻塞主 agent。** `delegate_worker` 是 `await agent.ainvoke` ——
  worker 跑完，这一轮的工具调用才返回。派几个 worker 就串行等几个。
  框架的 `AsyncSubAgentMiddleware` 能后台跑，但它那套是给**远程 Agent Protocol
  服务器**上的子代理用的，平台的 worker 是进程内现造的 deep agent，接不上。
- **没有"打分到满意为止"的质量闭环。** 失败会记进反思块、会触发复盘
  （见自我进化模块），但没有 grader 给这一轮的产出打分并驱动重做。
  框架的 `RubricMiddleware` 能做这件事，但它的 grader 是另一个模型调用 ——
  那次调用目前不在能力清单里，不声明也不留痕。

## 5. 相关文件

### 后端

| 文件 | 负责什么 |
| --- | --- |
| `backend/app/api/v1/agent.py` | 全部 HTTP 路由（14 个接口）与响应装配 |
| `backend/app/services/agent.py` | 业务入口：工作区解析、会话锁、检查点生命周期、模型选择、能力裁剪、审批恢复、分支回滚 |
| `backend/app/schemas/agent.py` | 请求 / 响应模型（`AgentChatRequest`、`AgentStreamChunk`、`PendingApprovalOut`、`SessionStatusOut` 等） |
| `backend/app/agents/runner.py` | 一轮对话的编排：`run` / `stream` / `resume` / `regenerate` / `fork` / `history` / `pending_approvals`，结果与中断的整理，图缓存键 |
| `backend/app/agents/builder.py` | 组装 deep agent：中间件顺序、审批闸、子代理规格、提示词替换件 |
| `backend/app/agents/prompts.py` | 基准提示词（工作准则 11 条）、协作说明、自动积累规则的渲染 |
| `backend/app/agents/permissions.py` | 会话级权限模式的读 / 写 / 清（存文件） |
| `backend/app/agents/commands.py` | 斜杠命令清单（12 条，`prompt` 7 + `action` 5） |
| `backend/app/agents/dynamic_context.py` | 三块每轮变化的提示词：占位符、`PromptHolder`、替换中间件 |
| `backend/app/agents/graph_cache.py` | 编译好的图的进程内 LRU 缓存（16 张）与上下文刷新 |
| `backend/app/agents/turn_lock.py` | 会话级轮次锁（进程内 `asyncio.Lock`，30 秒超时） |
| `backend/app/agents/checkpointer.py` | `open_checkpointer()`：每请求开一次 `AsyncSqliteSaver` |
| `backend/app/agents/worker.py` | 子代理 worker：独立 worktree、独立分支、能力收窄、宿主代提交 |
| `backend/app/agents/status.py` | 会话状态的各块：上下文占用估算、产出、协作、连接器 |
| `backend/app/agents/tasks.py` | 正在跑的工具调用的登记簿（进程内存） |
| `backend/app/agents/llm.py` | 模型构造、`supports_vision`、`model_identity`、上下文窗口挂载 |
| `backend/app/agents/providers.py` | 供应商配置的模型接入 |
| `backend/app/agents/reflection.py` | 反思记录（最近失败）—— 每轮注入提示词的那一块 |
| `backend/app/agents/autonomy.py` | 复盘链路的纯逻辑：组输入、解析模型给的改进提议、范围守卫 |
| `backend/app/context/compaction.py` | 压缩中间件（接管 deepAgents 那份）、阈值、历史外置路径修正 |

### 前端

| 文件 | 负责什么 |
| --- | --- |
| `frontend/src/features/agent/ChatPage.tsx` | 页面骨架、斜杠命令的本地动作、右侧状态面板 |
| `frontend/src/features/agent/useChat.ts` | 聊天状态机：事件流 → 消息列表、轮次代号、历史回填、审批后的重读 |
| `frontend/src/features/agent/MessageList.tsx` | 消息列表；每行 `memo`；只渲染最近一段 |
| `frontend/src/features/agent/StatusLine.tsx` | 状态行组件（四叶草 + 耗时 + token + 阶段） |
| `frontend/src/features/agent/turnStatus.ts` | 状态行的纯函数：阶段文案、耗时格式化、token 估算 |
| `frontend/src/features/agent/toolSummary.ts` | 工具名 → 人话、参数里最能说明问题的那一项、失败特征识别 |
| `frontend/src/features/agent/ToolCallBlock.tsx` | 工具调用卡片（折叠一行 / 展开参数与结果） |
| `frontend/src/features/agent/ApprovalCard.tsx` | 审批卡片：逐条开关、风险档位、调用参数 |
| `frontend/src/features/agent/Composer.tsx` | 输入框：附件、斜杠命令、@ 提及、权限模式、模型选择、上下文指示器 |
| `frontend/src/features/agent/PermissionModePicker.tsx` | 权限模式选择器（五个模式、bypass 总闸与二次确认） |
| `frontend/src/features/agent/permissionModes.ts` | 权限模式清单与中文标签（纯数据，可单测） |
| `frontend/src/features/agent/SlashCommandMenu.tsx` | 斜杠命令面板 |
| `frontend/src/features/agent/textBatcher.ts` | 文本攒批器（一个 token 一帧，攒着一起上） |
| `frontend/src/features/agent/messageWindow.ts` | 长会话只渲染最近一段的窗口计算 |
| `frontend/src/features/sessions/ContextUsageIndicator.tsx` | 上下文占用指示器（输入框底栏） |
| `frontend/src/api/resources.ts` | 后端接口的前端封装（`streamChat` / `approve` / `history` 等） |
| `frontend/src/api/sse.ts` | SSE 帧读取器；`StreamInterruptedError` 区分「断流」与「后端报错」 |

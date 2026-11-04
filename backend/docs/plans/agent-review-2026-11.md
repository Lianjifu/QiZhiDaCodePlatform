# agent 功能审查与改进方案

> 状态：审查报告（2026-11-04）
>
> 范围：`backend/app/{agents,capabilities,services,api,context,memory,environments}/**`、
> `frontend/src/features/agent/**`、相关测试与文档。
>
> 方法：四个方向并行审查（后端编排核心 / 能力治理 / 前端交互 / 接口与记忆上下文），
> 每一条结论都回到代码里复核过；带「已复核」标记的是实测确认的。

---

## 一、总体判断

这套 agent 的**治理骨架是对的**，不要动：

- `policy.py::approval_decision` 是唯一的审批判定出口，
  `builder.py` / `middleware.py` / `pipeline.py` 三处共用它 —— 这是本仓库最好的一块设计；
- 风险「只能上调不能下调」（`policy.py::max_risk`、`risk.py::assess`）落实得干净；
- 每条调用记录自己提交（`pipeline.py`），不会因用户中断而回滚留痕；
- `worker.py` 显式传 `thread_id` 与 `recursion_limit` 并写清原因 —— 救过人的注释。

问题**不在骨架，在接缝**。同一个语义（审批口径、风险档位、会话生命周期、
权限模式）在三四个地方各算一遍，各算一遍就各错一遍。下面的问题绝大多数是
「同一个判断有两份实现」造成的。

按修复收益排序，最该先做的是 **P0 的四条** —— 它们都能造成「用户以为被拦住了，
实际没拦」或「界面显示的状态与后端真实状态不一致」，这类问题会直接误导人。

---

## 二、P0 —— 必须先修（会误导人 / 会真的漏拦）

### P0-1 参数级风险规则在 agent 路径上基本失效

**位置**：`app/capabilities/intent/middleware.py:235-237`、`app/capabilities/execution/pipeline.py:261`

参数级检查的第一句是：

```python
if name in self._approval_tool_names():
    # 声明级已经批过这一次 —— 参数级不再重复问
    return None
```

注释的前提是「声明级已经批过这一次」。但声明级批的是**这个工具**，不是**这次参数**。
在 auto 模式下 `execute` 与 `delete` 本身就在声明级审批名单里（已复核：
`approval_tool_names('auto') == ['delete', 'execute']`），于是：

- 模型发 `execute("rm -rf /")` → 走声明级审批 → 人看到的是「执行命令 · 中档」，
  批准后执行层因 `pre_approved=True` 直接放行 → **参数级的 destructive 判定从未发生**；
- `registration/builtin/__init__.py` 里承诺的「web_fetch 抓内网 URL 顶成 destructive、
  要管理员放行」在 agent 路径上**从未发生**。

实测（已复核）：`assess(execute, {"command": "rm -rf /"})` → `destructive` +
`require_admin=True`，规则本身是对的 —— 只是这条路径根本没走到。

**修法**：参数级检查不再按工具名豁免，改成按**本次调用**豁免 ——
只有当「声明级审批已经批准了**这一次调用的同一份参数**」时才跳过。
最省事的做法是让声明级的中断载荷带上参数指纹（`args` 的 hash），
参数级检查比对指纹而不是比对工具名。

### P0-2 `run_python` 的破坏性内容完全逃逸

**位置**：`app/capabilities/execution/risk.py:132,142`、`registration/builtin/sandbox.py:32`

两条独立的漏洞：

1. 参数级规则只匹配 `arguments.get("command")` 与 `arguments.get("url")`，
   而 `run_python` 的参数名是 **`code`** —— 源码里的破坏性特征一个都匹配不上；
2. `risk.py:126` 的 `annotations.destructive` 分支只在 `risk == RiskLevel.READ` 时生效，
   而 `run_python` 声明的是 `EXECUTE` → 注解失效。

实测（已复核）：`assess(run_python, {"code": "import os; os.system(\"rm -rf /\")"})`
→ `risk=execute, reasons=[], require_admin=False, threat=False` —— **完全没命中**。

**修法**：对 EXECUTE 档的能力，参数名匹配扩到 `code` / `script` / `source` / `program`；
`annotations.destructive` 的上调条件从「risk==READ」放宽为「risk < DESTRUCTIVE」。

### P0-3 `bypass` 模式两头都不对

**位置**：`app/capabilities/registration/policy.py:222-229`、`intent/middleware.py:235`

- **判定侧**：`bypass` 直接 `return APPROVAL_APPROVED`，
  `require_admin` / SSRF 判定在 API 路径上被完全跳过 ——
  `web_fetch("http://169.254.169.254/...")` 静默执行；
- **中间件侧**：`bypass` 模式下 `approval_tool_names` 是空集，
  于是**每个工具都会进参数级检查**，`rm -rf` 会命中 `threat=True` →
  `interrupt()` → **「绕过权限」模式下照样会挂起等人批**。

这两条方向相反，但都源于同一件事：bypass 没有区分「跳过人工确认」与「跳过安全判定」。

**修法**：拆成两个开关。
- 人工确认：bypass 时全部跳过（这是用户的本意）；
- 安全判定（内网 URL、破坏性命令）：**始终执行**，命中时落日志 + 在返回值里标注，
  bypass 也不执行。

### P0-4 会话级权限模式接口 = 完整的绕过链

**位置**：`app/api/v1/agent.py:191-211`

平台**没有鉴权**（这是用户主动推迟的决定，见 `docs` 与 README 顶部的风险段）。
在这个前提下，`POST /api/v1/agent/{thread_id}/permission` 允许任何人把任意会话设成
`bypass`，随后该会话的 `delete` / `git_merge` / `execute` 全部放行。

同样地，知道 `thread_id` 就能 `POST /agent/{thread_id}/approve` 替别人批准挂起的破坏性调用。

**修法**（不要求现在做鉴权）：
- `bypass` 需要一个**进程级开关**（比如 `ALLOW_BYPASS_PERMISSION=false`）才生效，
  默认关掉 —— 本地开发时用环境变量打开；
- 至少把「设置 bypass」这件事记进日志（谁、何时、哪个会话）。

> 这一条要写进 `backend/README.md` 顶部的风险段 —— 目前那里只说了「文件接口无鉴权」，
> 没提「权限模式接口能把自己提到 bypass」。

---

## 三、P1 —— 会话生命周期与状态一致性

### P1-1 记忆详情 / 删除接口对会话记忆永远 404

**位置**：`app/api/deps.py:81`、`app/api/v1/memory.py:84,99`

`get_memory_service` 构造的服务 `thread_id=""`，而 `memory/session_store.py:118,222`
按 `thread_id==""` 过滤 → 查不到任何行。`GET /memory/{id}` / `DELETE /memory/{id}`
对**会话内记忆必然 404**。

实测（已复核）：

```text
GET /api/v1/memory/s:2      → 404 记忆 s:2 不存在
GET /api/v1/memory?scope=session&thread_id=t-e2e-2 → 200，列出 s:1、s:2
```

列表走 `service.for_thread(thread_id)`（新造一个绑定会话的服务），详情走
`service`（`thread_id=""`）—— 两条路径不一致。

**修法**：详情/删除接口接 `thread_id` 查询参数，或按 id 前缀反查所属会话。

### P1-2 直接调 API 的路径不认会话权限模式

**位置**：`app/services/capability.py:100,118,161`、`app/services/invocation.py:107`

直接调能力用的是**全局** `settings.capability_trust_mode`，会话模式只在
`agents/runner.py:675` 生效。更要紧的是 `_build_context(workspace or settings.workspace_root)`
—— 直接调用落在**平台工作区根目录**（所有会话目录的父目录），沙箱挂载的是整个根。

**修法**：两条路径统一读 `resolve_permission_mode(thread_id)`；
`workspace` 缺省时落到会话目录而不是工作区根。

### P1-3 删除会话只清了 checkpoint

**位置**：`app/services/agent.py:523-536`

`DELETE /agent/{thread_id}` 只删 checkpoint 与权限文件。**绑定文件**
（`environments/store.py:114`）、工作区里的 `AGENTS.md` 记忆、短期记忆（DB）、
工作区文件都不清 —— 而平台没有「删除会话」接口，这些副作用永远无人回收。

**修法**：加一个真正的会话删除端点，一次清掉：checkpoint、权限文件、绑定文件、
短期记忆、工作区目录。

### P1-4 fork 失败会留下孤儿目录与孤儿绑定

**位置**：`app/services/agent.py:587-592`

先 `_copy_workspace` + `_inherit_binding`（建目录、写绑定文件），**再**进 `try` 做
checkpoint。中途抛错就留下孤儿目录与孤儿绑定。

**修法**：把复制/继承挪进 `try`，失败时回滚（删目录 + 删绑定文件）。

### P1-5 同一会话的并发请求没有 turn 级锁

**位置**：`app/services/agent.py:328-379`、`app/agents/checkpointer.py:48`

同一 `thread_id` 的两个 chat/stream 没有互斥；checkpointer 是共享 SQLite 文件，
同会话两个请求并发写 checkpoint 会互相覆盖或报锁。
`core/locking.py` 的 `TaskLock` 每个请求新建一把，只护单请求内的并行工具。

**修法**：`AgentService` 里加一个按 `thread_id` 的 asyncio 锁（进程内即可，
和 B 类待办里「并发配额是进程内」的现状一致）。

### P1-6 `auto` 模式没法显式设

**位置**：`app/agents/permissions.py:84-87`

`set_permission_mode(thread_id, "auto")` 走 `clear_permission_mode` 分支 → 返回全局默认。
但 `auto` 是 `PermissionMode` 的合法值。后果：**全局配置是 `bypass` 时**，
用户在会话里选「自动（按风险档位）」会**静默拿到 bypass** —— 比用户请求的更宽松。

**修法**：只有 `None` / `"default"` 才清除覆盖；`"auto"` 显式写进文件。

### P1-7 审批只能全批或全拒，不能逐条拒绝

**位置**：`app/api/v1/agent.py:145-165`、`app/agents/runner.py:299`

`approve`/`reject` 无请求体；`_required_decisions` 取挂起数最大值 →
一轮里多个并行调用只能一次全批或全拒。

**修法**：`approve` 接受 `{"decisions": ["approve","reject",...]}`（按挂起顺序），
前端 `ApprovalCard` 在 N>1 时给出逐条勾选。

### P1-8 regenerate 会重复执行有副作用的工具

**位置**：`app/agents/runner.py:462-479`

`aupdate_state` 只删 checkpoint 里的**消息**，不撤销已经发生的文件写入 / 沙箱命令 /
外部调用。删消息后重跑同一轮 = 第二次执行 `write_file`、第二次 `execute`。

**修法**：regenerate 前检查本轮是否有已成功的调用记录，有则明确提示
「该轮已产生副作用，重跑会重复执行」，或只允许对纯读轮次重生成。

### P1-9 fork 复制的是「有 tool_calls 但没有 ToolMessage」的历史

**位置**：`app/agents/runner.py:512-526`

`kept = messages[:index+1]` 是整段历史，含 AIMessage 的 `tool_calls`，
但对应的 ToolMessage 可能在边界之后被裁掉。新会话第一次请求模型时，
历史里存在**有 tool_calls 但无对应结果**的 AIMessage → 兼容端点会 400。

**修法**：裁剪时回退到「最后一个完整的 AI+Tool 配对」，或给含 tool_calls 的
AIMessage 补一条占位 ToolMessage。

---

## 四、P2 —— 前端交互

### P2-1 多轮共用 `abortRef` / `turnMessageIdRef`，旧轮的 finally 会清掉新轮的状态

**位置**：`frontend/src/features/agent/useChat.ts:145,152,285-287`

触发：点「停止」后立刻再发一条。旧轮 AbortError 后进入 `finally`，把
`turnMessageIdRef.current` 与 `abortRef.current` 置 null → **新轮所有
text / tool_result 帧被 `patchTurnAssistant` 静默丢弃**，新轮助手气泡永远空白；
停止按钮失效、`busy` 提前为 false。

**修法**：每轮一个 generation token（`turnIdRef` 递增），所有 patch / finally
先比对 token 才生效。

### P2-2 SSE 提前断开被当成正常结束

**位置**：`frontend/src/features/agent/useChat.ts:240-247`、`frontend/src/api/sse.ts:69-74`

后端在 done 帧前断流（代理超时、进程被杀）时，`readSSE` 把剩余 buffer 当最后一帧
yield 完就正常返回 —— 前端**没有任何错误**，工具卡片永远停在 `running`（一直转圈）。

**修法**：`consume` 记录是否收到 `done` 帧；没收到就 `setError('连接中断')`
并把 `running` 工具块标记为失败。

### P2-3 切会话时历史加载失败不清屏

**位置**：`frontend/src/features/agent/ChatPage.tsx:53-75`、`useChat.ts:381-383`

切线程只 `loadHistory`，没有 `reset()`；catch 只 `setError`，不 `setMessages([])`。
→ 上一个会话的消息显示在新会话的 URL 下。

**修法**：切线程前先清 messages，并区分「加载中 / 加载失败」。

### P2-4 错误提示在空会话页完全不显示

**位置**：`frontend/src/features/agent/ChatPage.tsx:233-237`

`statusHint` 渲染在 `{!empty && ...}` 内。`loadHistory` 失败时 messages 为空 →
用户只看到一个空白页，没有任何报错。

**修法**：错误条提到页面级，与 `empty` 分支无关。

### P2-5 分支点索引基准不一致

**位置**：`frontend/src/features/agent/MessageList.tsx:99`、`useChat.ts:342-346`

`MessageList` 传的是**渲染数组**的下标；`forkAt` 在**重新拉取的历史数组**上取同一下标。
存在本轮流式消息（尚未进 checkpoint）时两者长度/顺序不一致 → 分支点错位，
用户毫无察觉。

**修法**：点分支时直接传该消息的 `sourceId`，不再按 index 重新对齐。

### P2-6 历史回填的工具块状态判不准

**位置**：`frontend/src/features/agent/useChat.ts:126-129`

无结果的 `tool_call` 一律置 `running`，刷新后永远转圈（实际可能已被中断 / 在等审批）；
`tool_call_id` 缺失时工具结果配不上，降级成一行纯文本。

**修法**：无结果标为 `pending` / `interrupted`；配不上时也并入工具块。

### P2-7 权限模式读取失败静默显示「自动」

**位置**：`frontend/src/features/agent/PermissionModePicker.tsx:32,50-52`

初始态写死 `'auto'`，读取失败静默吞掉 → 真实模式是 `bypass` 时界面显示「自动」，
用户误以为会有人批。

**修法**：读失败显示「读取失败」态；读回来之前不显示成某个具体模式。

### P2-8 Composer 渲染两处，切换即卸载重挂，模型选择被重置

**位置**：`frontend/src/features/agent/ChatPage.tsx:188,238`、`Composer.tsx:100-115`

空状态与底部各渲染一个 Composer。切到有消息时底部那个挂载，模型选择回到默认。

**修法**：只渲染一个，用 CSS 切换位置。

### P2-9 审批是阻塞长请求，按钮只 disabled，看起来像卡死

**位置**：`frontend/src/features/agent/useChat.ts:387-401`

后端 resume 是同步跑完整张图，批准后可能跑几十秒。前端没有「已批准，继续执行中…」提示。

**修法**：批准后立刻把卡片换成「已批准，agent 继续执行中」。

### P2-10 流式期间无条件滚到底

**位置**：`frontend/src/features/agent/ChatPage.tsx:156-166`

每帧 `scrollTop = scrollHeight` → 用户往上翻就被立刻拽回。

**修法**：用户已上滑则不自动滚。

### P2-11 其它

- `/clear`、`/rename` 仍用 `window.alert/prompt`（`ChatPage.tsx:97,104`），
  与平台自有的 `ConfirmDialog` 不一致；
- `ApprovalCard.tsx:47` 显示原始工具名 `write_file`，工具卡片用中文标签 —— 两处不一致；
- `ToolCallBlock` / `PermissionModePicker` 缺 `aria-expanded`、键盘导航、Escape 关闭；
- 窄屏（<1280）侧栏固定 256px + 右侧面板 22rem，对话区被挤到不足 400px。

---

## 五、P3 —— 上下文、记忆、性能

### P3-1 系统提示预算与压缩阈值互不知情

**位置**：`app/context/budget.py:37`（0.5×窗口）、`app/context/compaction.py:68`（0.85×窗口）

两套预算各算各的 → 实际请求可达 1.35×窗口被厂商拒。
且必装块超预算时强制装入（`context/assembler.py:123-131`），
`used_chars > budget_chars`，与 `assembler.py:80` 的 `over_budget` 注释矛盾。

**修法**：把预算合并成一个口径 —— 系统提示占用的额度要从压缩阈值里扣掉。

### P3-2 长期记忆超过 100 条后去重与冲突检测双双失效

**位置**：`app/memory/service.py:376,407`、`app/memory/long_term_store.py:199`

查重/冲突检测依赖 `list_all(limit=500)`，长期存储实际只返回 100 条 → 重复写入与冲突拦截
双双失效。且无标签的记忆（`service.py:403`）可静默写入与旧记忆矛盾的版本。

**修法**：查重改走数据库层的 `content_hash` 索引，别用「拉全量再比对」。

### P3-3 每轮重建整张图 + 重新导出技能

**位置**：`app/agents/runner.py:171`、`builder.py:187`、`runner.py:663`

每次请求 `create_deep_agent` 编译一次图；`export_skills` 每次 `rmtree` 整个技能目录再重写。
resume 路径更糟：建了**两次图**（`runner.py:275-294`），每次都重跑一次记忆召回。

**修法**：按 `thread_id` 缓存已编译的图（工具集 / 技能 / 后端不变时只换
system_prompt 与 checkpointer）。

### P3-4 每帧全量重渲染 + 每 token 重解析 Markdown

**位置**：`frontend/src/features/agent/useChat.ts:172`、`MessageList.tsx`

没有虚拟化，`Markdown` / `MessageList` 未 memo。

**修法**：按 message id 缓存渲染结果；长会话上虚拟化。

### P3-5 `_attach_summaries` 跨轮张冠李戴

**位置**：`app/agents/runner.py:1044-1076`

`repo.list_recent(thread_id, limit=...)` 取的是该 thread 最近 N 条，不分轮次；
同一工具同一参数在上一轮出现过时，`_match_record` 会先命中**旧记录**。

**修法**：给调用记录加轮次 id，runner 只在本轮之后的记录里匹配。

### P3-6 提示词与实际行为对不上（三处）

- `prompts.py:55-59` 说「沙箱容器按会话复用，装好的依赖会保留」，
  但 `sandbox_backend.py:32-40` 明确写「**环境变量不保留、cd 不保留**」，
  `container_pool.py:14-16` 的注释还写着「环境变量、cd 都留着」 —— **容器池的注释是错的**；
- `prompts.py:63-72` 教模型用 `delegate_worker` / `git_*`，
  但 `services/agent.py:250-256` 在**未绑定 git 目录时把这些能力剔掉** ——
  此时提示词仍在教模型用用不了的工具；
- `prompts.py:49` 说「删除文件…先说明影响再执行」，但 `tool_policy.py` 里
  `delete` 是 destructive（永远要人批），而 `write_file`/`edit_file` 是低档**自动放行** ——
  提示词给模型的预期与审批口径相反。

### P3-7 声明与实现不符

- `backends/assembly.py:63` 只传 `root_dir`，而模块 docstring 第 7 行声称用
  `virtual_mode=True` —— **实测 deepAgents 的 `FilesystemBackend` 默认就是
  `virtual_mode=True`**，所以实际行为是安全的，只是文档与显式配置该对齐；
- `run_python` 把临时脚本写进工作区却从不删除（`sandbox.py:96-114`），
  文件堆积且会被 `git_commit` 提交进仓库；
- `tool_policy.py:78-87` 给 `write_file`/`edit_file` 声明 `idempotent=True` →
  `intent.py:109` 生成幂等键 → `pipeline.py:281-299` 命中即复用旧结果，
  第二次编辑的「文件已变」错误被吞掉。**写类应 `idempotent=False`**；
- `execution/breaker.py:93-94` 把慢于 5s 的成功记为失败 →
  `run_python` 正常耗时即可能 5 次连错后误熔断；
- `execution/concurrency.py` 的 LIMITER 只覆盖 pipeline 路径，
  deepAgents 的 `execute`/`write_file` 完全不限流；`_gates` 永不清理。

---

## 六、分阶段实施计划

### 阶段 0 —— 止血（约 1 天，纯后端，改动小、可单测）

| 改动 | 文件 |
| --- | --- |
| 参数级检查改按**调用指纹**豁免，不按工具名豁免 | `intent/middleware.py`、`agents/builder.py` |
| `run_python` 的 `code` 等参数名纳入破坏性匹配；`annotations.destructive` 上调条件放宽 | `execution/risk.py` |
| bypass 拆成「跳过人工确认」与「跳过安全判定」两个开关 | `registration/policy.py`、`intent/middleware.py` |
| `auto` 可以显式设置 | `agents/permissions.py` |
| 直接调 API 的路径读会话权限模式 | `services/capability.py`、`services/invocation.py` |

配套测试：`tests/test_agent_capabilities.py` 补
「auto 模式下 `execute("rm -rf /")` 仍被顶到 destructive 且 require_admin」、
「bypass 模式下内网 URL 仍被判定但不再挂起」、
「`set_permission_mode(tid, "auto")` 之后读回是 auto」。

### 阶段 1 —— 会话生命周期（约 2 天）

| 改动 | 文件 |
| --- | --- |
| 记忆详情/删除按会话作用域修好 | `api/deps.py`、`api/v1/memory.py`、`memory/service.py` |
| 真正的「删除会话」端点：checkpoint + 权限文件 + 绑定 + 短期记忆 + 工作区 | `api/v1/agent.py`、`services/agent.py`、`environments/store.py` |
| fork 失败回滚（不留孤儿目录/绑定） | `services/agent.py` |
| 按 thread_id 的 turn 级锁 | `services/agent.py` |
| approve 接受逐条决定 | `api/v1/agent.py`、`agents/runner.py`、`frontend ApprovalCard` |

### 阶段 2 —— 前端状态机（约 2 天）

| 改动 | 文件 |
| --- | --- |
| 每轮 generation token，隔离 abortRef / turnMessageIdRef / busy | `useChat.ts` |
| SSE 无 done 帧时报错并标记工具块失败 | `useChat.ts`、`api/sse.ts` |
| 切会话失败清屏；错误条提到页面级 | `ChatPage.tsx` |
| 分支点改传 sourceId | `MessageList.tsx`、`useChat.ts` |
| 历史工具块状态区分 pending / interrupted | `useChat.ts` |
| 权限模式读失败显式提示 | `PermissionModePicker.tsx` |
| Composer 只渲染一个 | `ChatPage.tsx` |
| 批准后显示「继续执行中」 | `useChat.ts`、`ApprovalCard.tsx` |
| 流式时用户上滑不自动滚 | `ChatPage.tsx` |

### 阶段 3 —— 上下文与性能（约 2 天）

| 改动 | 文件 |
| --- | --- |
| 预算与压缩阈值合并口径 | `context/budget.py`、`context/compaction.py`、`context/assembler.py` |
| 长期记忆查重改走 content_hash 索引 | `memory/service.py`、`memory/long_term_store.py` |
| 图按 thread_id 缓存；resume 不再建两次图 | `agents/runner.py`、`agents/builder.py` |
| 提示词与实现对齐（容器复用说明、git 能力可用性、删除口径） | `agents/prompts.py`、`capabilities/backends/container_pool.py` |
| `run_python` 临时脚本执行后必删 | `registration/builtin/sandbox.py` |
| 写类能力 `idempotent=False`；熔断不把慢成功记为失败 | `registration/tool_policy.py`、`execution/breaker.py` |
| 前端渲染缓存 + 长会话虚拟化 | `frontend/src/features/agent/*` |

### 阶段 4 —— 文档与风险声明（并行做，半天）

- `backend/README.md` 顶部风险段补：权限模式接口能把自己提到 bypass（无鉴权前提下）、
  待批载荷明文落库（能力层待办 G）；
- `backend/docs/plans/capability-followups.md` 加一条新的 P0 条目，记录本次审查的结论；
- `backend/README.md` 里「215 passed」的实际条数已经过时（现 368），一并修掉。

---

## 七、建议现在做的取舍

**建议先做阶段 0 + 阶段 2**，理由：

- 阶段 0 的四条是「用户以为被拦住了，实际没拦」——
  在无鉴权的前提下，这是唯一一类会让平台做错事的问题；
- 阶段 2 的前端问题用户天天撞得到（切会话串消息、SSE 断了不报错、
  停止后新消息空白），修起来是纯前端改动，不依赖后端，风险低。

阶段 1 的会话生命周期与阶段 3 的性能可以放到第二轮。

另外**建议单独拍板一件事**：`bypass` 是否需要进程级开关。
这是「无鉴权」这个已知风险与「绕过权限」这个功能之间的取舍，
我倾向默认关（用环境变量开），但这是产品决定，不是技术决定。

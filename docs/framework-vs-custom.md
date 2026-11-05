# 框架能力 vs 自研实现 —— 盘点清单

> 这份清单回答一个问题：**平台里哪些东西是自己写的，而框架（deepAgents 0.7.32 /
> LangChain 1.4.7 / LangGraph）已经提供了？**
>
> 结论先说：**真正"自己写了一遍、框架已有"的只有 4 处**，其中 2 处曾是生产故障
> （2026-11-05 已按框架的形状改掉）。其余要么已经在用框架、要么框架给的是
> **另一层**的东西（执行器而非平台语义）、要么框架根本管不着（沙箱隔离、
> 调用留痕）。
>
> **盘点日期**：2026-11-05　**依据**：源码逐行核对 + 运行时实测（不是看名字猜）
>
> 本文里的每条 `文件:行号` 都对着源码复核过；两条"生产故障"都**实跑复现**过
> （写入落点、`grep` / `glob` 的报错文本都是实测输出）。

---

## 一、判据：什么不能替换

平台的核心价值主张是**每次能力调用都经过同一条可声明、可校验、可审计、可拦截的
流水线**（`app/capabilities/execution/pipeline.py:52` 的七阶段 + `:504` 的留痕）。

所以替换有一条硬规矩：

> **凡是会让工具调用绕过这条流水线的框架件，一律不能拿来替换自研实现。**
> 只能在流水线**之下**复用（当执行器用），不能在上游接管。

三条安全底线同样不可让步：**沙箱隔离**、**参数级风险判定**（`rm -rf` / SSRF，
`app/capabilities/execution/risk.py`）、**调用留痕**。

---

## 二、框架有、平台也写了的（4 处）

这一节是"框架能接手"的全部。其中 2.1 / 2.2 曾经是**生产故障**，2026-11-05
已按框架的形状改掉；2.3 是**留下的两条路里死的那条**；2.4 是**互补**
（可叠加，不宜替换）。

### 2.1 `PlatformBackend` 没继承 `CompositeBackend` —— ✅ 已修（2026-11-05）

**框架有**：`CompositeBackend` 的 `artifacts_root` 语义（`backends/composite.py:260`），
以及它按前缀路由文件操作的能力。

**原来的问题**：平台自研的 `app/capabilities/backends/platform_backend.py` 是个
**包装层**（`class PlatformBackend(SandboxBackendProtocol)`），**不是**
`CompositeBackend` 的子类。

**后果（实测）**：框架用 `isinstance` 判断 artifacts 根目录，
`FilesystemMiddleware` 里全落空 —— `middleware/filesystem.py:1993`：

```python
artifacts_root = self.backend.artifacts_root if isinstance(self.backend, CompositeBackend) else "/"
```

平台传给它的正是那层包装（`builder.py:221` → `:293`），于是 `artifacts_root`
退化成 `"/"`：

```text
PlatformBackend.artifacts_root 属性  = /.artifacts/
框架 isinstance 检查后的 artifacts_root = /          ← 退化
→ 框架算出的前缀: /conversation_history
   （另两个同理: /large_tool_results、/blobs）
```

而平台的 routes 只有 `"/"`（宿主工作区）——**这三个前缀都落进用户的工作区**。

**实测落点**：照框架的写法写一份 `{prefix}/{uuid}.md`：

```text
写入 //conversation_history/abc.md
落在工作区里:   [ .../workspace/conversation_history/abc.md ]
落在产物目录里: []
```

其中 `conversation_history` 是**活的路径**：框架的 `FilesystemMiddleware`
默认 `human_message_token_limit_before_evict=50000`
（`middleware/filesystem.py:1899`），超过 5 万 token 就会淘汰旧消息并把内容写到
`{prefix}/{uuid}.md`（`:3703`）。也就是说**长对话里，被淘汰的历史会落进用户的
工作区目录**，可能被 `git commit` 一起提交上去。

平台只在自己写的 `app/context/compaction.py:221` `_fix_artifacts_root()` 里修了
**摘要中间件那一半**（框架 `summarization.py:739` 是同样那行判断），框架
`FilesystemMiddleware` 那一半没修。

**修法**：`PlatformBackend` 改成**继承** `CompositeBackend`（不是包一层），
其余方法全部继承 —— 那一层转发本来就是多余的。一处改动同时修掉 artifacts
路径失效与 `ls("/")` 聚合分支不可达（`composite.py:332-349` 做的正是平台
`_merge_virtual_dirs` 手写的同一件事，所以那个覆写仍然保留）。

回归测试：`tests/test_backends.py::test_平台后端是复合后端的子类`、
`::test_产物目录落在工作区外面`、
`::test_框架的文件中间件把历史外置到工作区外面`（最后一条走框架**自己**的
判定，不是我们复述一遍那行 isinstance）。

### 2.2 `DockerSandbox.grep` 签名对不上框架 —— ✅ 已修（2026-11-05）

**框架的调用形状**（`backends/composite.py:439`）：

```python
raw = backend.grep(pattern, path, glob, max_count=max_count)   # 3 个位置参数
```

**原来的签名**（`app/capabilities/backends/sandbox_backend.py:225`）：

```python
def grep(self, pattern, path=None, **kwargs) -> GrepResult   # 只收 2 个位置参数
```

`glob` 挤到第三个位置参数上 → `TypeError`。

**实测（docker 模式，走平台自己的 `build_backend`）**：

```text
grep(pattern, path, glob, max_count=)  → TypeError: DockerSandbox.grep() takes from 2 to 3 positional arguments but 4 were given
grep(pattern, path)                    → NotImplementedError: DockerSandbox 只提供 execute，文件操作不走这里
glob(pattern, path)                    → NotImplementedError: DockerSandbox 只提供 execute，文件操作不走这里
```

异步那两条更糟：`agrep` / `aglob` / `als` **没有被覆写** —— 基类会**真的在容器里
跑 `grep` / `find` / `ls`**，路径是虚拟路径，于是搜出来的是**容器的系统文件**
（`/usr/share…`），不是工作区的内容。

**为什么测试没发现**：`tests/conftest.py:23` 把 `SANDBOX_TYPE` 设成 `local`。
local 模式下 default 是 `LocalShellBackend`（它实现了文件方法），同一条路径变成
"结果重复"，被平台的 `_dedupe_glob` / `_dedupe_grep` 掩盖了。**生产（docker）下
这条是真实故障面** —— 而模型是可以不传 `path` 的（`middleware/filesystem.py:1447`
的 schema：`path: str | None = Field(default=None)`）。

**修法**：签名对齐 `FilesystemBackend.grep`
（`pattern, path, glob, *, max_count, context_lines`）；查找类（`ls` / `glob` /
`grep`，含异步版）返回**空结果**而不是报错 —— 容器里的文件树**不是**平台的文件树
（工作区挂在 `/workspace`，平台的读写走宿主后端），而 `CompositeBackend` 会把
默认后端与各路由的结果**合并**：空结果不影响宿主那份真实命中，报错则会让整个
glob / grep 一起失败（框架不捕获后端异常）。

改单个文件的方法（`read` / `write` / `edit` / `delete`，含异步版）**继续报错**
—— 那才是有意的边界（基类会拿虚拟路径去容器里跑命令，返回"文件不存在"这种
看起来像正常结果的错误）。

回归测试：`tests/test_backends.py::test_sandbox_grep_accepts_framework_call_shape`、
`::test_sandbox_search_methods_return_empty`、
`::test_sandbox_async_search_methods_return_empty`、
`::test_docker_模式下_glob_与_grep_走得通`。

### 2.3 `capabilities/prompt.py` 的技能块拼接 —— 死代码

**框架有**：`SkillsMiddleware`（`deepagents/middleware/skills.py:916`）——
渐进披露：提示词里只放 name + description，模型觉得相关了才去读 `SKILL.md` 正文。

**平台自研**：`app/capabilities/prompt.py:17` `assemble_prompt()` + `:51`
`_skill_block()` —— 把技能的 knowledge / checklist / constraints / examples
**全量**拼进系统提示词。

**实测**：这条路径**不在 agent 主路径上**。全仓 `assemble_prompt` 只有两个调用点
—— `app/capabilities/__init__.py:20` 的再导出，和
`app/services/capability.py:140` 的**技能预览接口**。agent 真正用的系统提示词走
`app/context/service.py::assemble()`（`app/agents/runner.py:887`），技能那条路
走的是 `app/capabilities/skills_export.py:34` 导出成 `SKILL.md`（框架的渐进披露）。

**所以这不是"重复"，是"留了两条路，活的只有一条"**。要么删掉 `prompt.py` 的
技能拼接（预览接口改成展示 `SKILL.md` 的正文），要么明确它只服务预览。

### 2.4 `redaction.py` 与 LangChain 的 `PIIMiddleware` —— 互补，不是替换

**框架有**：`PIIMiddleware`（`langchain/agents/middleware/pii.py:513`）——
检测 email / credit_card / ip / mac_address / url，策略 block / redact / mask / hash，
可作用于 input / output / tool_results，且装流式 transformer 在**线路上**实时脱敏。

**平台自研**：`app/capabilities/execution/redaction.py:16` —— 只认 6 类**凭据**
（`sk-` / `sk-ant-` / `gh[pousr]_` / `AKIA` / `AIza` / `password=`），掩码 `***`，
且**只脱敏落库副本**，给模型看的原文不动。

**关系是互补**：框架不认 API key，平台不认 PII。框架能补上平台缺的**线路级脱敏**
（流式输出里就脱敏，而不是等落库时）。

**代价**：框架 `strategy="redact"` 会把 `[REDACTED_EMAIL]` 之类写回消息体，
这会污染平台 `Invocation.arguments` 的原样落库语义（`pipeline.py:507`）。
平台的取向是"存库脱敏、传模型原文"，与框架方向相反 —— **可叠加，不宜替换**。

---

## 三、框架有、平台没有 —— 值得补的（3 处）

| 框架能力 | 平台现状 | 引入的坑 |
| --- | --- | --- |
| `ModelRetryMiddleware` / `ModelFallbackMiddleware`（`langchain/agents/middleware/model_retry.py:33` / `model_fallback.py:342`，都有 async 变体） | **完全没有**。全仓 grep `max_retries\|with_retry` 零命中；`app/agents/llm.py` 建模型时不传 `max_retries` | 低。回退需要多厂商 key，平台现在是单模型单厂商；`model_fallback.py:117` 要剥 Anthropic `cache_control` / Fireworks 头 |
| `AsyncSubAgentMiddleware`（`deepagents/middleware/async_subagents.py:1508`）—— `start_async_task` / `check` / `update` / `cancel` / `list_async_task`，后台跑 + 唤醒父 agent | **没有**。`app/capabilities/registration/builtin/delegate.py:43` 的 `delegate_worker` 是**同步**的（`await agent.ainvoke`） | 高。它跑的是**远程 Agent Protocol 服务器**上的子代理（`url` 可省，走 ASGI 接本地的 Agent Protocol 服务）—— 平台的子代理是**进程内现造的 deep agent**，不是 Agent Protocol 服务，接不上。而且框架异步子 agent **默认不带** `CapabilityMiddleware` → 能力调用会**绕过七阶段流水线** |
| `RubricMiddleware`（`deepagents/middleware/rubric.py:492`）—— grader 子 agent 迭代打分直到 satisfied | 没有。`app/agents/reflection.py:237` 是失败记录，不是评分闭环 | 中。grader 的模型调用**不在能力清单里**，不声明、不留痕 —— 与"每次调用都可审计"冲突。引入前要给它单独声明能力 |

---

## 四、已经在用框架的（不用动）

| 平台用的 | 框架提供方 |
| --- | --- |
| `app/context/compaction.py:115` `PlatformSummarizationMiddleware` | 继承 deepAgents 的 `SummarizationMiddleware`，同名替换。**已是最优形态** —— LangChain 原版直接丢历史，deepAgents 版外置成文件供 `read_file` 回查 |
| `app/context/skills_quota.py:35` `QuotaSkillsMiddleware` | 继承框架 `SkillsMiddleware`，只覆写 `_format_skills_list` 做清单配额 |
| `app/agents/builder.py:261` `MemoryMiddleware` | 框架的（只当工作区规则用） |
| `app/agents/builder.py:415` `_general_subagent()` | 框架 `SubAgentMiddleware` 的默认规格，平台覆盖了一份（为了带治理中间件） |
| `app/agents/builder.py:202` `TodoListMiddleware` | LangChain 的 |
| `app/agents/builder.py:271` `interrupt_on` | 框架 `HumanInTheLoopMiddleware` |
| `app/agents/checkpointer.py:40` `AsyncSqliteSaver` | LangGraph 的 |
| `app/capabilities/backends/assembly.py:67` `FilesystemBackend(virtual_mode=True)` | deepAgents 的 |
| `app/capabilities/mcp_client.py:71` `MultiServerMCPClient` | `langchain_mcp_adapters` 的 |

**框架默认已经自动生效、平台没意识到自己在受益的**：

| 框架件 | 作用 |
| --- | --- |
| `_prompt_caching.py` | 按模型挂 Anthropic / Bedrock / Fireworks 的缓存中间件（`graph.py:407`） |
| `_blob_offload.py` | 二进制内容外置（`middleware/filesystem.py:88` 集成）—— 但 `offload_binary_content` 默认 `False`，平台也没开 |
| `_message_eviction.py` / `_overflow_clip.py` | 大结果预览替换、溢出裁尾。**这一条恰好作用在平台流水线没覆盖的那批工具（文件 / 命令）上** —— 是目前唯一给它们做的上下文保命措施 |
| `patch_tool_calls.py` | 给未完成的 tool call 补 `status="error"` 的 ToolMessage |
| `unsupported_content.py` | 按 model profile 过滤模型不支持的 content block —— 与平台 `_apply_vision` 填的 `profile["image_inputs"]` 天然咬合 |

---

## 五、框架给的是"另一层"，不能替换自研的（保留自研）

| 框架的 | 平台的 | 为什么不是一回事 |
| --- | --- | --- |
| `deepagents/middleware/filesystem.py` 的 `FilesystemPermission`（`middleware/permissions.py` 只是再导出） | `app/agents/permissions.py:53` + `app/capabilities/registration/policy.py:187` | 维度不同：框架按**路径模式** allow/deny/interrupt；平台按**风险档位 × 会话模式**。而且框架的 `permissions` 在后端支持执行时**抛 `NotImplementedError`**（`middleware/filesystem.py:2058-2064`）—— 只有当规则全部限定在已挂的路由上才放行；平台后端就是可执行的，**这条通道对平台基本不可用**。注意 `create_deep_agent` 也没传 `permissions=` |
| `langchain/agents/middleware/context_editing.py:187` `ContextEditingMiddleware` | `app/context/blocks.py` + `assembler.py` + `budget.py` | 框架是"清掉旧工具结果"；平台是"按优先级重装上下文块 + 外置历史" |
| `langchain/agents/middleware/tool_retry.py:32` | `app/capabilities/registration/policy.py:301` `RetryPolicy` + `pipeline.py:373` `_can_retry` | 层级不同：框架重试包在工具调用**外层**，会连整个七阶段流水线一起重跑 → **每次重试多一条 `Invocation` 留痕**，审计失真。平台重试在阶段⑤内部，留痕只一条。平台版还按能力声明、可持久化、区分错误码、强制幂等 |
| `langgraph/store/`（`BaseStore` / `SqliteStore`） | `app/memory/` 整套两层记忆 | 框架是通用 KV + 向量索引；平台是 RAG 集合 + hybrid 召回 + 记忆写入硬校验（凭据拦截、冲突拒绝、取代语义）。框架 `InMemoryStore` 是进程内的；`SqliteStore` 无向量检索 |
| `langchain/agents/middleware/_execution.py:267` `DockerExecutionPolicy` | `app/capabilities/backends/sandbox_backend.py` + `container_pool.py` | 框架版**每次命令起一个新容器**（`docker run -i`，`remove_container_on_exit=True`），**没有复用**。平台的容器按会话复用是框架没有的能力（框架无 session_id / 复用 / 池钩子） |
| `deepagents/backends/` 的 `StateBackend` / `StoreBackend` / `ContextHubBackend` | 平台的宿主工作区 | 平台的文件语义是"宿主工作区里的真实文件"，不是 state/store 里的虚拟条目。平台的判断（不用 / 暂不用）**正确** |
| `langchain_text_splitters`（已装未用） | `app/rag/chunking.py`（501 行，三种策略：text / markdown / code） | 框架的 `MarkdownHeaderTextSplitter` / `PythonCodeTextSplitter` 切法不同（不做代码顶层定义切、不产出 heading path / symbol 的 meta）。平台的切法是**为记忆层服务的**（分块 meta 要带 `kind` / `memory_kind` / `tags` 等字段，记忆层按它过滤）。**不是简单替换** |
| `langchain_core/vectorstores/base.py` `VectorStore` | `app/rag/vector_store.py` | 形状不同：框架是**文档导向**（字符串 / `Document`），平台是**分块导向 + 集合作用域**（`add_chunks` / `keyword_search` / `vector_search` 都带 `collection_id`） |
| `langchain/agents/middleware/tool_selection.py:123` `LLMToolSelectorMiddleware` | `app/capabilities/intent/middleware.py:117`（plan 模式裁工具集） | 框架用 LLM 挑工具 = 让模型**先看见全部工具名**再筛；平台在 plan 模式下**直接把写类工具摘掉**，模型根本看不见 |

---

## 六、绝对不能引进来的框架件（红线）

| 框架件 | 为什么不行 |
| --- | --- |
| `langgraph/prebuilt/chat_agent_executor.py:278` `create_react_agent` / `langgraph/prebuilt/tool_node.py:630` `ToolNode` | 会让工具脱离平台流水线 —— 无留痕、无审批、无风险判定。**注意 LangChain 1.x 的 `langchain/agents/factory.py:824` 只有 `create_agent`，`create_react_agent` 已经搬到 LangGraph 的 prebuilt 里** |
| `langchain/agents/middleware/shell_tool.py` `ShellToolMiddleware` + `DockerExecutionPolicy` | 绕开沙箱隔离与能力流水线；框架的沙箱没有平台的 `--network none` / `--cap-drop ALL` / `no-new-privileges` |
| `langgraph/store/` 当长期记忆 | 放弃 hybrid 召回 + 记忆写入硬校验 |
| `langgraph/types.py` `Send` / 框架原生 handoff | 会让子代理调度绕开平台的 `delegate_worker`（独立 worktree + 递归封顶 + git 能力收窄） |
| 框架 `permissions=[FilesystemPermission]` | 在可执行后端上直接抛 `NotImplementedError`；且会引入第二个审批事实源，与 `policy.py:187` 的 `approval_decision` 打架 |

---

## 七、一条不属于"重复"但更重要的发现

**平台流水线最大的缺口，恰好是框架的文件系统中间件。**

`app/capabilities/registration/builtin/__init__.py:18-21` 明写："文件与命令类工具
不在这里 —— 它们由 deepAgents 的文件系统中间件提供"。于是这八个工具
（`ls` / `read_file` / `write_file` / `edit_file` / `delete` / `glob` / `grep` /
`execute`）**不在**七阶段流水线里，只在 `app/capabilities/intent/middleware.py:352`
落一份**简化**的调用记录（没有分阶段结论）。

所以"每次能力调用都过同一条流水线"这句话目前**对文件 / 命令族不成立** —— 而这
恰恰是风险最高的一族（`delete` 是不可逆的、`execute` 能跑任意命令）。

它们**仍然**经过参数级风险判定（`middleware.py:175` 的 `assess_call`），但**没有**
七阶段里的 schema 校验、权限校验、幂等去重、熔断、结果验证。

补法**不是换成框架某个中间件**（那正是现在的情况），而是把这八个工具登记进
`CapabilitySpec`，让 `app/capabilities/execution/tooling.py:29` 的 `build_tools`
接管 —— 让它们自己走流水线，同时把 `skip_names=FS_TOOL_NAMES`（`builder.py:184`）
去掉。治理中间件怎么挂，参照平台自己的做法：`app/agents/builder.py:431`
（`_general_subagent` 里的 `"middleware": [governance, TodoListMiddleware()]`）。

---

## 八、清单速查

| # | 项 | 类型 | 位置 | 动作 |
| --- | --- | --- | --- | --- |
| 1 | `PlatformBackend` 不继承 `CompositeBackend` | **已修**（2026-11-05） | `backends/platform_backend.py` | 已改成继承，artifacts 路径与 `ls("/")` 同时修好 |
| 2 | `DockerSandbox.grep` 签名不兼容 | **已修**（2026-11-05） | `backends/sandbox_backend.py` | 签名对齐框架；查找类返回空结果，异步版一并覆写 |
| 3 | 技能全量拼接（死代码） | 重复 | `capabilities/prompt.py:17` | 删掉，或明确只服务预览 |
| 4 | 凭据脱敏 | 互补 | `capabilities/execution/redaction.py:16` | 可叠加 `PIIMiddleware`，不替换 |
| 5 | LLM 重试 / 回退 | 缺口 | —— | 引入 `ModelRetryMiddleware` |
| 6 | 异步子 agent | 缺口 | —— | 框架那套跑**远程 Agent Protocol 服务器**，平台的子代理是进程内现造的 —— 接不上 |
| 7 | 质量评分闭环 | 缺口 | —— | 引入 `RubricMiddleware`（须声明 grader 能力） |
| 8 | 文件 / 命令工具不入流水线 | **架构缺口** | `builder.py:184` | 把 8 个工具登记进 `CapabilitySpec` |

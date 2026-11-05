# Agent 自主能力审查：上下文 / 记忆 / 能力

> 审查日期：2026-11-05　审查范围：`backend/app/{context,memory,capabilities,agents}`
> 结论一句话：**执行层已经是生产级，规划/迭代/反思/进化四项基本缺失或被设计性地堵死。**

---

## 0. 总览

| 维度 | 上下文 | 记忆 | 能力 | 综合 |
| --- | --- | --- | --- | --- |
| 自主规划 | 弱（只有 write_todos 记事） | 无 | 弱 | **弱** |
| 自主执行 | 强 | 中 | **强** | **强** |
| 自主迭代 | 弱（压缩丢链） | 弱 | 中（重试上限 1） | **弱**（压缩丢链**已修**） |
| 自主反思 | 无 | 无 | 无 | **无** |
| 自主进化 | 无 | 弱（只记用户说的 → **已放开记验证过的经验**） | 无 | **无** |

三个"强"是真实的：工具集完整、执行层七阶段严谨、参数级风险判定看得见真实参数。
四个"弱/无"也是真实的，而且其中三处是**主动设计的直接后果**（提示词明确禁止、代码路径不存在），不是"还没来得及做"。

---

## 0.5 修复状态（同日推进，2026-11-05）

审查出的风险里，**R1 / R1b / R2 / R3 / R5 / R7 已修**，回归测试见
`backend/tests/test_autonomy_fixes.py`（14 条，每条盯一个**实测复现过**的缺陷）。

| # | 状态 | 怎么修的 |
| --- | --- | --- |
| **R1** | 已修 | worker 的 `trust_mode` **保持 `permissive`**（改成 `auto` 会挂起等不到人批，worker 会直接卡死），但补上了两件事：参数级风险判定**不再整段跳过** —— `interrupt_enabled=False` 时仍然 `assess_call` 算一遍并把风险写进调用记录（只记不拦）；命中风险规则时打 warning 日志。见 `capabilities/intent/middleware.py` |
| **R1b** | 已修 | 两层：**能力层**——`git_worktree_add` / `git_worktree_remove` / `git_merge` 从 worker 的能力清单里整个剔掉（`agents/worker.py` `WORKER_BLOCKED_GIT_KEYS`），worker 根本看不到这三个工具；**能力实现层**——`git_worktree_remove` 增加同仓库校验（`GitService.common_dir()` 比对），跨仓库的目标直接拒 |
| **R2** | 已修 | `PlatformSummarizationMiddleware` 改成继承 **deepAgents 版**（不再是 LangChain 版）—— `backend=` 不再被 `**deprecated_kwargs` 吞掉，历史外置回来了，state 不再被 `RemoveMessage` 改写。见 `context/compaction.py` |
| **R3** | 已修 | `trim_tokens_to_summarize=None` —— 被压缩的历史**全量**进摘要，不再只送最后 4000 token |
| **R5** | 已修 | 主 agent 与 worker 都显式设 `recursion_limit`（见 §3.1 的更正 —— 原报告的前提是错的）；`agent_max_steps` 从死配置接上，默认 20→60 |
| **R7** | 已修 | `compaction.py` 的注释改写为"为什么接管"（含实测证据），不再说谎 |

**另修的一处**（原报告没列）：deepAgents 算 `artifacts_root` 用的是
`isinstance(backend, CompositeBackend)`，而平台的 `PlatformBackend` 只是**包着**
CompositeBackend、不是它的子类 —— 判定为 False，产物目录退化成 `"/"`，
**被压缩掉的对话历史会落进用户的工作区**（可能被 `git commit` 一起提交上去）。
现在 `_fix_artifacts_root()` 显式把它指回 `/.artifacts/`。

**仍然开放**：

- **R4（无鉴权 + bypass 自提权）** —— 用户已决定**开启** bypass 并做成可点击的开关。
  这条风险**依然成立**：平台没有任何鉴权，知道 `thread_id` 就能把会话设成 bypass，
  而 bypass 会让能力调用直接执行。缓解只有白名单根目录 + 沙箱容器。
- **R6**（worker 无 checkpointer、无中断恢复）—— 未动。
- §4 自主反思、§5 自主进化、§6 意图层工具收敛 —— 未动，见 §8。

---

## 1. 自主执行 —— 强，唯一够格的一维

**能力层**（`app/capabilities/`）：

- 工具全量：deepAgents 文件/命令工具 8 个（ls/read_file/glob/grep/write_file/edit_file/delete/execute，
  `registration/tool_policy.py:33-35`）+ 自有内置 13 个（`registration/builtin/__init__.py:110-504`）+ MCP 工具 + 技能。
- 执行层七阶段（`execution/pipeline.py:52`）：任务状态 → schema → 权限 → 风险 → 审批 → 调用 → 结果验证 → 留痕。
  失败也留痕（`pipeline.py:67`）。
- **参数级风险是真的**：按参数名 `command/code/script/source/program` 匹配 `rm -rf`/`dd`/`curl|sh`/`shutil.rmtree`
  （`execution/risk.py:31-59`），URL 查内网段含 `169.254.0.0/16`（`risk.py:69`），
  只能上调不能下调（`registration/policy.py` 的 `max_risk`）。
  **实测会真的挂起**：一个声明为 `write`（低档、不进声明级审批）的工具，
  参数里含 `shutil.rmtree` 时，`_check_param_risk` 抛 interrupt → 走人工审批。
- **但要分清两层的作用域**（这是复核时才发现的，容易看错）：
  - **声明级**（`interrupt_on`）：按**工具**决定要不要人批。auto 模式下
    `execute`/`delete`/`run_python` 都在批的名单里（实测 `_approval_tool_names()`）。
  - **参数级**（`_check_param_risk`）：**只对没被声明级覆盖的工具生效** ——
    `middleware.py:247-255` 明确写着"声明级批过了就不问第二遍"。
  - 所以 `execute` + `rm -rf` 在 auto 模式下**走的是声明级**（人看到的是
    "执行命令 · 高档"，档位由 `_pending_entries` 用参数重新算过，`runner.py:1123`），
    **不是**参数级那条路。两条路都能拦住，路径不同而已。
- SSRF 有防（`risk.py:243` `_is_internal_url`）。
- 失败回灌模型带可执行指令：`errors.py:96` 的 `to_text()` 结尾固定是"（可以修正参数后重试）"或"（换个思路，不要重复同样的调用）"。

**多智能体**：`delegate_worker`（`builtin/delegate.py`）是真实可用的并行机制 ——
独立 worktree + 独立分支 + 独立沙箱容器 + 独立上下文，
产出经 `WorkerResult`（`agents/worker.py:214`）回到主 agent（`delegate.py:120-140`）。
并行靠模型一次回复发多个 tool call，无平台侧控件 —— 这个取舍是对的。

**结论**：这一维不需要返工。

---

## 2. 自主规划 —— 弱

**现状**：唯一的规划机制是 deepAgents 的 `write_todos`（`agents/builder.py:201`）。
而我们给它写的工具说明自己就声明了边界：

> `write_todos` 只管记事，不负责给答案。（`builder.py:89`）

**缺什么**：

- 没有 plan-first 的提示词规则。主提示词（`agents/prompts.py:50-76`）10 条准则里没有一条要求"先规划"。
  唯一的规划入口是用户手打斜杠命令 `/plan`（`agents/commands.py:38-42`）。
- 没有重规划。清单一旦写定，没有"发现前提变了要改计划"的通路。
- 清单状态不跨轮持久化的确认：`write_todos` 走 `TodoListMiddleware`，状态在图 state 里，
  但压缩一旦发生（见 §3），`before_model` 重建 messages 时不涉及 todos —— 这一项**恰好**是安全的，
  但也意味着清单与对话历史**脱钩**：清单说"做到第 3 步"，对话历史里前两步的细节已经没了。

**要补的**（按性价比）：

1. 提示词加一条规划准则（"三步以上先 write_todos"）—— 工具说明里已经写了，但**系统提示词**里没有，
   权重不同。低成本。
2. `write_todos` 增加"计划要改"的能力：允许模型在发现前提变化时改写清单（现在只能整体替换，
   没有"重规划"语义）。
3. 长期目标/约束持久化：目前只有 AGENTS.md（工作区级，每轮全量注入，不受预算控制）
   和记忆目录（每轮召回）。**"这个任务的目标是什么"没有专门的地方放。**

---

## 3. 自主迭代 —— 弱，且压缩环节有一处实打实的缺陷

### 3.1 迭代循环本身

没有 loop/continue 工具。多轮靠 LangGraph 的 ReAct 循环（模型持续调工具直到出最终消息）。

- 主 agent **没有设 `recursion_limit`** → **不是**框架默认的 25。deepAgents 在
  `create_deep_agent` 里用 `.with_config()` 把 `recursion_limit: 9999` 绑在了图上，
  而 LangChain 的 config 合并里**调用时的值只有不等于 25 时才覆盖绑定值**
  （`langchain_core/runnables/config.py:487`）。实测：平台此前既没传 `recursion_limit`
  也没改绑定值 → **实际生效的是 9999，等于没有上限**。
  `config.py:69` 的 `agent_max_steps: int = 20` **全仓无人读取** —— 死配置。
  - 原报告在这里写的是"用框架默认 25"，**前提是错的** —— 见文末「复核时修正的四处」。
- worker 设了（`agents/worker.py` `recursion_limit = max_steps + 20 = 60`）——
  但那个值同样**压不住** deepAgents 绑的 9999 之外的情况：worker 走的是
  `build_agent` + `ainvoke`，config 是显式传进去的，所以它那 60 是生效的。
  真正的问题在主 agent 那一侧。
- 跨轮迭代只能靠用户：`api/v1/agent.py:344` regenerate、`:376` fork。
- 执行层自动重试基本不发生：默认 `capability_retry_max_attempts = 1`（`config.py:153`），
  且要同时满足"错误码在白名单 + 运行时 retryable + 能力幂等"（`pipeline.py:389-393`）。

### 3.2 压缩会断迭代链 —— 已实测确认，**已修**

> **修复状态**：本节描述的缺陷全部已修（方案 A：继承改成 deepAgents 版 +
> `trim_tokens_to_summarize=None` + 注释改写）。下面的实测证据保留作为
> "曾经真的这样"的记录 —— 回归测试 `test_autonomy_fixes.py` 会盯着它不许退回去。

`context/compaction.py` 声称接管 deepAgents 的摘要中间件，并明确写了它带来的收益：

> 被压缩掉的历史先落到 backend 的文件里（`conversation_history/{id}.md`），摘要里带上这个路径，
> agent 需要时能用 `read_file` 回去翻。（`compaction.py:6-8`）
>
> LangChain 原版是直接丢 —— **不可恢复**。

**这句话在当前代码里不成立。** 实测证据：

1. `PlatformSummarizationMiddleware` 继承的是 **LangChain 的** `SummarizationMiddleware`
   （`compaction.py:73`），不是 deepAgents 的 `_DeepAgentsSummarizationMiddleware`。
2. `backend=` 参数被 LangChain 的 `**deprecated_kwargs` **静默吞掉** —— 该 `__init__` 只处理
   `max_tokens_before_summary` / `messages_to_keep`。实测：
   ```
   ours has backend attr: False | _backend: NONE
   base has _backend: True
   => base would offload history to: /conversation_history
   => ours offload prefix: NONE — offload LOST
   ```
3. 同名替换机制**本身是工作的**（`_apply_custom_middleware` 按 `.name` 原地替换，已实测：
   `_DeepAgentsSummarizationMiddleware -> PlatformSummarizationMiddleware`）——
   所以问题不是"挂了两份"，而是"换成了一份功能更少的"。
4. 实测压缩后 checkpoint 里**原始消息消失**：
   ```
   checkpoint msg count: 8
   'msg number 0' in checkpoint?: False
   first 200 chars of msgs[0]: Here is a summary of the conversation to date: ...
   artifacts dir exists?: False
   ```
   即：**没有外置文件，原始对话在 checkpoint 里也被抹掉 —— 真的不可恢复。**
5. 附带丢失的还有 deepAgents 版的三项能力：`_offload_to_backend`（外置）、
   `_truncate_args`（大工具参数截断）、`_check_reduction`（超窗后重试/降级）。
   我们的类这三个方法**都不存在**（实测 `hasattr` 全 False）。

**还发现一处独立的静默截断**：`trim_tokens_to_summarize` 默认 4000（LangChain 的默认）。
实测 13560 token 的历史被压缩时，摘要模型只看到索引 68~102 的 35 条消息 ——
**索引 0~67 的历史既没进摘要、也没被保留**，直接消失。原因：LangChain 版在 `trim_tokens_to_summarize`
之外的部分直接丢弃，而 deepAgents 版的默认是 `None`（全量送摘要）。

**影响**：长会话里 agent 会"忘记"早期做过什么，且**无法回查**。
这直接打击自主迭代 —— 迭代的前提是"我记得上一轮试过什么、结果如何"。

**修法**（很小）：

- 方案 A（推荐）：把 `PlatformSummarizationMiddleware` 改成继承
  `deepagents.middleware.summarization.SummarizationMiddleware` 而不是 LangChain 的。
  deepAgents 版内部用 `self._lc_helper` 包了一个 LangChain 版并**显式透传 backend**，
  我们的 `backend=` 就不会再被吞。代价是要重写 `_should_summarize` 的挂钩点
  （deepAgents 版是 `wrap_model_call` 而非 `before_model`，阈值判定要挪位置）。
- 方案 B（保守）：保持继承 LangChain 版，但**去掉 `backend=` 这个无效传参**（别再假装有外置），
  并把 `trim_tokens_to_summarize=None`，至少保证"被丢的历史进了摘要"。
- 无论 A/B，都要改 `compaction.py:6-8` 的注释 —— 现在它在说谎。

---

## 4. 自主反思 —— 无

全仓 grep「反思/复盘/reflection/lesson/retrospect」**零命中**。
`agents/` 下没有任何 after-model / after-agent 后置钩子。

**现状最接近反思的三样东西，都不构成反思**：

1. **准则 3**（`prompts.py:55`）"写完代码后用 run_python 实际跑一遍" —— 这是**验证**，不是反思。
   验证的是"代码能不能跑"，反思是"我这次的做法对不对、下次该怎么改"。
2. **置信度机制**（`execution/task_state.py`）—— 语义错误扣 25 分、格式错误扣 10 分、
   低于 40 阻塞转人工（`task_state.py:31-39, 128-133`）。这是**故障保护**，不是反思：
   它统计"错了几次"，不产出"错在哪、为什么、下次怎么办"。
3. **调用记录表 `invocation`** —— 落库了完整轨迹（各阶段结论、风险判定、置信度），
   **但 agent 读不到它**：没有 `invocation_*` 工具暴露给模型（grep 确认）。
   这是一个**已经存在但没接上的**反思数据源。

**要补的**：

1. **最小可行**：把 `invocation` 的近期失败摘要做成一个只读工具（如 `invocation_recent_failures`），
   下一轮开始时注入 —— agent 就有了"上次卡在哪"的自我感知。数据已经在库里，成本低。
2. **中档**：在一次任务收尾时（agent 给出最终答复后）跑一次"复盘"——
   让模型回答"这次哪些步骤多余、哪个判断错了、下次怎么改"，产出写进记忆模块
   （注意：当前提示词禁止模型自记，见 §5）。
3. **高档**：任务失败被阻塞时，把阻塞原因结构化回灌给模型，让它换策略而不是重试。

---

## 5. 自主进化 —— 无，且被提示词主动堵死

> **修复状态**：本节的两处（提示词封堵、source 维度是死的且是反的）**已修**。
> 5.2（长期记忆默认不可用）、5.3（无淘汰、无 RAG 工具、无自动提炼）仍然开放。

### 5.1 提示词层面的封堵

`agents/prompts.py:60-63`（准则 7）：

> - 用户**明确说出**了值得长期保留的偏好、约定或已定结论时，用 memory_write 记下来；
> - **只记用户说的，不记你自己的推断**；一次只讲一件事，标签写具体点。

这条规则**直接把自省式写入挡在门外**。agent 试过一次、发现某个写法不行，
这个"经验"恰恰是模型推断出来的，按规则不能记。

> **已修**：准则改写为 —— 用户明确说出的照记；**模型自己**验证过的经验（"这类问题
> 上次是怎么解决的"）也允许记，但**必须验证过**，且要带上任务/技术关键词。
> 未经验证的推断仍然不许记（那会把幻觉写进长期记忆）。同时新增准则 7
> （先出计划再动手、遇阻要重规划），记忆准则顺延为 8。

**服务层并不禁止**：`MemoryService.remember()` 的 `source` 参数默认 `SOURCE_MODEL`
（`memory/service.py:227`），`memory_write` 工具也硬编码传 `SOURCE_MODEL`
（`builtin/memory.py:249`）—— 说明设计上就是允许模型写入的，只是提示词不让它写。

`SOURCE_USER` 的情况比"没有生产路径"更微妙一点，**准确的说法是**：
- 它在**长期记忆的读回**里有生产路径（`long_term_store.py:404`
  `source=str(meta.get("source") or SOURCE_USER)` —— 文档 meta 里没有 source 时**默认按 user 算**）；
- 但**没有任何写入路径会产生 `SOURCE_USER`** —— `memory_write` 恒写 `SOURCE_MODEL`，
  管理接口也没有"由用户直接写记忆"的入口（`/api/v1/memory` 只有查/删）。

结果是一个反向的问题：长期记忆里**读回来的是 user 标**，写进去的是 model 标，
而召回排序**完全不看 source 字段**（`_apply_scoring` 只算相关性/时间/使用次数，
`service.py:452-470`）。所以这个维度不只是"死的" —— 它还是**反的**，且被静默默认成
"用户说的"，模型写的东西因此在召回时看起来像是用户授权过的。

> **已修**：- `effective_score` 引入 `source` 维度（`SOURCE_WEIGHTS`：
>   user 1.0 / model 0.9）—— 来源参与排序，且**只微调不淘汰**（0.9 是再乘的系数，
>   压不过时间与使用次数）；
> - `long_term_store._to_entry` 的默认从 `SOURCE_USER` 改成 `SOURCE_MODEL` ——
>   读回来的是模型写的，就不该标成"用户说的"。

### 5.2 长期记忆默认不可用

`config.py:212` `memory_collection_name: str = ""` —— 集合要用户手工建。
未配时降级成"只有会话内记忆"（`builtin/memory.py:75-77`），不报错。
所以**跨会话的经验积累当前实际上是关闭的**。

### 5.3 记忆的其他结构性限制

- 召回无分数阈值（`long_term_store.py:377` `min_score = 0.0`）—— 降权只影响排序，不影响资格。
- **无过期淘汰**（文档明说"不基于时间自动淘汰"）—— 只有半衰期 30 天的排序衰减。
- 无 RAG 语料工具：`app/rag/` 实现了集合/文档/分块/混合检索，但
  **没有暴露成 agent 工具**（grep `rag_search` 在 `app/capabilities/` 零命中）。
  RAG 当前只被长期记忆一个消费者用。
- 写入**完全由模型决定**（只有 `memory_write` 一条路径），**没有系统自动写、没有执行后自动提炼**。

### 5.4 要补的

1. ~~**把来源维度用起来**：`source` 区分 `user`（用户说的）/ `model-inferred`（agent 自己总结的）。
   两者都存，但**召回排序上给 user 来源更高权重**，并修掉长期记忆读回时默认按 user 算的偏差
   —— 现在这个字段不但没参与排序，还被静默默认成"用户说的"。
   同时放开提示词：允许模型记"这类问题上次是怎么解决的"这类**经验**，但要求带上任务/技术关键词。~~ ✅
2. **执行后自动提炼**：任务收尾时把"这次的做法"写成一条 `kind=note, source=model-inferred` 的记忆。
   这是"进化"最小可用的形态。
3. **长期记忆开箱可用**：平台在首次启用时自动建集合并写好 `MEMORY_COLLECTION_NAME`，
   而不是让用户手工建（集合的向量化配置建时锁死 —— 这正是"不能自动建"的原因，
   但可以提供一个显式的一键初始化流程）。
4. **RAG 语料工具**：给 agent 一个 `rag_search`，让"仓库外的资料"可被检索。

---

## 6. 意图层：实际未做工具收敛

审查中发现的一个偏差（不是五维之一，但影响执行效率与安全面）：

`allowed_tool_keys` 这个参数**贯穿了 builder → middleware → tooling 三层，
但没有任何调用方设置它**（全仓 grep 确认）。后果：

- `tooling.py:52` 的过滤永远不生效 → **所有 enabled 工具全量暴露给模型**。
- `middleware.py:120-126` 的 plan 模式过滤是唯一真正生效的收敛。
- `services/agent.py:252-258` 的 git 能力剔除是另一处生效的收敛。

所以 README 里写的"检索技能 → 收敛工具"在代码里**没有对应实现**。
意图层（`app/capabilities/intent/`）不是意图识别器，是"tool call → 意图对象"的翻译器
（`intent/intent.py:3-9` 自己就是这么写的）—— 命名有误导，但功能描述是准确的。

这不是 bug，是**未完成**。要不要做取决于方向：
工具少的时候全量暴露没成本，工具多起来（现在 13 个内置 + MCP + 技能）就会挤占模型的注意力。

---

## 7. 风险清单（按严重度）

| # | 问题 | 位置 | 后果 | 状态 |
| --- | --- | --- | --- | --- |
| **R1** | **worker 跑在 `permissive`（= bypass）上** | `agents/worker.py:171` | 子 agent 里的破坏性操作**无人拦**。legacy 映射 `permissive→BYPASS` 见 `registration/policy.py:116-120`；`policy.py:231` bypass 直接 APPROVED，**且 bypass 分支在 `require_admin` 检查之前就返回**（实测：同一次 destructive 调用，`permissive`/`bypass` → approved，`auto`/`accept_edits`/`manual` → pending）。worker 本就无审批（`worker.py:172` `allow_interrupt=False`），加上 bypass，**参数级风险判定整段被跳过**（实测 `interrupt_enabled=False` 时 `_check_param_risk` 直接返回 None，连评估都不做）。唯一的防线是"自己的 worktree + 断网容器"这条物理隔离 | **已修**（判定照跑并记账，不拦） |
| **R1b** | **worker 能删掉别人的 worktree** | `builtin/git.py:173` `git_worktree_remove` + `worker.py:171` | `path` 参数**由模型给**，能力直接把它交给 `EnvironmentService.remove_worktree()`；而那个方法只校验"在白名单内 + 是平台建的 worktree"，**不校验归属**。实测：worker A 用 worker B 的路径调用，**B 的 worktree 被真删了**。加上 R1（bypass + 无审批），一个 worker 能干掉同仓库其他 worker 的产出。`git_worktree_add` 同理（能开别人的分支） | **已修**（能力层剔除 + 同仓库校验） |
| **R2** | **压缩丢历史且不可恢复** | `context/compaction.py:73` | 见 §3.2。长会话迭代链断，agent 无法回查 | **已修** |
| **R3** | **压缩静默截断被丢的历史** | `trim_tokens_to_summarize=4000` 默认 | 历史既不进摘要也不保留 | **已修** |
| **R4** | **平台无鉴权 + bypass 可自提权** | 平台级（记忆 `no-auth-for-now`） | 知道 `thread_id` 就能把会话设成 bypass，接口无认证。**这是本清单里唯一需要外部决策的一条** | **开放**（用户已决定开启 bypass） |
| **R5** | 主 agent 无显式步数/递归预算 | `agents/runner.py:176` `_config()` | 实际生效的是 deepAgents 绑的 **9999**（不是框架默认 25）—— 陷在循环里的 agent 能一口气跑几千轮；`config.py:69` 的 `agent_max_steps=20` 是死配置 | **已修** |
| **R6** | worker 无 checkpointer、无中断恢复 | `agents/worker.py:167` | worker 中途失败整个白跑；无断点续跑 | **开放** |
| **R7** | `compaction.py:6-8` 的注释与实现不符 | `context/compaction.py` | 文档说谎，误导后续维护者 | **已修** |

---

## 8. 建议的推进顺序

**第一梯队（低成本、高回报）** —— **1~4 已于同日完成**

1. ~~修压缩：继承改成 deepAgents 版（或至少 `trim_tokens_to_summarize=None`），
   并改正注释 —— **R2/R3/R7**~~ ✅
2. ~~主 agent 显式设 `recursion_limit`（把 `agent_max_steps` 从死配置接上）—— **R5**~~ ✅
3. ~~worker 的 `trust_mode` 从 `permissive` 改成 `auto`~~ —— **改成 `auto` 这条否决了**：
   worker 没有界面，`auto` 会挂起等不到人批，worker 直接卡死。改成**"不让人批但要记账"**：
   参数级判定照跑、风险写进调用记录、命中风险打 warning 日志 —— **R1** ✅
4. ~~`git_worktree_remove` / `git_worktree_add` 校验 worktree **归属**——
   改为"从 worker 的能力清单里整个剔掉 + `git_worktree_remove` 增加同仓库校验" ——
   **R1b** ✅
5. `invocation` 近期失败做成只读工具 + 每轮注入 —— 反思的最小形态　**← 下一个**

**第二梯队（补齐五维）**

6. 提示词加规划准则 + `write_todos` 支持"重规划"语义 —— 规划　**（准则已加，重规划语义待做）**
7. 记忆 `source` 语义落地 + 放开"记经验" + 执行后自动提炼 —— 进化　**（source 已落地）**
8. 长期记忆一键初始化 + `rag_search` 工具 —— 进化

**需要用户决策**

9. **R4（无鉴权 + bypass 自提权）** —— 用户已决定**开启** bypass 并做成可点击开关，
   风险随之**依然成立**。至少要保证"能设成 bypass 的接口"与"能调能力的接口"
   不是同一批无认证端点 —— 目前它们是。

---

## 附：核心结论的验证方式

本报告里最要紧的结论都是**实测**而非读码得出的（复核于 2026-11-05，backend 416 项测试全绿）：

| 结论 | 怎么验的 | 结果 |
| --- | --- | --- |
| 压缩外置失效 | 构造 `PlatformSummarizationMiddleware` 与 deepAgents 版**实例**对比 `_backend` / `_history_path_prefix` | ours 两个属性都 MISSING，base 是 `/conversation_history` |
| 替换机制本身工作 | 直接调 `deepagents.graph._apply_custom_middleware` | `_DeepAgentsSummarizationMiddleware` → `PlatformSummarizationMiddleware`，栈长仍为 1（不是挂了两份） |
| 历史真的不可恢复 | 跑真实 `create_agent` + 压缩后读 checkpoint | `msg number 0` 不在 checkpoint 里、artifacts 目录不存在 |
| 静默截断 | 120 条消息触发压缩，看送进摘要模型的内容 | 摘要模型只看到索引 68~102，**0~67 既没进摘要也没被保留** |
| worker = bypass | `normalize_mode('permissive')` + 各模式对比 `approval_decision` | permissive/bypass → approved；auto/accept_edits/manual → pending |
| bypass 连 require_admin 都忽略 | 同一 destructive 调用跨模式跑 `_decide_approval` | permissive/bypass 一律 approved |
| worker 参数级判定被整段跳过 | `interrupt_enabled=False` 时调 `_check_param_risk` | 直接返回 None，连评估都不做 |
| 参数级规则确实会挂起 | 用一个声明为 `write`（不在声明级名单）的工具喂 `shutil.rmtree` | 抛 interrupt → 人工审批 |
| worker 能删别人的 worktree | 建两个 worktree，用 A 的能力传 B 的路径调 `remove_worktree` | **B 被真删了**（已清理测试产物） |

### 修复后的验证（2026-11-05 晚）

| 结论 | 怎么验的 | 结果 |
| --- | --- | --- |
| 压缩后原始对话还在 | 跑真实 `create_agent` + 压缩后读 checkpoint | `msg number 0` **在** checkpoint 里 |
| 历史外置回来了 | 同一场景查 artifacts 目录 | 有 `conversation_history/*.md`，且 agent 能 `read_file` 读回 |
| 外置不进用户工作区 | 检查工作区里有没有 `conversation_history` | 没有（落点在 `/.artifacts/`） |
| 摘要涵盖全部历史 | 读中间件的 `trim_tokens_to_summarize` | `None`（全量送摘要） |
| 递归预算有限 | 读 `AgentRunner._config()` / worker 的 config | 主 agent `= agent_max_steps*2+20`（< 9999）、worker `= max_steps*2+20` |
| worker 拿不到破坏性 git | 跑一次 `run_worker`，看实际组出来的 spec 清单 | `git_worktree_remove` / `git_merge` 不在里面 |
| 跨仓库删 worktree 被拒 | 两个仓库各建 worktree，用 A 的上下文删 B 的 | 抛错，**B 的目录还在** |
| worker 的风险判定照跑 | `interrupt_enabled=False` 下调 `_check_param_risk` | 不挂起，但风险等级确实算出来（destructive） |
| 来源参与排序 | `effective_score` 对比 user / model | user > model，且使用次数与时间的权重仍压过来源 |
| 回归测试真的会咬人 | 把修复 stash 掉再跑新测试 | 旧代码**确实失败**（`msg number 0` 不在 checkpoint、无 `_history_path_prefix`、没设 `recursion_limit`） |
| 活体端到端 | 真模型（DeepSeek）跑 `run_python` 算 17×23，bypass 模式下自动放行 | 工具真的执行（289ms），模型回「**391**」 |

最后一条是关键：压缩中间件换基类是最容易把 agent 循环改坏的那种改动 ——
只有真跑一遍活体才知道它有没有把工具调用链打断。

**当前状态**：backend **431 项测试全绿**，ruff 干净。

### 复核时修正的三处

1. **R1 的措辞过宽**。原写法暗示"参数级判定被绕过"，实测是**整段不执行**
   （`interrupt_enabled=False` 提前返回），比"绕过"更彻底 —— 已按实测改写。
2. **SOURCE_USER 不是"没有生产路径"**。它在长期记忆**读回**时有路径
   （`long_term_store.py:404`），且默认按 user 算；真正的问题是**没有写入路径会产生它**，
   而召回排序又完全不看 source。已改成"这个维度是反的"这一准确说法。
3. **§1 对参数级风险的作用域说得太笼统**。它只对**未被声明级覆盖**的工具生效；
   `execute` + `rm -rf` 在 auto 模式下走的是声明级（档位仍按参数重算）。已补上分工说明。

新增 **R1b**（worker 能删别人的 worktree）—— 这是复核时实测出来的，原报告没有。

### 修复阶段的修正（2026-11-05 晚）

4. **R5 的前提是错的 —— 已更正**。原报告 §3.1 与 §7 写的是"主 agent 没设
   `recursion_limit` → 用框架默认 **25**"。**实测不是**：
   - deepAgents 的 `create_deep_agent` 用 `.with_config()` 把 `recursion_limit: 9999`
     绑在了图上；
   - LangChain 的 config 合并里，**调用时的值只有 != 25 时才覆盖绑定值**
     （`langchain_core/runnables/config.py:487`）；
   - 平台此前既没传 `recursion_limit`、也没改绑定值 → **实际生效的是 9999**，
     不是 25。

   所以真实情况比原报告写的**更糟**：不是"上限太紧（25）"，而是"**没有上限**"。
   一个陷在循环里的 agent 能一口气跑几千轮，token 与费用都是真的烧掉了。

   修复按 superstep 换算：一轮「模型 ↔ 工具」= 2 个 superstep，
   所以 `recursion_limit = 轮数 × 2 + 余量`。主 agent 用
   `agent_max_steps * 2 + 20`（默认 60 → 140），worker 用 `max_steps * 2 + 20`。
   `agent_max_steps` 从死配置接上。

   ⚠️ 这条是**先做错了再改对的**：第一版按"框架默认 25"的错误前提把上限设成
   `max_steps + 20 = 40` —— 那会把 9999 **收紧**到 40，属于把"没上限"变成
   "上限太紧"的另一种坑。实测 `merge_configs` 的覆盖规则后才发现。

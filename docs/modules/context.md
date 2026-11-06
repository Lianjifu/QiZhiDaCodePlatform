# 上下文模块

> 一句话：把记忆目录、策略说明、上传件清单、反思记录这些来源的内容，按预算
> 装成一份系统提示词，并在对话太长时接管框架的压缩。
>
> 前端入口：输入框底栏的「上下文占用」指示器（`ContextUsageIndicator.tsx`），
> 右侧会话状态面板的「上下文」卡片；排查用的预览接口是
> `GET /api/v1/context/preview`。

## 1. 这个模块是什么

上下文模块是 agent 层与各内容源之间的**装配层**：它决定"这一轮给模型看什么、
按什么顺序看、超出预算怎么办"。

它**不负责**：

| 不是它的活 | 归谁 |
| --- | --- |
| 召回到什么记忆、怎么排序 | 记忆模块（`app/memory/`） |
| 工具能不能执行、要不要人批 | 能力层（`app/capabilities/`） |
| 对话历史存在哪 | checkpointer |
| 摘要用什么模型、摘要写到哪 | 框架的摘要中间件（这里只做接管） |

代码落在 `backend/app/context/`，落点是 agent 层的一次调用：

```text
各来源 ──► ContextService.collect() ──► assemble() ──► 系统提示词
                              ▲
                              └── 预算（窗口 × 比例）
```

提示词本身**不是这个模块重写的**。基准提示词原样保留，各来源的内容按块登记、
按优先级装箱 —— deepAgents 会往 system prompt 上追加它自己的内容（文件工具
说明、虚拟路径映射），我们拼的只是其中一段。重写提示词等于跟框架打架。

### 1.1 为什么需要"块"

早先的提示词是几段字符串直接拼起来的：基准提示词 + 运行环境 + 策略说明 +
记忆目录。每段各自只知道自己的存在 —— 谁长谁就挤掉别人。记忆目录有它自己的
字符预算，但那是**它自己**的预算；基准提示词、运行时说明全都不限量。

改成**块**之后，每块自带标题、优先级与溢出策略，装配器按统一预算装箱，
谁被挤掉是可见的、可诊断的。

## 2. 接口清单

只有一条 —— 预览。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/v1/context/preview` | 预览这次会给模型看的上下文 |

查询参数：

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `query` | str | 用这句话召回记忆目录。空则不召回记忆块 |
| `thread_id` | str | 会话 ID。用于取反思记录与上传件清单 |
| `workspace` | str | 工作区路径。给模型解释路径该怎么用 |

响应（`app/schemas/context.py::ContextPreviewOut`）：

| 字段 | 说明 |
| --- | --- |
| `text` | 拼好的提示词正文 |
| `budget_chars` / `used_chars` | 本次预算与实际用量（字符） |
| `included` / `dropped` / `truncated` | 块诊断：装进去的、被丢的、被截断的 |
| `warnings` | 需要人知道的异常 |

**这个接口不跑模型、不改任何状态。** 记忆召回、反思查表、策略说明生成、
上传件扫描各自失败时都返回空串或跳过，不会让预览失败 —— 预览是排查工具，
它自己挂了就没法用了。

一个示例：

```bash
curl 'http://127.0.0.1:8000/api/v1/context/preview?query=pnpm&workspace=/tmp/ws'
```

预览**不包括**自动积累的提示词规则块（`prompt_rules`）—— 那块来自复盘表，
预览接口没有查它。

## 3. 实现要点

### 3.1 上下文块：`ContextBlock`

`app/context/blocks.py`。字段：

| 字段 | 作用 |
| --- | --- |
| `key` | 稳定标识（"memory" / "policy" / "uploads"…），排查与测试断言用 |
| `content` | 正文 |
| `priority` | 数字越小越先装，默认 50 |
| `title` | 独立成块的标题。**空串表示不单独成块** |
| `overflow` | 装不下时怎么办：`drop` / `truncate` / `compact` |
| `unit` | 计量单位（"chars" / "tokens"） |
| `required` | 必装块。装不下时**报错**而不是静默丢 |
| `notes` | 装配过程中的诊断信息 |

**为什么块要自带标题**：模型分不清"这是我要遵守的规矩"还是"这是背景资料"。
记忆目录是背景，工作区说明是规矩 —— 混在一个标题下，模型会把背景当指令执行。

**为什么溢出策略要分三种**：装不下时的处理不是一刀切。`DROP` 整块丢（记忆目录
丢了下一轮还在召回）；`TRUNCATE` 截断到剩余预算并留标记（工作区说明不能丢，
但规矩通常写在开头，砍尾比砍头好）；`COMPACT` 换成摘要（给"内容有先后、砍掉
中间就断片"的内容用）。

**`required` 与 `TRUNCATE` 的关系**：基准提示词既是 `required` 又是
`TRUNCATE` —— 装不下就截断，而不是硬塞进去把预算撑破。超预算的后果是**厂商
拒掉整个请求**（这一轮对话直接失败）；截断的后果只是模型少看到一段提示词。
两害相权，截断更轻。

### 3.2 块清单与优先级

`app/context/service.py::ContextService.collect`。各来源的块在这里汇合。

| key | 内容 | priority | overflow | required |
| --- | --- | --- | --- | --- |
| `base` | 基准提示词 | 0 | truncate | ✅ |
| `runtime` | 运行环境（路径怎么用） | 10 | drop | |
| `policy` | 策略说明（哪些工具要人批） | 20 | drop | |
| `prompt_rules` | 自动积累的规则（复盘学的） | 22 | drop | |
| `uploads` | 上传件清单 | 25 | drop | |
| `workspace` | 工作区说明 AGENTS.md | 30 | — | 未实现 |
| `reflection` | 反思记录（最近失败） | 35 | drop | |
| `memory` | 记忆目录 | 40 | drop | |

优先级不是随手排的：

- **`prompt_rules`（22）排在 `policy`（20）之后**：代码里的准则永远在前、永远
  完整 —— 那是 agent 的安全底线。复盘学到的行为准则是补充。
- **`uploads`（25）比 `workspace`（30）高**：上传件是**本轮的工作材料**，
  AGENTS.md 里的项目规矩晚一轮再读也来得及。
- **`reflection`（35）比 `memory`（40）高**：记忆目录是背景资料，丢了下次还能
  再召；反思记录是"你刚才失败过"，丢了 agent 会在同一个坑里再摔一次，而且它
  自己不知道。重复摔同一个坑要花真的 token 与时间。

**`workspace` 块目前没有实现**。`KEY_WORKSPACE` 与 `PRIORITY_WORKSPACE`
只声明并导出，没有对应的 `ContextBlock`；AGENTS.md 实际走 deepAgents 的
`MemoryMiddleware`（`runner._build()` 里 `memory=[memory_source]`），不在这个
模块的预算控制范围内。

### 3.3 装配算法

`app/context/assembler.py::assemble`。步骤：

1. **过滤空块** —— 空块不占预算，也不该在提示词里留个空标题。
2. **稳定排序** —— 按 `(priority, 原序号)` 排。同 priority 保持原顺序，
   所以"稳定"是显式做的。
3. **逐块装箱** —— 块的成本是 `len(block.rendered()) + 2`。渲染后长度含标题，
   `+2` 是块之间的分隔符。装得下就装。
4. **装不下时按 overflow 处理**：
   - 必装且不是 `TRUNCATE` → **强制装入 + 记 warning**。这是配置问题，不是
     运行时问题，要明确报出来。
   - `TRUNCATE` → 截断到剩余预算（**保留头部** + 留"已截断"标记）。截不出内容
     就当丢掉。
   - `COMPACT` → 丢掉并记 warning（压缩要额外一轮模型调用，这里只标记）。
   - 默认 `DROP` → 丢掉。
5. **丢 `policy` 块要额外记 warning**（`_note_drop`）—— 那意味着模型不知道
   哪些操作要人批，会反复触发审批。
6. **按 priority 排回去** —— 高优先级的块应该在提示词前面。
7. **`separator.join`** —— 默认 `"\n\n"`。

**为什么截断要留标记**：静默截断会让模型以为看到的是全部。标记本身的长度算
进预算 —— 它自己占位置，不能让它把预算撑破。

**`used_chars ≤ budget_chars` 在正常情况下成立**，唯一例外是第 4 步里那个
"必装且不可截断"的块被强制装入。超预算的后果是厂商拒请求，所以宁可截断。

### 3.4 预算：一个窗口怎么分

`app/context/budget.py`。

```text
窗口（模型的 max_input_tokens）
  ├─ 系统提示词  0.5 × 窗口   ← 提示词、记忆目录、策略说明、上传件清单
  ├─ 对话历史    0.5 × 窗口   ← 压缩阈值按这个算
  └─ 余量        0.15 × 窗口  ← 模型输出、工具 schema、框架追加的提示词
```

- `budget_for_window(window, ratio=CONTEXT_BUDGET_RATIO 0.5, chars_per_token=4.0)`
  = `window × ratio × chars_per_token`。`chars_per_token` 取 4 是中英文折中
  —— 英文大约 4 字符/token，中文大约 1.5。
- **窗口未知用 `FALLBACK_CONTEXT_WINDOW = 128000`**。不猜：猜大了会在真正窗口
  用满前就开始压缩（浪费），猜小了会压缩得太晚、直接撞上厂商报错。
- **对话历史的份额是减出来的**：`conversation_budget_ratio()` = `1 - context_budget_ratio`。
  不是另定一个数。早先两份预算互不知情（系统提示词 0.5×窗口、对话 0.85×窗口），
  一次请求能到 1.35 倍窗口，厂商直接拒。
- **整个请求的上限是 `TOTAL_REQUEST_FRACTION = 0.85`**。压缩中间件拿厂商报回来
  的 `total_tokens`（整个请求的真实大小）跟它比。

**窗口从哪来 —— 两条路同一个口径**：

| 来源 | 窗口怎么定 |
| --- | --- |
| 数据库供应商配置 | 配置上的 `context_window`，没填就取厂商预设的 |
| `.env` 兜底 | `LLM_CONTEXT_WINDOW`，没写（0）就取厂商预设的 |

两条路的"有效窗口"都由 `app/agents/llm.py::env_context_window()`（兜底那条）
与模型对象上的 `profile["max_input_tokens"]` 给出，**上下文预览接口算预算时
调的是同一个函数** —— 预览报的字符预算跟模型实际挂着的窗口永远是同一个数。
预览说 17 万 token 而模型其实有 100 万，排查时会被带偏。

**预算一律按字符算**，不按 token。token 数要分词器，而不同厂商的分词粒度差
好几倍；用字符更可预测，也更好解释。

### 3.5 每轮都变的三块：占位符机制

记忆目录、上传件清单、反思记录这三块**每轮都不一样**。它们被编进图里，图
就不能跨轮复用 —— 复用等于让 agent 一直看着第一轮的旧记忆。

所以编译期只留**占位符**（`app/agents/dynamic_context.py`）：

| 占位符 | 内容 |
| --- | --- |
| `<!--qzd:memory-catalog-->` | 记忆目录 |
| `<!--qzd:uploads-->` | 上传件清单 |
| `<!--qzd:reflection-->` | 反思记录 |

真实内容放在 `PromptHolder` 里，由 `DynamicContextMiddleware.awrap_model_call`
在**调模型之前**替换占位符。

**为什么是替换而不是追加**：上下文模块是按优先级装箱的 —— 上传件（25）在策略
说明（20）后面、记忆目录（40）在最后。追加到末尾会把顺序打乱，模型先读到
的内容就不对了。占位符让装配器照常排位，中间件只换内容。替换后为空的块直接
丢掉（这一轮没有记忆、没传附件时，提示词里不留几行空行）。

`runner._fill_holder` 每轮重填这三块。因为内容不编进图里，**图可以跨轮复用**
（`GRAPH_CACHE`）。

代价：装配器算预算时看到的是**占位符的长度**（几十个字符），不是真实目录的
长度。记忆目录的溢出风险由记忆模块自己的召回条数与预算兜着。

### 3.6 反思记录：`recent_failures`

`app/agents/reflection.py`。每轮注入最近几次没成功的调用，渲染成 XML 块：

```xml
<recent_failures>
本会话最近几次没成功的调用（你自己的记录）：
- run_python（退出码 1）：ValueError: name 'x' is not defined
别重复同样的调用 —— 换个做法，或调用 invocation_recent_failures 看完整报错。
</recent_failures>
```

末尾那行是必须的：光列条目，模型只会读，不会因此换个做法。

**什么算"失败" —— 两层**。判定放在 `app/models/invocation.py::is_failure`，
反思块、复盘候选筛选、复发率统计三处共用：

1. **记录的状态** —— `failed` / `blocked` / `denied` / `expired`；
2. **退出码非零** —— 工具本身跑成了（记录是 `succeeded`），但要做的事没做成。

第二层是代码 agent 最常见的失败形态。`run_python` 的职责是"在沙箱里跑一段代码
并把退出码与输出带回来" —— 代码抛异常时这个职责**履行了**，所以记录是成功的。
但从 agent 的角度那次尝试就是失败了：它想算的东西没算出来。只看状态的话，
反思块**恰恰在代码 agent 最常见的失败场景里是空的**。

**为什么不改记录的状态**：状态还喂给置信度机制（失败会扣分、低于阈值就阻塞
任务）。把"测试跑挂了"算成能力失败，会让"写完代码跑一遍"这条工作准则每次都
把任务扣到阻塞线 —— 那是把记录改坏，不是改准。所以只改**读取时的判断**。

规模控制：`SCAN_WINDOW = 20`（往前看多少条调用记录）、`MAX_INJECTED = 5`
（最多报几条）、`MAX_MESSAGE_CHARS = 120`（单条错误截到多长，完整报错留给工具）。
窗口不是条数上限 —— 窗口的作用是把陈旧的失败挤出去：后面又跑了几十次调用，
早先那次失败就不在窗口里了。

### 3.7 压缩：接管 deepAgents 的摘要中间件

`app/context/compaction.py`。

deepAgents 已经挂了 `SummarizationMiddleware`，而且比 LangChain 原版多做了一件事：
**被压缩掉的历史先落到 backend 的文件里**（`conversation_history/{id}.md`），
摘要里带上这个路径，agent 需要时能用 `read_file` 回去翻。LangChain 原版是直接
丢 —— 不可恢复。自己再写一套摘要，等于有两个地方在改历史，谁先谁后说不清。

**做法**：继承 deepAgents 版（**不是 LangChain 版**），`name` 保持
`"SummarizationMiddleware"`，放进 `middleware=[...]` —— deepAgents 的
`_apply_custom_middleware` 按名字替换，位置不变。

继承 deepAgents 版而不是 LangChain 版，是因为差的两样正好是压缩最要紧的两件
事：被压缩的历史外置成文件、`state["messages"]` 不被改写。早先继承的是
LangChain 版，而 deepAgents 版的构造函数把 `backend` 当**必填关键字参数**，
我们传进去的 `backend=` 被 LangChain 的 `**deprecated_kwargs` **静默吞掉**
（那个 `__init__` 只认 `max_tokens_before_summary` / `messages_to_keep`）。
实测后果是**压缩之后原始对话彻底没了** —— 既没有外置文件，checkpoint 里的
消息也被重写掉了。

改了三处：

| 参数 | 默认 | 这里 | 为什么 |
| --- | --- | --- | --- |
| `backend` | 必填 | 传平台后端 | 少了它就没有历史外置 |
| `trigger` | 看 profile | 见下 | 阈值 |
| `keep` | 比例 0.1 / 6 条 | 比例 0.1 / 绝对 20 条 | 6 条对代码 agent 太少 |

**阈值从哪来**：窗口已知（模型挂了 `profile["max_input_tokens"]`）用 fraction
（`trigger` = 对话的份额 = `1 - 0.5` = 0.5，`keep` = `KEEP_FRACTION` 0.10）；
窗口未知退回绝对值（`FALLBACK_TRIGGER_TOKENS` 100000 token / `FALLBACK_KEEP_MESSAGES`
20 条）。**窗口未知时不能用 fraction** —— langchain 的摘要中间件在用比例而
模型没有 profile 时会**直接抛 ValueError**（"Model profile information is
required..."），整个 agent 起不来。

**`trim_tokens_to_summarize=None`**：框架默认只送最后 4000 token 进摘要，更早的
那部分**既不进摘要也不被保留**，直接消失（实测：120 条消息里索引 0~67 的内容
就这么没了）。设成 `None` = 全量送进摘要。

**`_should_summarize` 拆两个阈值**：框架只有一个阈值，可它要同时跟两种度量比 ——
`token_counter(messages)` 只算对话，`usage_metadata["total_tokens"]` 是整个请求
（含系统提示词与工具 schema）。拿同一个阈值比两者，要么提前压缩（把系统提示词
的额度也算进对话），要么放任请求超窗被厂商拒。所以拆成：对话超过对话的份额
就压缩；整个请求超过 `TOTAL_REQUEST_FRACTION` 也压缩。

### 3.8 不变量：改错了会出事的地方

这一节是本文档里最值钱的部分 —— 这些坑是**实测复现过的**，不是设想。

| 不变量 | 改错了会怎样 |
| --- | --- |
| **`artifacts_root` 指到工作区外面**（`PlatformBackend` 继承 `CompositeBackend`，`_fix_artifacts_root` 兜底） | deepAgents 用 `isinstance(backend, CompositeBackend)` 判定 —— 只**包着**它就判定为 False，产物目录退化成 `"/"` —— 被压缩掉的对话历史（含用户说过的话、agent 读到的代码）落进用户的工作区，可能被 `git commit` 一起提交上去 |
| 压缩中间件的 `name` 必须与框架默认一致（`"SummarizationMiddleware"`） | deepAgents 按 name 替换默认项，名字对不上就变成"多挂了一个"，两份摘要中间件同时跑。升级 deepAgents 时这条会**静默失效** |
| 必须继承 **deepAgents 版**，不能继承 LangChain 版 | LangChain 版的 `__init__` 只认 `max_tokens_before_summary` / `messages_to_keep`，我们传的 `backend=` 被 `**deprecated_kwargs` **静默吞掉** —— 历史外置整个失效，压缩之后原始对话彻底没了，且无法回查 |
| `trim_tokens_to_summarize=None` | 框架默认只送最后 4000 token 进摘要，更早的那部分**既不进摘要也不被保留**，直接消失（实测 120 条消息里索引 0~67 的内容就这么没了） |
| 必装块装不下时**报错**，不能静默丢 | `policy` 块丢了模型不知道哪些操作要人批，会反复触发审批，用户看到的是"这 agent 怎么一直要确认" |
| 丢 `policy` 块必须记 warning | 静默丢是排障时最难查的一类问题 —— 界面上的症状（反复要审批）与真正的原因（策略说明被挤掉了）之间没有任何线索 |
| 窗口未知时**不能给 fraction** | langchain 的摘要中间件在用比例而模型没有 profile 时直接抛 `ValueError`（"Model profile information is required..."），整个 agent 起不来 |
| 占位符**替换**而不是追加到末尾 | 上下文模块是按优先级装箱的，追加会把顺序打乱 —— 模型先读到的内容就不对了（上传件 25 在策略说明 20 后面、记忆目录 40 在最后） |
| 上下文占用是**估算**（4 字符约 1 token） | 别拿它跟厂商的计费 token 数对账 |

### 3.9 技能清单配额

`app/context/skills_quota.py::QuotaSkillsMiddleware`。

deepAgents 的技能中间件会把**全部**技能的 name + description 塞进系统提示词
（正文靠模型自己 `read_file` 按需读 —— 那部分是对的）。技能少的时候没问题，
但清单本身没有上限：五十个技能就是几千 token，每轮都付。

做法是**同名替换**（`name = "SkillsMiddleware"`）并覆写私有方法
`_format_skills_list`，默认最多列 `DEFAULT_MAX_SKILLS = 20` 个技能。超出的
在清单末尾留一行"- 还有 N 个技能未列出（按名称或描述查找；技能目录可用 ls
查看完整列表）" —— 静默截断会让模型以为只有这几个技能。

不重写整个中间件 —— 技能加载、渐进披露、动态工具挂载那些机制都是框架的。

### 3.10 前端：上下文占用

输入框底栏（`Composer.tsx`）有一根「上下文占用」指示器，在**模型选择的左边**
—— 上下文是这一轮对话的事，就该待在输入框这一行。窄屏上收起来（底栏塞不下
四样东西）。

- 一行"上下文 15.6%"加一根小进度条，点开弹明细。
- 明细里逐块列出：对话消息 / 系统提示词 / 工具定义 / MCP 工具 / 技能 / 记忆文件，
  各占多少。MCP 工具与记忆文件可以展开看具体是哪些。
- **新会话是 0%** —— 可请求还没发出去，占用的就是 0，弹层写"还没开始对话 ——
  发出第一条消息才会占用窗口"。
- **压缩缓冲不算占用** —— 它是预留出来放摘要的那段（窗口用到 85% 触发压缩），
  单列一行标「压缩缓冲（预留）」，**不计入已用，但要从剩余空间里扣掉**。
- **指示器浮层与右栏会话状态面板的「上下文」卡片共用同一份明细渲染**
  （`ContextBreakdown.tsx`），数据也来自同一个接口（`useSessionStatus.ts`）。
  同一个接口读两遍，数字会对不上。

轮询 30 秒一次（标签页不可见时不读），并且这一轮跑完时立刻重读一次 ——
光靠轮询太慢，一轮对话刚结束数字还停在上一轮，看着像没变。

数据来自 `GET /api/v1/agent/{thread_id}/status` 的 `context` 字段
（`app/agents/status.py::build_context_usage`）。

## 4. 已知限制

1. **摘要暂时用主模型。** `summarizer` 参数的口子留着，没接独立配置。压缩是
   纯信息搬运，用旗舰模型不划算。

2. **`keep` 的下限 20 条落不到框架参数上。** langchain 的 `keep` 只吃单个子句
   （"fraction 0.1" 或 "messages 20"），不支持"至少 20 条"这种组合语义。
   现在窗口已知用比例（1M 窗口下保留 10 万 token ≈ 几百条，本来就够多），
   窗口未知才用"保留 20 条"这个绝对值。

3. **接管摘要中间件有版本耦合。** 升级 deepAgents 时同名替换会**静默失效**，
   变成两份摘要中间件同时跑（或我们那份被忽略）。

4. **截断工作区说明，agent 可能违反项目规矩。** 对策是截断保留**头部**（规矩
   通常在开头）并留"已截断"标记 —— 但尾部被砍掉的部分里的规矩，模型确实没看到。

5. **策略说明被丢掉 → 模型反复触发审批。** 丢掉时会记 warning，策略说明本身
   也很短，实际很难被挤掉。

6. **`TIER_REQUIRED_MAX = 20` / `TIER_TRUNCATABLE_MAX = 40` 声明并导出，
   但 `assemble()` 从不使用。** docstring 描述的"分档装箱"没有实现 —— 实际是
   排序后依次装。这两个常量目前是死代码。

7. **`workspace` 块（优先级 30）没有实现。** `KEY_WORKSPACE` / `PRIORITY_WORKSPACE`
   只声明导出，没有对应的 `ContextBlock`；AGENTS.md 实际走 deepAgents 的
   `MemoryMiddleware`。

8. **`OVERFLOW_COMPACT` 策略没有调用方。** 装配器实现了这条分支，但没有任何块
   声明这个策略。压缩是框架摘要中间件的活。

9. **占位符长度进预算，真实内容长度不进。** 装配器算的是占位符的几十个字符。
   记忆目录的溢出风险由记忆模块自己的召回条数与预算兜着，上传件与反思记录
   本身有长度上限。

10. **`ContextBlock.measure()` 的 `unit` 参数无效。** `unit == "tokens"` 与
    `unit == "chars"` 都返回 `len(content)`。而且 `measure()` 在整个代码库里
    没有调用点 —— 装配器用的是 `_cost()`（渲染后长度 + 2）。

11. **窗口未知时用 128000 兜底。** 拿不到窗口时不知道真实的压缩点，可能压缩
    得比必要更早（浪费）或更晚（撞厂商报错）。

## 5. 相关文件

```
app/context/__init__.py
app/context/blocks.py         ContextBlock：标题、优先级、溢出策略、截断
app/context/assembler.py      装配：按优先级装箱，产出诊断信息
app/context/budget.py         预算：窗口 × 比例，对话份额是减出来的
app/context/compaction.py     接管 deepAgents 的摘要中间件（同名替换）
app/context/service.py        对外入口：collect + assemble，块的清单与优先级
app/context/skills_quota.py   技能清单配额（同名替换 SkillsMiddleware）
app/schemas/context.py        预览接口的响应模型
app/api/v1/context.py         GET /context/preview
```

接入点（既有文件）：

| 文件 | 改什么 |
| --- | --- |
| `app/agents/runner.py` | `_build()` 走上下文模块装配；`_fill_holder` 每轮重填三块 |
| `app/agents/dynamic_context.py` | 占位符与 `PromptHolder` |
| `app/agents/reflection.py` | 反思块的渲染与失败判定 |
| `app/agents/prompts.py` | `render_prompt_rules`（自动积累规则的块） |
| `app/agents/builder.py` | 挂压缩中间件与带配额的技能中间件 |
| `app/agents/status.py` | `build_context_usage`（面板上的占用明细） |
| `app/capabilities/backends/assembly.py` | `artifacts_root` 指到工作区外 |
| `app/models/invocation.py` | `is_failure`（失败判定的唯一入口） |
| `app/api/router.py` | 挂载上下文路由 |

前端：

```
frontend/src/features/sessions/ContextUsageIndicator.tsx  底栏指示器
frontend/src/features/sessions/ContextBreakdown.tsx       明细（两处共用）
frontend/src/features/sessions/contextFormat.ts            颜色与数字格式
frontend/src/features/sessions/useSessionStatus.ts         共享的数据读取
frontend/src/features/agent/Composer.tsx                   指示器的落位
```

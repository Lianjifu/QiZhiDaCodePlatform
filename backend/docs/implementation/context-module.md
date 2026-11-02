# 上下文模块方案

> 状态：**已实现**（2026-11-02）
>
> 关联文档：[implementation/memory-module.md](memory-module.md)（记忆模块，已实现）、
> [implementation/rag-architecture.md](rag-architecture.md)（RAG 服务层，已实现）、
> [implementation/capability-module.md](capability-module.md)（能力层，已实现）、
> [plans/rag-followups.md](../plans/rag-followups.md)（RAG 待办）

上下文模块负责**一次模型调用看到什么**：把多来源的内容（记忆目录、工作区说明、
运行时环境、对话历史）拼成一份有预算的结构化上下文，并在超出预算时压缩。

**它不负责**：召回到什么（那是记忆模块）、怎么执行（能力层）、对话历史存哪
（checkpointer）。它只管**装配与压缩**。

---

## 0. 结论速览

| 面 | 结论 |
| --- | --- |
| 定位 | agent 层与各内容源之间的**装配层**：决定"这次给模型看什么" |
| 拼接 | **不重写提示词** —— 基准提示词原样保留，各来源的内容按块登记、按配额装箱 |
| 压缩 | **接管 deepAgents 的摘要中间件**（同名替换），而不是自己另造一套 |
| 触发 | 按**模型实际窗口比例**算，不写死 token 数 —— 当前 DeepSeek 是 1M 窗口 |
| 已修的缺口 | 模型的 `max_input_tokens` 没告诉框架 → 摘要阈值退化成 170k / 保留 6 条（✅ X0 已修） |
| 落点 | `app/context/`（新模块），agent 层只做调用 |

---

## 1. 实施前的现状：上下文是怎么拼出来的

> 这一节记的是**动手之前**的样子，留着是为了说明"为什么要做这个模块"。
> 实施后的形态见 §3。

一次请求真正发出去的 system prompt 是四段拼的：

```text
CODE_AGENT_PROMPT（基准提示词）
  + deepAgents 自己追加的（文件工具说明、write_todos 用法、虚拟路径映射…）
  + assemble_prompt() 拼的运行时说明（"运行环境："）
  + CapabilityMiddleware 每次调用前追加的策略说明（哪些工具要人批）
```

而 `assemble_prompt` 拼的块是这四类（`app/capabilities/prompt.py`）：

| 块 | 来源 | 现在的形态 |
| --- | --- | --- |
| 基础提示词 | `app/agents/prompts.py` 常量 | 固定 |
| 技能知识 | `CapabilitySpec` | `assemble_prompt(skills=...)` —— **实际没走这条路** |
| 运行环境 | `runner._build()` 里的 `runtime_notes` | 一行路径说明 |
| 额外块 | `runner._memory_block()` | 记忆目录，独立成块 |

### 1.1 现在的问题不在"拼"，在"没有预算"

四类块**各自都不知道别人的存在**，谁长谁就挤掉别人：

- 记忆目录有 1200 字符预算（`MEMORY_CATALOG_BUDGET_CHARS`），但那是**它自己**的预算；
- 运行时说明、策略说明、基准提示词全都不限量；
- 基准提示词 ~1200 字符，deepAgents 追加的那几段我们自己控制不了；
- 技能正文按需读是对的，但**技能列表**（name + description）每轮都进提示词，涨起来没上限。

所以"拼"其实已经能拼，缺的是**统一的预算与配额**。

### 1.2 更要紧的问题：压缩阈值是错的

deepAgents 自动挂了 `SummarizationMiddleware`，但阈值由
`compute_summarization_defaults(model)` 决定 —— **看模型有没有 `profile.max_input_tokens`**：

```python
有 profile → trigger=("fraction", 0.85), keep=("fraction", 0.10)
没 profile → trigger=("tokens", 170000), keep=("messages", 6)
```

我们的模型是 `init_chat_model("openai:deepseek-flash", ...)` —— **OpenAI 兼容端点，
模型对象上没有 profile**（实测 `profile` 为 `None`）。所以走的是第二行：

> **触发线 170000 token，只保留最近 6 条消息。**

而实测 DeepSeek 官方 `/models` 接口返回：

```json
{"id": "deepseek-flash", "context_window": 1048576, "max_output_tokens": 393216}
```

窗口是 **1M token**。170k 触发意味着：

- 明明还有 83% 的窗口没用，就提前开始压缩；
- 压完只留 6 条消息 —— 对代码 agent 来说，一轮就是好几条工具调用，6 条几乎等于"只留当前这一轮"；
- 压缩本身要调一次模型，170k 之后就频繁触发。

**这是当前最该修的一处。** 修法很轻：给模型对象挂上 profile。

```python
model.profile = {"max_input_tokens": 1048576}   # 实测有效，阈值变成 0.85 / 0.10
```

挂上之后：触发线 850k、保留 10%（≈100k token）—— 跟 1M 窗口匹配。

---

## 2. deepAgents 已经给了什么（别重造）

实测 + 读源码确认，框架自带这些：

| 能力 | API | 默认 | 能否接管 |
| --- | --- | --- | --- |
| 自动摘要 | `SummarizationMiddleware` | 装 | ✅ **同名替换**（实测有效） |
| 摘要阈值 | `trigger` / `keep` | 看 profile | ✅ 替换时传参 |
| 摘要模型 | `model=` / `summarizer=` | 同主模型 | ✅ 可换便宜模型 |
| 被压缩的历史外置 | 写到 backend `conversation_history/{id}.md` | 开 | ❌ 不可关 |
| 工具结果大对象驱逐 | `FilesystemMiddleware(tool_token_limit_before_evict=20000)` | 开 | ✅ 传参 |
| 工具参数预裁剪 | `truncate_args_settings` | 关 | ✅ 传参 |
| 二进制外置 | `offload_binary_content` | 关 | ✅ 传参 |
| AGENTS.md 注入 | `MemoryMiddleware` | 只在传 `memory=` 时 | ✅ |
| 技能渐进披露 | `SkillsMiddleware` | 只在传 `skills=` 时 | ✅ |

**外置历史那一条要特别注意**：被摘要掉的消息会**先写到 backend 的
`/conversation_history/{session_id}.md`**，摘要里带上这个路径，agent 需要时可以用
`read_file` 回去翻。这是 deepAgents 相对 LangChain 原版的关键改进（原版直接丢，
不可恢复）。

但我们的 backend 是 `CompositeBackend`，路由是 `"/" → FilesystemBackend(工作区)`
—— 所以这个 `.md` 会落在**工作区里**，agent 的文件工具能直接看到。这既是好事
（可回溯），也是风险（用户能在工作区里看到自己的对话历史，含可能被脱敏遗漏的
内容）。方案里 §6 单列这一条。

### 2.1 挂载点：中间件栈序（实测）

deepAgents 的栈是**靠前 = 外层 = 先改 request**：

```text
FilesystemMiddleware → SubAgent → ParentNotifier → Summarization → PatchToolCalls
  → 【用户中间件】 → Skills → prompt-caching → Memory(HITL 之后)
```

实测印证（灌 3 轮历史，观察进入顺序）：

```text
('Summarization 进入', 1)
('UserMiddleware 进入', 1)      ← 我们在里层
```

**含义**：我们自己的中间件拿到的 `request.messages` **已经是压缩后的**。
要拿全量原始历史，得自己替换 `SummarizationMiddleware`（同名替换时接管它）。

---

## 3. 模块设计

### 3.1 定位

```text
        记忆模块        工作区说明      运行时环境      技能
            │              │             │            │
            └──────────────┴──────┬──────┴────────────┘
                                  ▼
                         ┌──────────────────┐
                         │   上下文模块     │
                         │  装配 + 预算     │
                         └────────┬─────────┘
                                  ▼
                          system prompt
```

模块只做三件事：

1. **收集块** —— 各个来源把内容交过来，模块登记；
2. **配额** —— 按优先级把块装进预算，装不下的按规则降级；
3. **压缩** —— 历史太长时触发摘要（接管框架的摘要中间件）。

### 3.2 目录

```text
app/context/
  __init__.py
  blocks.py         上下文块的抽象：标题、内容、优先级、溢出策略
  assembler.py      装配：按优先级与配额把块拼成最终 prompt
  budget.py         预算计算（按模型窗口的比例）
  compaction.py     接管 deepAgents 的摘要中间件（同名替换）
  service.py        对外入口：collect + assemble
  skills_quota.py   技能清单配额（同名替换 SkillsMiddleware）
```

### 3.3 上下文块：一条统一抽象

现在四类块散在四个地方，各有各的拼法。统一成一种：

```python
@dataclass
class ContextBlock:
    """一次上下文里的一个块。

    块是**自带的** —— 标题、顺序、降级方式都写在块里，组装器只管按配额
    装箱。混在一起拼字符串的老做法没法做预算：谁长谁就挤掉别人。
    """

    key: str                    # 稳定标识（"memory" / "workspace" / "runtime" / "policy"）
    title: str                  # 独立成块的标题
    content: str
    priority: int               # 数字越小越先装（必填）
    # 装不下时可以怎么处理：
    #   DROP     —— 整块丢掉（默认）
    #   TRUNCATE —— 截断到剩余预算
    #   COMPACT  —— 换成摘要（历史块用）
    overflow: str = "drop"
    # 计量单位：字符还是 token
    unit: str = "chars"
```

**为什么块要自带标题**：模型分不清"这是规则"还是"这是背景资料"。记忆目录是
背景，工作区说明是规则 —— 混在一个标题下模型会当背景资料执行。

**为什么 overflow 要分三种**：记忆目录装不下可以丢（下一轮还在），但工作区
说明丢了 agent 就会违反项目规矩 —— 那种要报错，不能静默丢。

### 3.4 优先级与配额

| 块 | priority | overflow | 装不下时 |
| --- | --- | --- | --- |
| 基准提示词 | 0 | truncate | 不允许（永远装得下） |
| 运行时环境 | 10 | drop | 丢（模型能自己 ls） |
| 策略说明（审批） | 20 | drop | 丢（**但要警告**） |
| 上传件 | 25 | drop | 丢 |
| 工作区说明 AGENTS.md | 30 | truncate | 截断 |
| 记忆目录 | 40 | drop | 丢 |

「上传件」块（2026-11-02 加）：工作区里用户传过的材料清单。它比工作区说明
更该保住 —— 它是**本轮的工作材料**，而 AGENTS.md 里的项目规矩晚一轮再读也
来得及。详见 [file-module.md](file-module.md)。

配额怎么分，用**总预算的百分比**而不是绝对值 —— 不同模型的窗口差 10 倍：

```python
CONTEXT_BUDGET_RATIO = 0.5   # 上下文最多占窗口的一半，另一半留给输出与工具 schema
```

剩下那一半留给什么：输出（`max_tokens` 4096）+ 工具 schema（十来个工具的 JSON
schema，几千 token）+ deepAgents 自己追加的提示词。

**策略说明被丢掉要警告**：那意味着模型不知道哪些操作要人批，会反复触发审批。

### 3.5 压缩：接管，不重造

**做法**：构造一个 `SummarizationMiddleware` 子类，`name` 保持
`"SummarizationMiddleware"`，放进 `middleware=[...]` —— deepAgents 的
`_apply_custom_middleware` 按名字替换，位置不变（实测有效）。

```python
class PlatformSummarizationMiddleware(SummarizationMiddleware):
    """平台的压缩中间件。

    名字必须与框架的一致 —— deepAgents 按 name 替换默认项，名字对不上
    就变成"多挂了一个"，两份摘要中间件同时跑。
    """
    name = "SummarizationMiddleware"
```

三个要改的地方：

| 参数 | 改成 | 为什么 |
| --- | --- | --- |
| `model` | **独立的便宜模型** | 压缩是纯信息搬运，用主模型（贵的）不划算 |
| `trigger` | 按窗口比例 | 见 §1.2 |
| `keep` | 按比例，且**下限 20 条** | 6 条对代码 agent 太少 |

**摘要用便宜模型**：压缩的产物是"过去发生了什么"，不需要旗舰模型的推理能力。
但**摘要模型必须是同一个供应商的** —— 摘要文本会进主对话，跨厂商会有格式与
风格差异，而且多一次网络往返。

`keep` 的下限 20 条是个经验值：代码 agent 一轮就有好几次工具调用，6 条等于
只留最后一轮，agent 会忘记自己刚改过什么。

### 3.6 模型 profile：先把阈值修对

这是 §1.2 的修法，也是整个方案里**性价比最高的一处**。

给模型挂上 profile，框架的阈值自动从"170k / 6 条"变成"85% / 10%"：

```python
# app/agents/llm.py
model = init_chat_model(...)
model.profile = {"max_input_tokens": <窗口大小>}
```

窗口大小从哪来？三个来源，按优先级：

| 来源 | 说明 |
| --- | --- |
| 供应商配置的 `context_window` | **新增字段**，用户在界面上填 |
| 预设表里的默认值 | 按厂商预设（DeepSeek 1M） |
| 兜底 | 不挂 profile（退回框架默认） |

建议给 `LLMProvider` 加一列 `context_window`（整数，0 = 未知）。**迁移很轻**，
加一列 + 回填现有记录。

> 为什么不直接探测：`/models` 接口不一定返回窗口（OpenAI 兼容端点五花八门），
> 而且模型的窗口会变 —— 探测一次就固化，不如让用户填 + 预设兜底。

### 3.7 与记忆模块的分工

记忆模块文档 §11 已经把边界划了：

> 「每轮自动注入目录」这个决策归上下文组装模块。

具体到代码：

| 谁 | 管什么 |
| --- | --- |
| 记忆模块 | **召回什么**（`recall_catalog` 返回什么、排序、预算装箱） |
| 上下文模块 | **要不要注入、注入哪一块、放在提示词的哪个位置** |

所以记忆模块**不改** —— `recall_catalog` 的签名、预算机制都保留。上下文模块
调用它，把返回的目录当成一个 `ContextBlock` 登记。

**边界不变**：记忆模块管"记忆这件事"，上下文模块管"提示词这件事"。

### 3.8 与技能的关系

技能已经是渐进披露（name + description 在提示词里，正文按需 `read_file`）——
**这部分不动**。

但技能**列表**每轮都进提示词，涨起来没上限。上下文模块给它一个配额：技能多
的时候只列前 N 个（`CONTEXT_MAX_SKILLS_IN_PROMPT`，默认 20），后面的省略并
注明"还有 N 个技能未列出"。

实现方式是**同名替换 `SkillsMiddleware`**，只覆盖它的清单渲染 —— 技能加载、
渐进披露、动态工具挂载那些机制都是框架的，动了容易坏。

---

## 4. agent 层的接入点

### 4.1 改哪里

```text
app/agents/runner.py
  _build() 里现在这样拼：
      assemble_prompt(CODE_AGENT_PROMPT, runtime_notes=[...], extra_blocks=[memory_block])
  改成：
      blocks = await context_service.collect(...)     # 收集各来源的块
      system_prompt = context_service.assemble(blocks)  # 按配额装配
```

`runner._memory_block()` 里那段"造 MemoryService → recall_catalog → render"
的逻辑**留在 runner**（它需要数据库会话，构造 MemoryService 是记忆模块的事），
但**产出的目录作为一个块**登记进上下文。记忆召回的判据仍在记忆模块
（`MemoryService.remember` 的硬校验不动）。

> 实施时的一个调整：策略说明的生成逻辑从 `CapabilityMiddleware._policy_note`
> 抽成了模块级函数 `build_policy_note()` —— 上下文模块要把它当一块装进去，
> 就不能只依赖中间件往提示词上追加。

### 4.2 压缩中间件挂在哪

`app/agents/builder.py` 的 `build_agent()` 里，`middleware=` 参数加上：

```python
middleware: list[Any] = [
    governance,
    TodoListMiddleware(),
    build_compaction_middleware(model=resolved_model, backend=backend),
]
```

注意 `CapabilityMiddleware` 与压缩中间件的**相对顺序**：我们在里层，压缩在外层
—— 所以治理中间件看到的是压缩后的消息。这是对的：治理判定的对象应该是"模型
这一轮实际看到的东西"。

### 4.3 审批恢复路径

`runner.resume()` 现在要造两次图（一次读最后一条用户消息，一次带记忆目录）。
**这条路径的实现没变** —— 记忆目录照样召回、照样作为一块装进上下文，
只是装配的动作从 runner 内部挪到了上下文模块。

---

## 5. 实施情况

| 阶段 | 内容 | 前置 | 状态 |
| --- | --- | --- | --- |
| **X0** | **修阈值**：给模型挂 profile + `LLMProvider` 加 `context_window` 列 + 迁移 `0010` | — | ✅ 已完成 |
| **X1** | 上下文块抽象 + 装配器 + 配额（纯函数，不接 agent） | — | ✅ 已完成 |
| **X2** | 压缩中间件接管（同名替换）+ 窗口未知时的兜底 | X0 | ✅ 已完成 |
| **X3** | agent 接入：`runner._build()` 改成走上下文模块；记忆目录作为一块登记 | X1 | ✅ 已完成 |
| **X4** | 技能列表配额 + 策略说明被丢时的警告 + 预览接口 | X1 | ✅ 已完成 |

实施过程中相对本方案的四处调整：

1. **窗口未知时不能用 fraction**。langchain 的摘要中间件在用比例而模型没有
   profile 时会**直接抛 ValueError**（"Model profile information is
   required..."）。方案里没考虑到这点 —— 实现时退回绝对值
   （100000 token 触发 / 保留 20 条），保证 agent 永远起得来。
2. **摘要模型暂时用主模型**。方案 §3.5 提了"可配的独立便宜模型"，但那需要
   再加一个供应商配置项；当前先不接 —— 压缩频率不高，主模型够用。
   真要省钱时再补一个 `SUMMARY_PROVIDER_ID` 配置。
3. **`keep` 的下限没能落到框架参数上**。langchain 的 `keep` 只吃单个子句
   （"fraction 0.1" 或 "messages 20"），不支持"至少 20 条"这种组合语义。
   实现上分两条路：窗口已知用比例（1M 窗口下保留 10 万 token ≈ 几百条，
   本来就够多），窗口未知才用"保留 20 条"这个绝对值。
4. **产物目录用 `CompositeBackend.artifacts_root` 实现**，而不是单独加路由
   前缀。框架按 `{artifacts_root}/conversation_history/` 写历史，把
   `artifacts_root` 指到工作区外面就够了。

### 5.1 X0 单独做的理由

X0 是**纯 bug 修复**，不是新功能：实施前的压缩阈值对 1M 窗口的模型是错的，
而且错得很离谱（170k 就压缩、只留 6 条）。它不依赖上下文模块的任何东西。

先做它，等于先止血。

---

## 6. 已知风险

1. **被压缩的历史落在工作区里** —— ✅ **已解决**。
   deepAgents 把摘要掉的消息写到 `{artifacts_root}/conversation_history/`，
   默认的 `/` 会落进工作区。现在把 `CompositeBackend.artifacts_root` 指到
   `/.artifacts/`（工作区外面的 `<workspace父目录>/.artifacts/<会话名>/`），
   并给这条前缀挂一条 `FilesystemBackend` 路由。agent 的文件工具看不到它。
2. **接管摘要中间件有版本耦合**。`name = "SummarizationMiddleware"` 是靠
   deepAgents 的**同名替换**约定生效的。升级 deepAgents 时这条会静默失效
   —— 变成两份摘要中间件同时跑（或者我们那份被忽略）。
   - 对策：✅ 已加测试断言"栈里只有一份压缩中间件，且 trigger 是我们配的"
     （`tests/test_context.py::test_compaction_replaces_default_not_duplicates`）。
3. **压缩用便宜模型会有质量损失**。摘要模型弱了，压缩后的历史会丢细节。
   - 现状：**摘要暂时用主模型**（`summarizer` 参数留了口子，没接独立配置）。
     压缩频率不高，先不优化。真要省钱时补一个 `SUMMARY_PROVIDER_ID`。
4. **配额把工作区说明截断了**，agent 可能违反项目规矩。
   - 对策：✅ 截断时保留**头部**（规矩通常在开头），并留"已截断"的标记 ——
     静默截断会让模型以为看到的是全部。
5. **策略说明被丢掉** → 模型不知道哪些操作要人批 → 反复触发审批。
   - 对策：✅ 丢掉时记警告（`assembler._note_drop`）。策略说明本身很短，
     实际很难被挤掉。
6. **窗口未知时用 fraction 会直接崩**（langchain 抛 ValueError）。
   - 对策：✅ 压缩中间件在窗口未知时退回绝对值
     （100000 token 触发 / 保留 20 条）。

---

## 7. 待定项

| # | 问题 | 结论 |
| --- | --- | --- |
| 1 | `context_window` 字段加在 `LLMProvider` 表，还是放 `providers.py` 预设表？ | **表 + 预设兜底** —— 已实现：表里可改，预设给默认值；换供应商时自动跟到新预设 |
| 2 | 摘要用哪个模型？ | **暂用主模型** —— `summarizer` 口子留着了，独立配置以后补 |
| 3 | 上下文预算占窗口多少？ | **50%**（`CONTEXT_BUDGET_RATIO`）—— 另一半留给输出与工具 schema |
| 4 | 被压缩的历史放工作区里还是外面？ | **外面** —— 已实现：`artifacts_root` 指到工作区外 |
| 5 | 上下文模块要不要提供"预览"接口？ | **要** —— 已实现：`GET /api/v1/context/preview` |
| 6 | 压缩是自动触发还是也给模型一个手动工具？ | **先只自动** —— deepAgents 有 `compact_conversation` 工具，等自动的跑顺了再说 |

---

## 8. 文件清单

```
app/context/__init__.py
app/context/blocks.py         ContextBlock 抽象
app/context/assembler.py      装配器：按配额拼成最终 prompt
app/context/budget.py         预算计算与配额分配
app/context/compaction.py     接管 deepAgents 的摘要中间件
app/context/service.py        对外入口：collect + assemble
app/context/skills_quota.py   技能清单配额（同名替换 SkillsMiddleware）
app/schemas/context.py        预览接口的响应模型
app/api/v1/context.py         GET /context/preview
```

改动的既有文件：

| 文件 | 改什么 |
| --- | --- |
| `app/agents/llm.py` | 给模型挂 `profile`（含 `max_input_tokens`） |
| `app/agents/builder.py` | 挂压缩中间件 + 带配额的技能中间件 |
| `app/agents/runner.py` | `_build()` 改走上下文模块；策略说明改从能力层取 |
| `app/agents/providers.py` | 预设表加 `context_window` 默认值 |
| `app/models/llm_provider.py` | 加 `context_window` 列 |
| `app/schemas/llm_provider.py` | 增删改查都带上 `context_window` |
| `app/services/llm_provider.py` | 创建时取预设默认；换供应商时窗口跟着走 |
| `app/capabilities/intent/middleware.py` | 策略说明抽成 `build_policy_note()`（上下文模块要用） |
| `app/capabilities/backends/assembly.py` | `artifacts_root` 指到工作区外 |
| `app/core/config.py` | 上下文相关配置项 |
| `app/api/router.py` | 上下文路由挂载 |

新增迁移：`0010_llm_provider_context_window`。

**不动的**：记忆模块（召回逻辑、写入校验全保留）、RAG 层、能力层的执行流水线、
`app/capabilities/prompt.py`（能力层直接调用那条路径还在用它）。

---

## 9. 已知会踩的坑

1. **别重写提示词**。现在的提示词是几段拼起来的，看着像可以重构成一个模板 ——
   不要。deepAgents 会往 system prompt 上追加它自己的内容（文件工具说明、
   虚拟路径映射），我们拼的只是其中一段。重写会跟框架打架。

2. **别把压缩写在自己模块里**。deepAgents 已经有一套（还带历史外置），
   自己再写一套摘要，等于有两个地方在改历史，谁先谁后说不清。用同名替换接管。

3. **`keep` 别太小**。6 条对代码 agent 太少 —— 一轮就有好几次工具调用。

4. **中间件栈序别搞反**。我们在里层，拿到的 `request.messages` 已经压缩过。
   想看全量历史就得接管摘要中间件，别指望在用户中间件里能看到。

5. **`assemble_prompt` 还有别的调用方**。`app/services/capability.py` 的直接
   调用路径（不走 agent）也在用它 —— 那条路径不需要上下文模块，别把它一起改坏。

---

## 10. 怎么用

### 10.1 配置

| 配置 | 默认 | 说明 |
| --- | --- | --- |
| `LLM_CONTEXT_WINDOW` | 0 | .env 兜底路径的上下文窗口；0 = 未知 |
| `CONTEXT_BUDGET_RATIO` | 0.5 | 上下文占窗口的比例 |
| `CONTEXT_CHARS_PER_TOKEN` | 4.0 | token 折算字符 |
| `CONTEXT_MAX_SKILLS_IN_PROMPT` | 20 | 技能清单最多列几个 |

**正式路径**（走「模型接入配置」）的窗口来自供应商记录的 `context_window`
字段 —— 创建时默认取厂商预设的值（DeepSeek 1048576、OpenAI 128000 等），
用户可以改。换供应商时窗口自动跟到新预设。

### 10.2 排查：预览这次会给模型看什么

```bash
curl 'http://127.0.0.1:8000/api/v1/context/preview?query=pnpm&workspace=/tmp/ws'
```

返回拼好的提示词正文、预算用量，以及**哪些块被丢了、哪些被截断了**。
「为什么模型没记住这条记忆」这类问题只能看着实际拼出来的东西才答得上来。

### 10.3 供应商记录的窗口

```bash
curl -X PUT http://127.0.0.1:8000/api/v1/llm-providers/1 \
  -H 'Content-Type: application/json' \
  -d '{"context_window": 1048576}'
```

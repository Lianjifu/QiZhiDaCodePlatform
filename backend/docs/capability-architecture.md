# 能力层 v2 架构方案（注册 / 意图 / 执行）

> 状态：**已实施（P0–P4、P5a/P5b、P6）**。
> P5c（agent 挂起等待审批，用 LangGraph interrupt）**未做** —— 审批改为由审批接口
> 直接执行挂起的调用，不依赖 agent 还活着，够用了。
> 本文是设计文档，实现与文中描述的差异见 §14「实施情况」。

---

## 1. 目标与边界

能力层现在能跑通，但它是"能跑"的形态，不是"可治理"的形态：能力靠描述文字注册，执行靠一个 `try/except` 包住，谁调了、调了什么、为什么失败，只能查到一半。

v2 要解决的是一件事：**让每一次能力调用都经过同一条可声明、可校验、可审计、可拦截的流水线。**

三条硬目标：

1. **声明式注册** —— 一个能力的输入输出契约、风险、权限、超时、重试、幂等，全部是声明字段，不是散落在实现里的隐含行为。
2. **单一执行路径** —— agent 内部调用、直接调 API、MCP 工具，走同一条流水线，留同一份痕。当前 agent 内部调用**完全绕过**执行层（见 §2），这是最大的缺口。
3. **副作用可控** —— 高风险动作能被拦住、能被等一下、能被撤销。

### 不在本方案范围内

- 多 agent 协作、agent 之间的权限传递
- 细粒度 RBAC（角色体系留到 §6.4 之后再谈）
- 能力市场 / 第三方能力签名校验
- MCP server 的自动发现与热更新
- 语义检索（当前是关键词打分，换向量检索是独立议题）

---

## 2. 现状与差距

### 2.1 现状盘点

```text
注册层  app/capabilities/registry.py    三张表聚合成统一清单；sync_builtins 装出厂能力
        app/capabilities/entry.py       CapabilityEntry —— 清单条目视图
编排层  app/capabilities/orchestration.py   ← 现状叫「编排」，v2 更名为「意图层」
          ├── retrieve_skills / select_skills   按用户输入检索技能
          ├── validate_arguments               参数校验（手写，只查必填+基础类型）
          └── CapabilityMiddleware              收窄工具集 / 调用前校验参数
执行层  app/capabilities/adapter.py      builtin / http / mcp 三种适配器
        app/capabilities/result.py       ExecutionResult + 错误码分级
        app/services/capability.py       直接调用的入口，写 ExecutionLog
```

### 2.2 七个差距

| # | 差距 | 证据 | 后果 |
| --- | --- | --- | --- |
| 1 | **agent 内部调用绕过执行层** | `adapter.py:52` 把工厂函数直接包成 `StructuredTool`，LangChain 调它时**不经过** `adapter.invoke` | 没有日志、没有超时、没有重试、没有策略检查；`services/capability.py:125` 的 `_record` 只覆盖直接调用那条路 |
| 2 | 没有输出契约 | 所有内置能力返回自由文本 | 无法验证结果，无法自动化消费 |
| 3 | 风险与权限合成一个字段 | `models/tool.py:58` / `entry.py:46` 都是 `permission: low\|high` | 无法表达"要人批但风险可控"和"高风险但自动放行" |
| 4 | 参数校验是玩具 | `orchestration.py:116` 的 `_type_matches` | `enum` / `minimum` / `pattern` / `additionalProperties` 全不生效 |
| 5 | 超时是全局的 | `context.sandbox_timeout`；http 工具硬编码 30s | 一个慢能力拖垮整轮；http 能力无法单独调优 |
| 6 | 重试是全局静态表 | `result.py:33` 的 `_RETRYABLE` 按错误码映射，与能力无关 | 不知道重试几次、退避多久、哪些错误可重试；写操作盲目重试会重复副作用 |
| 7 | 无审批、无幂等、无脱敏、无配额 | —— | 高风险动作无法拦截；重试可能重复发请求；结果里可能带密钥 |

> 差距 1 是根因性的：**执行层现在是"可选路径"而不是"必经之路"**。不先把它变成必经之路，后面加的所有治理字段都只是装饰。

---

## 3. 三层职责

三层分别是 **注册层 / 意图层 / 执行层**——三个动词，正好是同一条流水线上的三步：
**注册**声明能力，**意图**形成这次调用意图，**执行**真正动手。

> 现状里的「编排层」在 v2 更名为 **意图层**（`intent/`）——它产出的不是"编排好的
> 执行"，而是**一次调用的意图**：把模型的提议翻译成结构化意图，**不产生任何副作用**。
> 执行层才真正动手。

```text
┌─────────────────────────────────────────────────────────────┐
│ 注册层 registration                                         │
│   CapabilitySpec：声明式契约（输入/输出 schema、风险、权限、│
│   超时、重试、幂等、注解）                                  │
│   产物：统一能力清单（数据库是唯一事实来源，代码是出厂源）  │
└───────────────┬─────────────────────────────────────────────┘
                │ CapabilitySpec
                ▼
┌─────────────────────────────────────────────────────────────┐
│ 意图层 intent                                               │
│   任务上下文 → 候选能力 → CapabilityIntent（一次调用）      │
│   产物：结构化意图（可审计、可拦截、可审批）                │
└───────────────┬─────────────────────────────────────────────┘
                │ CapabilityIntent
                ▼
┌─────────────────────────────────────────────────────────────┐
│ 执行层 execution                                            │
│   ① schema 校验 → ② 权限校验 → ③ 风险评估 → ④ 审批等待      │
│   → ⑤ 实际调用 → ⑥ 结果验证 → ⑦ 结果回写                    │
│   产物：ExecutionResult + Invocation（生命周期记录）        │
└─────────────────────────────────────────────────────────────┘
```

### 3.1 边界约定

| 层 | 输入 | 输出 | 禁止 |
| --- | --- | --- | --- |
| 注册 | 数据库 + 出厂源 | `CapabilitySpec` 列表 | 不知道"这次任务要干什么" |
| 意图 | 任务上下文 + 清单 | `CapabilityIntent` | **不执行任何副作用**；不替模型做"调哪个能力"的最终决策 |
| 执行 | `CapabilityIntent` | `ExecutionResult` | 不做规划、不检索技能 |

**最关键的一条边界：模型提议，意图层收敛。**

模型（LangGraph）仍然决定"下一步调哪个能力"——它看得见对话历史，这是它不可替代的优势。意图层做的是把模型这次 tool call **翻译并约束**成一个结构化意图：绑定 spec、解析参数、附上本次会话的策略上下文。

意图层的产出是**一次调用**，不是多步计划。多步计划由模型的 ReAct 循环产生；如果意图层要产出计划，等于把 ReAct 重写一遍，LangGraph 的价值也就没了。

---

## 4. 注册层：声明式

### 4.1 数据结构

```python
# app/capabilities/registration/spec.py
from dataclasses import dataclass, field


@dataclass(frozen=True)
class CapabilitySpec:
    """一个能力的完整声明。"""

    key: str
    kind: str  # tool | mcp | skill
    display_name: str = ""
    description: str = ""

    # ---- 契约 ----
    input_schema: dict = field(default_factory=dict)  # JSON Schema
    output_schema: dict | None = None  # None = 自由文本

    # ---- 治理 ----
    risk: str = RiskLevel.READ  # read|write|execute|network|destructive
    permission: str = PermissionLevel.NONE  # none|confirm|admin
    timeout_s: int = 30
    retry: RetryPolicy = field(default_factory=RetryPolicy)
    annotations: Annotations = field(default_factory=Annotations)

    # ---- 身份与来源 ----
    enabled: bool = True
    origin: str = ORIGIN_USER
    version: int = 1
    source_type: str = SOURCE_BUILTIN
    locator: dict = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)
```

配套的三个小类型：

```python
# app/capabilities/registration/policy.py


@dataclass(frozen=True)
class RetryPolicy:
    """重试策略。与运行时错误的 retryable 取交集后才生效。"""

    max_attempts: int = 1  # 含首次调用
    backoff_s: float = 0.0
    retry_on: tuple[str, ...] = ()  # ErrorCode 白名单


@dataclass(frozen=True)
class Annotations:
    """工具自述的性质，词汇对齐 MCP tool annotations。

    与 risk 的关系：annotations 是"工具本来是什么样"，
    risk 是"平台判定这次调用有多危险"。注册时按 annotations 推导 risk 下界，
    平台可以上调，不允许下调。
    """

    read_only: bool = False
    destructive: bool = False
    idempotent: bool = False
    open_world: bool = False

    @classmethod
    def from_mcp(cls, annotations: Any) -> "Annotations":
        """从 MCP server 侧的 tool annotations 继承。"""
        if annotations is None:
            return cls()
        return cls(
            read_only=bool(getattr(annotations, "readOnlyHint", False)),
            destructive=bool(getattr(annotations, "destructiveHint", False)),
            idempotent=bool(getattr(annotations, "idempotentHint", False)),
            open_world=bool(getattr(annotations, "openWorldHint", False)),
        )
```

### 4.2 风险与权限是两个维度

现在 `permission: low|high` 一个字段兼了"多危险"和"要不要人批"，两者正交，必须拆开：

| risk | 含义 | 典型 |
| --- | --- | --- |
| `read` | 只读，不改变任何状态 | `read_file` / `list_files` / `glob` / `grep` |
| `write` | 改变工作区内状态 | `write_file` / `edit_file` / `move_file` / `copy_file` / `make_dir` |
| `execute` | 执行代码或命令 | `run_python` / `run_bash` |
| `network` | 与外部网络交互 | `web_fetch` / `web_search` |
| `destructive` | 不可逆，或影响工作区外的系统 | 删库、部署、发消息；目前内置能力没有，http/mcp 来源可能出现 |

| permission | 含义 |
| --- | --- |
| `none` | 自动执行 |
| `confirm` | 需要人确认（审批） |
| `admin` | 需要管理员（角色体系就绪前等价于 confirm + 更严的记录） |

**风险等级的推导规则**（注册时执行一次，平台可上调不可下调）：

```text
annotations.read_only          → risk 上限 read
annotations.destructive         → risk ≥ destructive
annotations.open_world         → risk ≥ network
内置能力的出厂声明              → 作为基线
```

### 4.3 信任模式：不让"每次写文件都要人批"拖死开发

按 risk 直接映射 permission 会让 agent 基本不可用（每写一个文件都等人批）。引入**会话级信任策略**：

| 模式 | 放行范围 | 用途 |
| --- | --- | --- |
| `strict` | 完全按 `permission` 声明 | 生产 |
| `balanced`（默认） | `risk=read` 自动放行；`write` 自动放行并记录；`execute`/`network` 需确认 | 默认 |
| `permissive` | 仅 `risk=destructive` 需确认 | 本地开发 |

信任模式**只能放宽 `confirm`，不能放宽 `admin`**。它是运行时策略，不是声明的一部分。

### 4.2 个内置能力的规格

| key | risk | permission | timeout_s | retry（max_attempts / retry_on） | annotations | output_schema |
| --- | --- | --- | --- | --- | --- | --- |
| `read_file` | read | none | 10 | 1 / — | read_only, idempotent | `{content, line_count, truncated}` |
| `write_file` | write | none | 10 | 1 / — | idempotent | `{path, bytes, created}` |
| `edit_file` | write | none | 10 | 1 / — | idempotent | `{path, replaced_count}` |
| `move_file` | write | none | 10 | 1 / — | — | `{source, destination}` |
| `copy_file` | write | none | 10 | 1 / — | idempotent | `{source, destination}` |
| `make_dir` | write | none | 10 | 1 / — | idempotent | `{path, created}` |
| `change_directory` | read | none | 5 | 1 / — | read_only, idempotent | `{cwd}` |
| `list_files` | read | none | 10 | 1 / — | read_only, idempotent | `{entries: [{name, is_dir, size}]}` |
| `glob` | read | none | 15 | 1 / — | read_only, idempotent | `{matches: [str]}` |
| `grep` | read | none | 20 | 1 / — | read_only, idempotent | `{matches: [{file, line, text}]}` |
| `run_python` | execute | confirm | 60 | 2 / `TIMEOUT` | destructive, open_world | `{stdout, stderr, exit_code, duration_ms}` |
| `run_bash` | execute | confirm | 60 | 2 / `TIMEOUT` | destructive, open_world | `{stdout, stderr, exit_code, duration_ms}` |
| `web_fetch` | network | confirm | 20 | 3 / `NETWORK_ERROR` | open_world | `{url, status, text, bytes}` |
| `web_search` | network | confirm | 20 | 3 / `NETWORK_ERROR` | open_world | `{query, results: [{title, url, snippet}]}` |

说明：

- `move_file` **不幂等**：第一次成功移动后源已不存在，重试会失败。幂等标记必须如实，否则重试策略会做出错误判断。
- `write_file` / `edit_file` / `copy_file` / `make_dir` 幂等：同样的参数重放得到同样的结果。
- `run_python` / `run_bash` 只在**超时**时重试——超时意味着"不知道有没有执行完"，重试可能重复副作用；所以这两者的重试必须配合幂等键（§6.5）。
- 上表的 `permission` 是**声明值**；实际是否需要审批由 §4.3 的信任模式在运行时决定。

---

## 5. 意图层

这一层把"模型的一次 tool call"变成"一条可执行、可审计、可拦截的意图"。

```text
app/capabilities/intent/
  intent.py       CapabilityIntent —— 与执行层之间的唯一契约
  planner.py      任务上下文 → 候选技能/能力（现状：retrieve_skills / select_skills）
  resolver.py     模型 tool call → CapabilityIntent
  middleware.py   LangChain middleware 挂钩（现状：CapabilityMiddleware）
```

### 5.1 意图结构

```python
# app/capabilities/intent/intent.py
@dataclass
class CapabilityIntent:
    """一次能力调用的意图。意图层与执行层之间的唯一契约。"""

    capability_key: str
    arguments: dict[str, Any]
    spec: CapabilitySpec

    # 上下文
    thread_id: str = ""
    agent_id: str = ""
    workspace: Path | None = None

    # 策略快照（生成意图那一刻的判定，执行层不再重新推导）
    trust_mode: str = "balanced"
    idempotency_key: str | None = None
    requested_at: datetime = field(default_factory=utcnow)

    def fingerprint(self) -> str:
        """意图指纹，用于去重与审计。"""
        payload = json.dumps(
            {"key": self.capability_key, "args": self.arguments},
            sort_keys=True,
            ensure_ascii=False,
            default=str,
        )
        return hashlib.sha256(payload.encode()).hexdigest()[:32]
```

### 5.2 生成流程

```text
用户输入
  │
  ├─ planner.retrieve_skills(清单, 输入)      现有 retrieve_skills
  │    → 命中的技能
  │
  ├─ planner.collect_tool_keys(技能, 清单)     现有 collect_tool_keys
  │    → 本次允许暴露给模型的能力集
  │
  ├─ middleware.awrap_model_call              现有能力：收窄工具集
  │    → 模型看到的是一个收敛后的工具集
  │
  ├─ 模型产出 tool call                       {name, args, id}
  │
  └─ resolver.resolve(tool_call, 清单, 上下文)
       → CapabilityIntent
         ├── 绑定 spec（找不到 → CAPABILITY_NOT_FOUND）
         ├── 校验参数（前置快速校验，失败给模型可重试提示）
         ├── 计算幂等键
         └── 快照当前信任模式
```

### 5.3 与 LangChain middleware 的接入

`CapabilityMiddleware` 的两个挂钩职责升级：

| 挂钩 | 现在 | v2 |
| --- | --- | --- |
| `awrap_model_call` | 收窄工具集 | 收窄工具集 + 注入策略上下文（本次信任模式、可用能力摘要） |
| `awrap_tool_call` | 校验参数后 `handler(request)` | **构造意图 → 调执行层 → 返回结果**；不再直接 `handler` |

`awrap_tool_call` 不再直通，是消除 §2.2 差距 1 的关键改动：所有经过模型的调用，从此都在执行层的流水线里。

### 5.4 意图层的两个"不做"

- **不做多步计划**。计划由模型的 ReAct 循环产生。
- **不做能力选择**。候选集已经收敛给模型，选哪个是模型的事；意图层只翻译和约束。

---

## 6. 执行层

### 6.1 流水线

```python
# app/capabilities/execution/pipeline.py
async def execute(intent: CapabilityIntent, ctx: ExecutionContext) -> ExecutionResult:
    """唯一执行路径。agent 内部调用、直接调 API、MCP 工具都走这里。"""

    # ① schema 校验（权威）
    # ② 权限校验（能力是否被授权给本次会话）
    # ③ 风险评估（声明风险 + 参数级风险）
    # ④ 审批等待（none 自动放行 / admin 拒绝 / confirm 按信任模式决定）
    # ⑤ 实际调用（适配器 + 超时 + 重试 + 幂等去重）
    # ⑥ 结果验证（对 output_schema 校验 + 副作用效果确认）
    # ⑦ 结果回写（Invocation 记录 + 脱敏）
```

每个阶段产出一条阶段记录，失败时带上"已走到第几步"，便于定位是哪一关拦下的。

### 6.2 ① schema 校验

- **两处校验、一个实现**：意图层做前置快速校验（给模型一句"缺少必填参数 path，可以修正后重试"，省一轮往返）；执行层做权威校验（不通过就拒绝执行）。共用同一份 `input_schema` 和同一个校验器。
- **换掉手写校验**：`orchestration.py:116` 的 `_type_matches` 只查必填和基础类型。改用 `jsonschema` 库（需在 `pyproject.toml` 显式加依赖），支持 `enum` / `minimum` / `pattern` / `additionalProperties`。
- 校验失败的错误码 `SCHEMA_INVALID`，`retryable=true`（模型改参数就能重试）。

### 6.3 ② 权限校验 / ③ 风险评估

**② 权限校验**回答的是"这个会话有没有资格用这个能力"（能力被禁用 / 不在本会话允许集内 / 来源不可达）。

**③ 风险评估**回答的是"这次调用具体有多危险"。声明的 risk 是基线，**参数级规则**可以上调：

| 能力 | 参数特征 | 判定 | 升级到 |
| --- | --- | --- | --- |
| `run_bash` | 命令匹配 `rm -rf` / `dd ` / `mkfs` / `curl … \| sh` | 破坏性 | `destructive` + `admin` |
| `web_fetch` | URL 主机是 `127.0.0.1` / `10.` / `172.16-31.` / `192.168.` / `169.254.` / `::1` | SSRF | `destructive` + `admin` |
| `web_fetch` | URL 主机是内网域名（按配置的内部域名后缀） | SSRF | `destructive` + `admin` |
| 任意写类能力 | 路径解析后越出工作区 | 越界 | 直接拒绝（路径层已拦，双保险） |
| http 来源能力 | URL 指向内网 | SSRF | `destructive` + `admin` |

参数级规则的实现是**纯函数**：`(spec, arguments) -> RiskAssessment(risk, reasons[])`，便于单测，也便于将来让模型解释"为什么这次要审批"。

### 6.4 ④ 审批等待

审批把同步调用变成两阶段：

```text
调用 → 生成 Invocation(status=pending) → 返回"等待审批"
                                              ↓
                              人工/规则批准 ─┬─ approved → 继续执行
                                           └─ denied   → 返回 APPROVAL_DENIED
```

**决策规则**（按顺序）：

```text
permission == admin            → 必须人工批准
permission == confirm
  且 trust_mode == strict      → 必须人工批准
  且 risk 评估后被上调到 destructive → 必须人工批准（且要 admin）
  否则                          → 自动放行
permission == none             → 自动放行
```

**三种落地形态，建议按期推进**：

| 期 | 形态 | 依赖 |
| --- | --- | --- |
| P4a | 规则自动审批（无人工介入），先把 `invocation` 表和状态机建起来 | 无 |
| P4b | 人工审批 API（`approve` / `deny` / `cancel`），agent 这轮返回 pending 引用后结束 | 需要审批界面 |
| P4c | agent 挂起等待（LangGraph `interrupt`），批完自动续跑 | 需要 checkpointer 恢复，复杂度最高 |

P4c 的一个必须注意的点：**LangGraph 的 `interrupt` 恢复会重放节点**，也就是同一次调用可能被执行两遍。因此**幂等键不是可选优化，是 P4c 的前提**（§6.5）。

审批请求默认超时自动拒绝（`CAPABILITY_APPROVAL_TIMEOUT`，默认 300 秒），不留悬挂状态。

### 6.5 ⑤ 实际调用：超时、重试、幂等

```text
for attempt in 1..retry.max_attempts:
    if idempotency_key 已存在且状态为 succeeded → 直接返回该结果（不再执行）
    try:
        result = await adapter.invoke(entry, arguments, ctx, timeout=spec.timeout_s)
        if result.ok and error_code 不在 retry.retry_on → 成功返回
        if error.retryable is False → 失败返回（声明可重试 ≠ 运行时可重试，取交集）
        退避 backoff_s 后重试
    except TimeoutError:
        同上，按 retry_on 判断是否重试
```

**幂等键的派生**：`sha256(capability_key + 参数中参与副作用的字段 + thread_id)`。参与哪些字段由 spec 声明（没有幂等性的能力不生成键，也不重试）。

**幂等记录存在 `invocation` 表**：同一个 `idempotency_key` 若已有 `succeeded` 记录，直接返回它的结果。这让 P4c 的节点重放安全。

### 6.6 ⑥ 结果验证

两个层面：

- **契约验证**：结果数据对照 `output_schema` 校验。不符合 → `OUTPUT_INVALID`（`retryable=false`，是能力实现的问题，不是模型的问题）。
- **效果验证**：确认副作用真的发生了。例：`write_file` 返回后检查目标文件存在且大小匹配；`move_file` 检查源已消失、目标已存在。这一层能抓住"适配器说成功了但实际没写成"的情况。

效果验证规则同样声明在 spec 里（`verify: str | None`，指向一个校验函数名），内置能力出厂时带上。

### 6.7 ⑦ 结果回写

- 所有执行路径统一写 `invocation` 表（§7.1），不再由服务层单独写 `ExecutionLog`。
- **结果脱敏**：回写前对 `arguments` 和 `result` 过一遍脱敏规则。默认识别 `sk-`、`ghp_`、`AKIA`、`.env` 内容、`password=` 等模式，命中替换为 `***`。执行给模型看的文本**不脱敏**（模型需要看到真实内容才能干活），脱敏只作用于落库的部分。
- 记录维度：调用方、耗时、每个阶段的结论、最终结果/错误码。

### 6.8 错误码

现有 7 个保留，新增：

| 错误码 | retryable | 含义 |
| --- | --- | --- |
| `SCHEMA_INVALID` | true | 输入不符合声明的 schema |
| `RISK_BLOCKED` | false | 风险评估判定为不可执行 |
| `APPROVAL_REQUIRED` | false | 需要人工审批，调用已挂起 |
| `APPROVAL_DENIED` | false | 审批被拒 |
| `APPROVAL_EXPIRED` | false | 审批超时 |
| `OUTPUT_INVALID` | false | 结果不符合声明的输出契约 |
| `TIMEOUT` | true | 超时（`SANDBOX_TIMEOUT` 作为别名保留） |
| `CANCELLED` | false | 被取消 |
| `QUOTA_EXCEEDED` | true | 超出并发或配额 |
| `IDEMPOTENCY_CONFLICT` | false | 同一幂等键上有冲突的进行中调用 |

---

## 7. 数据模型与迁移

### 7.1 `invocation` 表（取代 `execution_log`）

```text
invocation
  id                 PK
  thread_id          str        会话
  capability_key     str        调了哪个能力
  arguments          json       已脱敏
  intent_fingerprint  str       意图指纹
  idempotency_key    str        可空，索引
  status             str        pending|approved|denied|running|succeeded|failed|cancelled|expired
  risk               str        评估后的风险等级
  permission         str        评估后的权限要求
  decision_reason    str        为什么放行/拦截/挂起
  stages             json       各阶段结论
  result             json       已脱敏
  error_code         str        可空
  error_message      str        可空
  created_at / decided_at / started_at / finished_at
  duration_ms        int
```

`execution_log` 现有数据迁移过来（`ok=true` → `succeeded`，`ok=false` → `failed`），表改名 `op.rename_table`。

### 7.2 `tool` 表新增列

| 列 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `risk` | String(16) | `read` | 风险等级 |
| `output_schema` | JSON | `null` | 输出 JSON Schema |
| `timeout_s` | Integer | `30` | 单次调用超时 |
| `retry_policy` | JSON | `{}` | `{max_attempts, backoff_s, retry_on}` |
| `annotations` | JSON | `{}` | `{read_only, destructive, idempotent, open_world}` |

`permission` 取值域从 `low|high` 扩到 `none|confirm|admin`，需要数据迁移：

```text
low  → none
high → confirm
```

（内置能力的声明值以出厂源为准，`sync_builtins` 只补空字段，不覆盖运营改过的值——这条规则继续有效。）

### 7.3 迁移脚本

```text
0003_capability_v2        新增列 + permission 取值域迁移 + execution_log → invocation
0004_builtin_specs        14 个内置能力的出厂规格升级（risk/permission/timeout/retry/annotations/output_schema）
0005_structured_output    内置能力改返回结构化结果
```

按 Alembic 现有约定，`alembic revision --autogenerate` 生成骨架后手工补数据迁移段。

---

## 8. API 变更

```text
GET    /api/v1/capabilities/{key}/spec     声明详情（schema/风险/权限/策略）—— 新增
POST   /api/v1/capabilities/{key}/invoke   执行；需审批时返回 202 + invocation_id
GET    /api/v1/invocations                 分页查询调用记录 —— 新增
GET    /api/v1/invocations/{id}            详情（含各阶段结论）—— 新增
POST   /api/v1/invocations/{id}/approve   批准 —— 新增
POST   /api/v1/invocations/{id}/deny      拒绝 —— 新增
POST   /api/v1/invocations/{id}/cancel    取消 —— 新增
```

**兼容性**：`POST /capabilities/{key}/invoke` 的成功响应结构不变（`CapabilityInvokeOut`）；只是当需要审批时，改为 `202` + `{invocation_id, status: "pending"}`。现有前端需要适配这一种情况。

---

## 9. 配置项

```bash
# 信任模式：strict | balanced | permissive
CAPABILITY_TRUST_MODE="balanced"
# 默认超时（能力没声明时的兜底）
CAPABILITY_DEFAULT_TIMEOUT=30
# 审批请求超时自动拒绝（秒）
CAPABILITY_APPROVAL_TIMEOUT=300
# 单会话并发执行上限
CAPABILITY_MAX_CONCURRENCY=4
# 重试上限兜底（能力没声明时）
CAPABILITY_RETRY_MAX_ATTEMPTS=1
# 脱敏正则（额外补充默认规则）
CAPABILITY_REDACT_PATTERNS=""
# 内部域名后缀（SSRF 判定用，逗号分隔）
CAPABILITY_INTERNAL_DOMAINS="localhost,.internal,.local"
```

---

## 10. 落地顺序

每一期都可独立发布、独立验收、独立回滚。

### P0 — 注册层字段（低风险）

补齐 `risk` / `output_schema` / `timeout_s` / `retry_policy` / `annotations` 五个字段，拆开 `permission`，加 `0003` 迁移，出厂规格升级到 `0004`。

**不动任何执行路径**，只加声明。验收：14 个内置能力的 spec 齐全；`GET /capabilities/{key}/spec` 能返回完整声明；存量库迁移后数据不丢。

### P1 — 执行日志下沉（低风险）

把 `services/capability.py:125` 的 `_record` 迁到执行层，建立 `invocation` 表，覆盖 agent 内部调用这条路径（当前完全没日志）。

**验收**：agent 内部每次工具调用都在 `invocation` 里有记录，含参数、耗时、结果。

### P2 — 执行流水线显式化（中风险）

把散在 `adapter.py` 里的超时/异常处理收进 `pipeline.py` 的七个阶段；执行层成为唯一路径（§2.2 差距 1）。

**这是整个方案的关键一期**：之后所有治理能力才有落点。

**验收**：agent 内部调用与直接调用产出的 `invocation` 记录字段完全一致；流水线每一阶段可单独单测。

### P3 — 结构化输出（中风险）

14 个内置能力改为返回结构化数据，spec 补 `output_schema`，执行层做契约验证与效果验证（`0005` 迁移）。

**验收**：结果验证能抓出"适配器说成功但文件没写成"；`OUTPUT_INVALID` 能被正确触发。

### P4 — 意图层（中风险）

引入 `CapabilityIntent` 与 resolver；`awrap_tool_call` 改为构造意图后调执行层。

**验收**：意图指纹可用于去重；调用记录里能看到意图快照。

### P5 — 审批（高风险）

P5a 规则自动审批 + `invocation` 状态机 → P5b 人工审批 API → P5c agent 挂起等待（LangGraph interrupt）。

**验收**：写文件类操作在 strict 模式下能挂起并被批准；节点重放不产生重复副作用（幂等键生效）。

### P6 — 治理补齐（低风险）

结果脱敏、并发配额、参数级风险规则、错误码扩展。

---

## 11. 测试策略

| 层 | 测什么 | 形态 |
| --- | --- | --- |
| 注册 | spec 与出厂源一致；sync_builtins 不覆盖运营改过的值 | 单测 |
| 注册 | 迁移后 schema 与模型一致（现有 `test_migrations.py` 已有对照机制） | 集成 |
| 意图 | 检索/收敛/意图构造；意图指纹稳定 | 单测 |
| 意图 | middleware 收窄工具集、构造意图并走执行层 | 集成（假模型） |
| 执行 | 七个阶段各自的判定 | 阶段级单测 |
| 执行 | 幂等：同键重放不重复执行 | 集成 |
| 执行 | 审批：挂起 → 批准 → 执行；拒绝 → 不执行 | 集成 |
| 执行 | 风险升级：`rm -rf` / 内网 URL 被拦 | 单测 |
| 端到端 | agent 内部调用产出完整 invocation | 集成 |

**回归红线**：P2 之后，每次改动都必须跑通"agent 内部调用"这条路径的端到端测试——那是当前完全没有覆盖、也最容易悄悄坏掉的路径。

---

## 12. 风险登记

| 风险 | 影响 | 缓解 |
| --- | --- | --- |
| 执行层成为唯一路径，agent 每步多走一遍流水线 | 延迟上升 | 阶段可配置跳过；先量测再优化 |
| LangGraph interrupt 恢复会重放节点 | 重复执行副作用 | 幂等键（P5c 的前提，不是可选项） |
| 14 个内置能力改结构化输出 | 改动面大，模型看到的返回文本会变 | 分两批：execute/network 类先改（结果本就是结构化的），fs 类后改；保留 `to_text()` 渲染保证可读性不变 |
| `jsonschema` 新增依赖 | 依赖树变化 | 显式声明并锁版本区间；不引入第二套校验实现 |
| MCP annotations 是否透传不确定 | 风险等级可能要人工填 | 适配器里做映射 + 保留人工覆盖；先按"不继承"实现，透传确认后再打开 |
| 审批无人处理 | 调用悬挂 | 审批超时自动拒绝；pending 记录有告警 |

---

## 13. 附：现状 → 目标 映射

| 现状 | 目标 |
| --- | --- |
| `capabilities/entry.py` `CapabilityEntry` | `registration/spec.py` `CapabilitySpec`（拆开 risk/permission，补齐契约与策略） |
| `capabilities/registry.py` | `registration/registry.py` |
| `capabilities/builtin/` | `registration/builtin/` |
| `capabilities/orchestration.py` 检索部分 | `intent/planner.py` |
| `capabilities/orchestration.py` middleware | `intent/middleware.py` |
| `capabilities/orchestration.py` `validate_arguments` | `registration/validator.py`（换成 jsonschema，意图层与执行层共用） |
| `capabilities/adapter.py` | `execution/adapters.py`（只保留"执行"这一件事，工具构造挪到 `execution/tooling.py`） |
| `capabilities/result.py` | `execution/result.py`；错误码与 `CapabilityError` 提到 `capabilities/errors.py`（三层共用，避免注册层反向依赖执行层） |
| `capabilities/context.py` | `execution/context.py`（加 session / thread_id） |
| — | `intent/intent.py` `CapabilityIntent`（新增） |
| — | `execution/pipeline.py` 七阶段（新增） |
| — | `execution/risk.py` `execution/redaction.py` `execution/tooling.py`（新增） |
| — | `services/invocation.py` + `api/v1/invocation.py`（审批与查询，新增） |
| `models/execution_log.py` | `models/invocation.py`（改名扩列） |

---

## 14. 实施情况

### 已实现

| 期 | 内容 | 落点 |
| --- | --- | --- |
| P0 | 声明式契约：risk / permission / output_schema / timeout_s / retry_policy / annotations | `registration/`、`models/tool.py`、迁移 `0003`/`0004` |
| P1 | 调用记录下沉：`execution_log` → `invocation`，扩成完整生命周期 | `models/invocation.py`、`repositories/invocation.py` |
| P2 | 七阶段流水线，**执行层成为唯一路径** | `execution/pipeline.py`、`execution/tooling.py` |
| P3 | 14 个内置能力改结构化输出 + 输出契约校验 + **效果确认**（文件真写下来了没） | `registration/builtin/*`、`registration/builtin/verify.py` |
| P4 | 意图层：`CapabilityIntent` + resolver + middleware 改造 | `intent/` |
| P5a | 规则自动审批 + `invocation` 状态机 | `execution/pipeline.py::_decide_approval` |
| P5b | 人工审批接口（approve / deny / cancel + 查询） | `services/invocation.py`、`api/v1/invocation.py` |
| P6 | 结果脱敏、参数级风险规则、错误码扩展 | `execution/redaction.py`、`execution/risk.py`、`capabilities/errors.py` |
| P7 | 失败分层、任务置信度与阻塞、幂等闸门、指数退避、滑动窗口熔断 | `execution/task_state.py`、`execution/breaker.py`、`execution/pipeline.py` |

### 与设计文档的偏差

| 偏差 | 原因 |
| --- | --- |
| **审批返回 202**（不是 200 + status 字段） | 语义更准：202 表示"已受理，等审批"。响应体里仍有 `status` 字段 |
| **批准后由审批接口直接执行**，不挂起 agent | 挂起要靠 LangGraph `interrupt`，恢复时会重放节点，复杂度高。改为"审批接口按记录里的参数执行"后，agent 不必还活着 |
| **审批超时自动拒绝**（`CAPABILITY_APPROVAL_TIMEOUT`）目前只在配置里，没有后台任务去扫 | 待审批记录由查询接口暴露（`GET /invocations?status=pending`），超时清扫需要常驻任务 |
| **并发配额**（`CAPABILITY_MAX_CONCURRENCY`）目前只在配置里，没有生效 | 执行层还没做并发闸门 |
| **MCP annotations 继承**没有实现 | `langchain-mcp-adapters` 是否透传 annotations 未验证，先按"不继承"处理，风险等级人工填 |
| **`execution/approval.py` 没有单独成文件** | 审批逻辑就三个决策分支，放在 `pipeline.py` 里更直白；状态流转在 `services/invocation.py` |

### 已知限制

- **并发配额未生效**：一个 agent 循环理论上可以并发开很多沙箱进程。docker 模式有
  `--memory 512m --cpus 1 --pids-limit 256` 兜着，但那是单个容器的限制，不是会话级的。
- **审批无自动过期**：pending 记录不会自己变成 expired，需要人工处理或加后台清扫。
- **待审批记录的 `payload` 存的是原始参数**（不脱敏），因为批准后要按它执行。
  它不对外暴露（接口返回的是脱敏后的 `arguments`），但库里是明文。
- **结构化输出的 `content` 字段是给模型看的文本**，与改造前逐字一致；模型的行为不会因为
  返回结构变了而变化。

---

## 15. 失败分层、重试与熔断

这一节是实施过程中补上的 —— 原来只有"失败要分级"，但没说清不同类型的失败
**该让模型怎么反应**。

### 15.1 五道闸门，各管一件事

| 闸门 | 管什么 | 判定依据 |
| --- | --- | --- |
| **schema 校验** | 参数格式对不对 | 注册层声明的 `input_schema`（JSON Schema） |
| **权限校验** | 这次调用放不放行 | `permission` + 本次会话的授权范围 |
| **风险评估** | 自动执行还是转人工 | 声明的 `risk` + 参数级规则（SSRF、破坏性命令） |
| **超时** | 别长时间挂起 | 每个能力声明的 `timeout_s`，执行层再套一层兜底 |
| **重试** | 失败了能不能再来一次 | `retry` 声明 ∩ 错误自身的 `retryable` ∩ **能力是否幂等** |

### 15.2 重试必须过幂等这一关

早先的实现只看了「声明允许重试」和「错误可重试」两个条件，漏掉了幂等性 ——
于是 `run_bash` 声明了"超时就重试"，但它**不幂等**：超时只说明"不知道执行完没有"，
重跑可能把副作用做两遍。

现在重试要同时满足四条：

1. 还有次数；
2. 错误自身标了 `retryable`；
3. 错误码在 `retry_on` 白名单里；
4. **能力是幂等的**（`require_idempotent`，默认开）。

不幂等的能力（`run_bash`、`run_python`、`move_file`）超时不重试，直接转人工 ——
"不知道做没做完"的状态下自动重试，比不重试更危险。

### 15.3 失败分层：不同类型给模型不同指令

| 类型 | 错误码 | 模型的反应 | 平台的动作 |
| --- | --- | --- | --- |
| **格式类** | `SCHEMA_INVALID` | 改正参数后重试 | 扣置信度 10；同一能力连续 3 次就**阻塞** |
| **语义类** | `INVALID_ARGUMENTS` / `EXECUTION_ERROR` | 检查参数是否与实际目标一致 | 扣置信度 25；置信度低于 40 就**阻塞** |
| **权限类** | `PERMISSION_DENIED` / `RISK_BLOCKED` | **停止**，不要再试 | 直接终止；能力只是"未授权"时转人工授权 |
| **审批类** | `APPROVAL_REQUIRED` | 停止，告知用户 | 生成 pending 记录，批准后自动执行 |
| **熔断类** | `CIRCUIT_OPEN` | 换一条路 | 快速失败 |

**任务置信度**从 `invocation` 表的调用记录算出来（不是内存状态），所以跨进程、
跨请求都一致：格式类扣 10、语义类扣 25、成功回补 5，低于 40 判定阻塞。

阻塞之后执行层直接返回 `TASK_BLOCKED`，不再执行 —— 让模型知道该停，而不是
继续在一个错的方向上打转。

### 15.4 指数退避与熔断

- **退避**：`backoff_s × 2^(n-1)`，封顶 `max_backoff_s`（默认 30s）。
  可声明成固定间隔（`exponential=false`）。
- **熔断**：按能力维护滑动窗口，统计窗口内的错误率与延迟。样本数达到下限、
  错误率超过阈值就打开熔断，冷却期内该能力直接快速失败；冷却结束进入半开，
  放一次调用过去试试。慢调用（超过 `latency_ms`）也算"劣化"，一并计入。

熔断状态是**进程内**的 —— 多副本部署时各算各的。要跨副本共享得挪到 Redis，
现在还没那个必要。


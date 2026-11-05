# 能力模块：工具治理与执行

> 一句话：能力模块 = 平台所有"能被调用、能被治理"的东西的统一体系 —— 让每一次能力调用都经过同一条可声明、可校验、可审计、可拦截的流水线。
>
> 前端入口：`/capabilities`（`frontend/src/pages/CapabilitiesPage.tsx`，四个标签页：清单 / 工具 / 技能 / MCP）

---

## 1. 这个模块是什么

**能力模块 = 平台所有"能被调用、能被治理"的东西的统一体系。**

它的目标只有一句：**让每一次能力调用都经过同一条流水线**。

早先能力层的形态是"能跑"但不是"可治理"：能力靠描述文字注册，执行靠一个
`try/except` 包住，谁调了、调了什么、为什么失败，只能查到一半；更糟的是
**agent 内部的工具调用完全绕过了执行层** —— 于是没有日志、没有超时、没有重试、
没有策略检查。

能力模块把这件事补上，方法是**三个动词** —— 正好是同一条流水线上的三步：

```text
        注册层 registration                 意图层 intent              执行层 execution
   ┌──────────────────────────┐      ┌──────────────────────┐      ┌──────────────────────────┐
   │ CapabilitySpec           │      │ CapabilityIntent     │      │ execute()                │
   │ 声明式契约                │ ───▶ │ 一次调用意图           │ ───▶ │ 七阶段流水线               │
   │ 契约 + 策略               │      │ 模型提议，意图层收敛   │      │ 唯一执行路径               │
   │ 出厂源 → 数据库 → 统一清单 │      │ 不产生任何副作用       │      │ 校验/授权/风险/审批/调用/   │
   └──────────────────────────┘      └──────────────────────┘      │ 验证/留痕                  │
                                                                   └──────────────────────────┘
```

### 1.1 与 deepAgents 的分工

2026-11-01 之后，**agent 底座换成了 deepAgents**。分工是：

| 谁 | 提供什么 | 我们怎么接 |
| --- | --- | --- |
| deepAgents | 文件工具（`ls` / `read_file` / `write_file` / `edit_file` / `delete` / `glob` / `grep` / `execute`）、规划（`write_todos`）、子代理（`task`）、技能（`SKILL.md`）、记忆（`AGENTS.md`）、摘要、prompt caching | 换成我们的 backend（文件本地、命令进沙箱） |
| 我们 | 能力清单与声明、治理中间件、执行流水线、联网工具、MCP 工具、`run_python`、技能知识、调用记录 | 作为 deepAgents 的 `tools` / `middleware` / `subagents` 接进去 |

**我们不再自己实现文件工具** —— 路径安全、读写语义、结果校验都归 deepAgents 的
`FilesystemBackend`（虚拟模式：阻断 `..`，绝对路径映射进工作区）。我们只保留
它没有的那部分。

### 1.2 清单里有三类成员

| 成员 | 是什么 | 能不能被模型直接调 |
| --- | --- | --- |
| **tool** | 原子能力，最小可执行单元 | 有 input/output 契约 |
| **mcp** | 供给通道，一个外部 server 提供的一组能力 | 它是通道，通道上的工具才是能力 |
| **skill** | 能力组合：原子能力 + "怎么用它们"的知识 | 本身不执行，导出成 `SKILL.md` 交给 deepAgents 渐进披露 |

三者共用同一个 `CapabilitySpec` 结构，字段按 `kind` 取用。

### 1.3 边界的核心约定

**模型提议，意图层收敛。**

模型仍然决定"下一步调哪个能力" —— 它看得见对话历史，这是它不可替代的优势。
意图层做的是把模型这次 tool call **翻译并约束**成一个结构化意图
（`CapabilityIntent`）。意图层的产出是**一次调用**，不是一串步骤 —— 一串步骤
由模型的 ReAct 循环产生。

意图里的策略字段是**快照**：生成意图那一刻的判定，执行层不再重新推导。这样
"审批时看到的风险"和"执行时的风险"必然是同一个，不会中途变。

### 1.4 声明：一份模板长什么样

模板 = `CapabilitySpec`（运行时读的统一结构）+ `BuiltinSpec`（出厂声明）。
字段分三组：身份、契约、策略。

| 组 | 字段 | 取值 | 说明 |
| --- | --- | --- | --- |
| 身份 | `key` | `web_fetch` … | 模型看到的工具名，必须是合法标识符 |
| 身份 | `display_name` | 抓取网页 | 给人看的名字 |
| 身份 | `description` | 自由文本 | **给模型看的**，检索和选型都靠它 |
| 身份 | `tags` | `("网络", "抓取")` | 检索用的领域标签 |
| 身份 | `kind` | tool / mcp / skill | 清单里三类成员共用这一个结构 |
| 身份 | `origin` | builtin / user | 出厂内置 or 用户自建 |
| 身份 | `enabled` | bool | 停用的能力不进可用集合 |
| 契约 | `input_schema` | JSON Schema | **声明了 properties 就默认拒绝额外字段**（`normalize_schema`） |
| 契约 | `output_schema` | JSON Schema | 执行层据此校验返回值（校验时**忽略**多余字段） |
| 契约 | `source_type` | builtin / http / mcp | 决定用哪个适配器执行 |
| 契约 | `locator` | 见说明 | 执行入口：builtin 是 `模块:工厂函数`，http 是 method/url/headers/body 模板，mcp 是 server + tool_name |
| 策略 | `risk` | read / write / execute / network / destructive | 这能力**最坏**能造成什么 |
| 策略 | `permission` | none / confirm / admin | 谁来放行这次调用 |
| 策略 | `timeout_s` | 秒 | 单次调用上限 |
| 策略 | `retry` | `RetryPolicy` | 次数 / 退避 / 可重试错误码白名单 / 是否要求幂等 |
| 策略 | `annotations` | read_only / destructive / idempotent / open_world / egress_target / egress_data | 工具自述，词汇对齐 MCP tool annotations |

### 1.5 deepAgents 的文件工具没有声明 —— 用一张表补上

文件工具由 deepAgents 注册，我们手里没有 `CapabilitySpec`。它们的治理策略放在
`registration/tool_policy.py` 的映射表 `TOOL_POLICIES` 里，是三样东西的
**单一来源**：

- `interrupt_on` 集合（哪些工具要人批）；
- 风险基线（写进调用记录，参数级规则只能上调它）；
- 幂等标记（决定能不能自动重试）。

| 工具 | 风险 | 档位 | 权限 | 幂等 | 审批 |
| --- | --- | --- | --- | --- | --- |
| `ls` / `read_file` / `glob` / `grep` | read | 低 | none | ✓ | 自动放行 |
| `write_file` / `edit_file` | write | 低 | none | **✗** | 自动放行 |
| `delete` | destructive | 高 | none | ✗ | **永远**要人批 |
| `execute` | execute | 中 | none | ✗ | auto / manual 要人批 |

三处必须记住：

1. `delete` 是高档：写文件能靠再写一遍补救，**删掉的回不来** —— 只有「绕过权限」
   模式会放它，其余都要人批。
2. `write_file` / `edit_file` 标为**不幂等**：同样的参数写第二次，结果不一样 ——
   第一次写进去的内容会被第二次覆盖，而 `edit_file` 的第二次调用会因为
   "文件已变"报错。声明成幂等的话，执行层会拿上一次的**旧结果**直接返回，
   把那个错误吞掉 —— agent 以为改成功了，其实文件没动。
3. `execute` 不幂等：超时只说明"不知道执行完没有"，重跑可能把副作用做两遍。

我们自己的工具（16 个内置能力 + 用户自建 + MCP）走同一套判定 —— 它们的
`permission` / `risk` 来自声明。两边合起来才是完整的工具集，判定函数只有一个
（`policy.needs_approval`）。

### 1.6 风险档位与权限是两个正交维度

- `risk` —— 这能力**最坏**能造成什么（描述能力本身）；
- `permission` —— **谁来放行**这次调用（描述运行时策略）。

**审批按权限模式判**。模式是**会话级**的（`app/agents/permissions.py` 存文件，
跨进程可见），`CAPABILITY_TRUST_MODE` 只是默认值。语义对齐 Claude Code 的权限模式：

| 模式 | 行为 |
| --- | --- |
| `auto`（默认） | 按风险档位：低档自动放行，中档与高档要人批 |
| `manual` | 任何会改东西的操作都要人批（只读不问） |
| `accept_edits` | 文件编辑与命令都自动放行，只有破坏性要人批 |
| `plan` | 只读：写类与执行类工具**不暴露给模型**（中间件裁工具） |
| `bypass` | 全放行（含破坏性），受进程级总闸 `capability_allow_bypass` 约束 |

`auto` 模式下的风险档位：

| 档位 | 风险等级 | 要不要人批 |
| --- | --- | --- |
| 低 | read / write | 自动放行 —— 改错了能改回来 |
| 中 | execute / network | 要人批 —— 会在进程外产生副作用 |
| 高 | destructive | **永远**要人批 —— 不可逆 |

`permission` 是档位之上的两个口子：`confirm` = 运营额外要求人确认，
`admin` = 管理员放行（只有 `bypass` 会放它）。出厂能力的 `permission` 一律 `none`
—— 档位说了算，声明不重复表达一遍。

老配置名还认：`strict`→`manual`、`balanced`→`auto`、`permissive`→`bypass`。
认不出来的按 `auto` 归一 —— 宁可多问一次，也不要因为一个错字变成"全放行"。

### 1.7 出口维度：会不会出进程，与出口是不是模型说了算

`open_world` 只说"会不会访问进程之外的系统"。真正决定风险面的是**出口地址
是不是模型给的**：

| 字段 | 取值 | 含义 |
| --- | --- | --- |
| `egress_target` | `fixed` / `model_controlled` | 连到哪里是平台定的还是模型定的 |
| `egress_data` | yes / no | 有没有把数据发出去 |

只有 `model_controlled` 才是 SSRF 面（模型能把数据发到任意地址）。`fixed`
的出口（平台自配的向量化端点、内部服务）模型只能决定"发什么内容"，决定不了
"发到哪"。

新字段缺失时按**最保守**的默认处理（`model_controlled`）—— 改造前的老数据
行为完全不变，不需要写数据迁移。

---

## 2. 接口清单

全部前缀 `/api/v1`。

**统一能力清单**（`app/api/v1/capability.py`，prefix `/capabilities`）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/capabilities` | 列出全部成员。query：`kind`（tool/mcp/skill）、`tag`、`enabled` |
| GET | `/capabilities/{key}` | 按 key 取任意一类成员 |
| GET | `/capabilities/{key}/spec` | 完整声明（契约 + 策略：schema / 风险 / 权限 / 超时 / 重试 / 注解 / 权限模式） |
| POST | `/capabilities/{key}/invoke` | 直接调一次（调试用）。body：`arguments`；query：`thread_id`。**需审批时返回 202**，能力还没执行 |

**工具**（`app/api/v1/tool.py`，prefix `/tools`）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/tools/builtins` | 内置能力白名单（builtin 类型的工具只能引用这里的入口点） |
| GET | `/tools` | 分页查询。query：`page`、`page_size`（≤100）、`type`（builtin/http/mcp）、`enabled` |
| POST | `/tools` | 新增工具（201） |
| GET | `/tools/{tool_id}` | 详情 |
| PUT | `/tools/{tool_id}` | 更新 |
| DELETE | `/tools/{tool_id}` | 删除（204，同时解除技能引用） |
| POST | `/tools/{tool_id}/test` | 测试工具配置能否被构造出来（**只构造不执行**） |

**技能**（`app/api/v1/skill.py`，prefix `/skills`）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/skills` | 分页查询。query：`page`、`page_size`（≤100）、`enabled` |
| POST | `/skills` | 新增技能（201） |
| GET | `/skills/{skill_id}` | 详情（含 `tool_ids` / `tool_names`） |
| PUT | `/skills/{skill_id}` | 更新（`tool_ids` 整体替换） |
| GET | `/skills/{skill_id}/preview` | 预览挂载效果：会带哪些能力、往提示词里加什么 |
| DELETE | `/skills/{skill_id}` | 删除（204；内置技能 `builtin=1` 拒绝，只能停用） |

**MCP**（`app/api/v1/mcp_server.py`，prefix `/mcp-servers`）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/mcp-servers` | 查询列表。query：`enabled` |
| POST | `/mcp-servers` | 新增（201） |
| GET | `/mcp-servers/{server_id}` | 详情 |
| PUT | `/mcp-servers/{server_id}` | 更新 |
| DELETE | `/mcp-servers/{server_id}` | 删除（204；有工具引用时拒绝） |
| GET | `/mcp-servers/{server_id}/tools` | **连上去**探测该服务器提供的工具 |

**调用记录**（`app/api/v1/invocation.py`，prefix `/invocations`）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/invocations` | 分页。query：`thread_id`、`capability_key`、`status`、`offset`、`limit`（≤200）。响应带 `pending` 计数 |
| GET | `/invocations/{invocation_id}` | 详情（含各阶段结论） |
| POST | `/invocations/{invocation_id}/approve` | 批准并执行待审批的调用（**直接调用路径**） |
| POST | `/invocations/{invocation_id}/deny` | 拒绝。body：`reason` |
| POST | `/invocations/{invocation_id}/cancel` | 取消 |

**相邻路径**（属 agent 模块，但审批必须知道有两条路）：agent 在跑、撞上要人批的
调用时，审批入口是 `POST /api/v1/agent/{thread_id}/approve` / `reject`，恢复的是
LangGraph 的图；待审批列表是 `GET /api/v1/agent/{thread_id}/pending`。**agent 路径
的审批状态在 checkpoint 里，不在调用记录表里** —— 上面 `/invocations/*/approve`
只服务"直接调 API"那条路。

---

## 3. 实现要点

### 3.1 落点总览

| 层 | 目录 | 负责 |
| --- | --- | --- |
| 注册层 | `app/capabilities/registration/` | 声明、编目、安装 —— 产出**统一能力清单**；文件工具的策略映射表 |
| 意图层 | `app/capabilities/intent/` | 治理中间件（工具收敛 / 参数级风险 / 留痕）+ 一次 tool call → 一条意图 |
| 执行层 | `app/capabilities/execution/` | 七阶段流水线：校验 → 授权 → 风险 → 审批 → 调用 → 验证 → 留痕 |
| 后端 | `app/capabilities/backends/` | deepAgents 的 backend：文件本地、命令进沙箱 |

### 3.2 注册：出厂源 → 数据库 → 统一清单

出厂源在 `registration/builtin/__init__.py::BUILTIN_SPECS`，**16 条**出厂声明。
每个工厂函数签名为 `factory(context: ExecutionContext) -> Callable`，
`context` 由运行时注入，不会出现在给模型的 tool schema 里。

启动时 `registry.py::sync_builtins()` 把它同步进 `tool` 表（`origin=builtin`），
**只补空字段** —— 运营改过的内容不会被启动流程冲掉。删掉过的内置能力也不会被
自动装回来。

> **一条例外：契约以代码为准。** 入参 / 出参 schema **无条件**用出厂声明覆盖 ——
> 内置能力的实现是代码里写死的函数签名，契约跟着实现走。数据库里那份要是能
> 漂移，模型看到的就是旧契约，调用会被参数校验直接拦下。策略字段（风险等级、
> 权限要求、超时、重试、启用状态、描述）仍然听数据库的 —— 那些本来就该由
> 运营调。读清单时（`_tool_spec()`）同样优先用代码里的契约。

同一次同步还会**下线被 deepAgents 接管的出厂能力**（`_retire_superseded()`）：
`read_file` / `write_file` / `edit_file` / `glob` / `grep` / `list_files` /
`move_file` / `copy_file` / `make_dir` / `change_directory` / `run_bash`
—— 共 11 个，停用并清空执行入口（`source` 置空）。它们的历史调用记录留着，
审计时还能查。

> `source` 只能引用白名单里登记过的 `模块:工厂函数`。允许任意 `模块:函数`
> 等于允许任何能调工具管理接口的人让平台 import 并执行任意 Python 代码 ——
> 所以越界的 source 在 schema 校验层（`app/schemas/tool.py`）直接 422。

注册层把三张表聚合成一份清单：`tool` → 原子能力，`mcp_server` → 供给通道，
`skill` → 能力组合。**数据库是唯一事实来源**，`CapabilityRegistry.load()`
每次查库、**无缓存** —— 运营改完声明立刻生效，不存在"清单过期"这回事。

MCP server 在清单里是**通道**，权限写死 `confirm`：走外部 server 意味着平台
管不到那侧的副作用。

### 3.3 backend 组装：文件本地 + 命令进沙箱

`backends/assembly.py::build_backend()` 组出 deepAgents 的 `CompositeBackend`：

```text
CompositeBackend(
    default = DockerSandbox(工作区)            # execute 永远走 default
    routes = {
        "/":         FilesystemBackend(工作区),   # 文件操作：本地
        "/skills/":  FilesystemBackend(技能目录), # 技能：工作区外面
        "/uploads":  FilesystemBackend(上传件目录),
        "/.memory":  FilesystemBackend(会话目录),
        "/.artifacts/": FilesystemBackend(产物目录),
    }
)
```

**为什么这么组装**：`CompositeBackend` 按**路径前缀**路由文件操作，而**命令执行
不可按路径路由，固定走 default backend** —— 正好对上我们要的组合：文件操作要
快、要路径安全（`virtual_mode=True` 阻断 `..` / `~`，绝对路径映射进工作区），
命令执行要隔离。

**命令与文件分别落在哪**：

- **文件**（读 / 写 / 改 / 搜 / 删）落在**宿主机**的会话工作区里，走
  `FilesystemBackend` —— 本地、快、路径已经虚拟过；
- **命令**（`execute` / `run_python`）落在 **docker 容器**里，容器**只挂载会话
  工作区**，跑在断网、限资源的隔离环境里。

一个必须成立的一致性：容器只挂载会话工作区，所以容器里写的文件落在同一个
工作区目录，`execute` 造出的文件随后能被文件工具读到。

技能目录、产物目录挂在**工作区外面** —— agent 的文件工具只看得见工作区里的
东西，改不到这些导出文件。

隔离参数（`--network none --memory 512m --cpus 1 --pids-limit 256
--cap-drop ALL --security-opt no-new-privileges`）、容器复用规则、挂载预检
这些属于 storage-sandbox 模块，本篇只记"命令与文件分别落在哪、为什么这么组装"。

### 3.4 执行：七阶段流水线

`execute()` 在 `execution/pipeline.py`，负责**我们自己的工具**（联网 /
`run_python` / MCP / git / 记忆 / 知识库）。顺序是刻意的 —— 便宜的确定性检查
在前，昂贵的人介入在中，副作用在后：

```text
⓪ 任务状态    load_task_state()     execution/task_state.py
               置信度 / 重试阈值 / 是否已阻塞
① schema 校验 validate_arguments()  registration/validator.py   ← 权威校验
② 权限校验    _check_permission()   pipeline.py
               停用 / 不在允许集 → PERMISSION_DENIED
③ 风险评估    assess()              execution/risk.py
               声明的 risk + 参数级规则（破坏性命令 / SSRF）
④ 审批等待    _decide_approval()    pipeline.py
               按权限模式 + permission + 风险档位 → approved / pending / denied
⑤ 实际调用    _invoke()             pipeline.py
               幂等去重 → 熔断判断 → 超时 → 重试 → 适配器
⑥ 结果验证    _verify_output()      pipeline.py
               契约校验：output_schema
⑦ 结果回写    _finish()             pipeline.py
               写 invocation 表（脱敏参数 + 各阶段结论 + 置信度）
```

**任一阶段失败都走到 ⑦** —— 失败的调用同样要留痕，否则出问题就查不到。
`execute()` **不抛异常**，失败返回结果对象，让调用方（middleware / API）统一
处理"把失败翻译给模型"这件事。

第 ⑤ 步的内部顺序是刻意的：**先看能不能白拿结果**（幂等命中就复用上次结果，
不再执行一遍副作用），**再看这个能力是不是已经坏了**（熔断打开就直接快速失败，
别把时间耗在坏掉的能力上），最后才真的动手 —— 然后是超时兜底（声明的超时 +
5s 宽限，避免内外两层同时到点时误报）、重试、适配器执行。每次成败都记一份熔断
样本 —— 慢的失败和快的失败一样会拖垮 agent。

**能不能重试，四个条件都要满足**：

1. 还有次数；
2. 运行时错误标了 `retryable`（声明允许 ≠ 运行时值得）；
3. 错误码在 `retry_on` 白名单里；
4. **能力是幂等的**（`require_idempotent=true` 时）—— 不幂等的操作重试可能把
   副作用做两遍。

退避是指数的：`base × 2^(n-1)`，封顶 `max_backoff_s`（默认 30s）。

**并发闸门**：模型一次回复里可以带多个工具调用，它们是并行跑的 —— 一次回复里
来十个并行执行命令，就是十个沙箱同时跑。拿不到名额就排队等，超时了按"执行层
繁忙"失败（可重试）。**长任务（`delegate_worker`）不过闸门** —— 它内部自己
有一套闸门，再占着父会话的名额，父会话里并行的其它工具调用会排队等着，
最后报"执行层繁忙"，主 agent 派三个活反而把自己的工具全堵死了。

**效果确认（副作用真的发生了吗）已经不需要**：文件类能力由 deepAgents 提供，
它的 `WriteResult` / `ReadResult` 自带错误字段，"写没写成功"由它自己回答。

### 3.5 治理落在中间件层

deepAgents 的所有工具调用（它自己的文件工具、我们的工具）都会经过
`CapabilityMiddleware.awrap_tool_call`，所以治理在这一层做：

```text
模型调用前  awrap_model_call
             ├─ 把工具收敛到本次允许的范围
             └─ 追加策略说明（哪些工具会要人批）

工具调用中  awrap_tool_call
             ├─ 参数级风险规则命中 → interrupt() 挂起等人批
             ├─ 执行
             └─ 文件工具：写一条简化的调用记录 + 更新任务置信度

工具调用后  写 invocation 表
```

**两条审批路径，都落在 LangGraph 的中断机制上**：

| 路径 | 触发 | 挂起点 |
| --- | --- | --- |
| **声明级** | 工具在 `interrupt_on` 里（执行命令、联网、删除…） | 工具执行**之前**（deepAgents 的 HITL） |
| **参数级** | 命令含 `rm -rf`、URL 指内网 | 工具执行**中**（我们自己的 `interrupt()`） |

**一次调用只让人批一次。** 声明级先于工具执行发生 —— 工具能跑到参数级检查，
说明人已经看过这次调用并批过了，参数级就不再重复问。参数级只管**声明级放行过
的那类调用**：平时不用人批的工具，碰到破坏性命令 / 内网地址时单独拎出来问一次。

但**声明级批过，不等于这一次调用就安全了** —— 声明级看的是**工具**（"execute
这个工具要人批"），参数级看的是**这次调用的参数**（"这条命令里有 rm -rf"）。
所以声明级那张审批卡片上显示的风险档位是**参数级上调后的**（"破坏性"而不是
"执行命令"），由 `runner._pending_entries` 算出来。

两处中断的载荷形状不同，但恢复值统一成 deepAgents HITL 的格式：

```json
{"decisions": [{"type": "approve"}]}
```

**"要不要人批"的判定只有一份**（`registration/policy.py::approval_decision` /
`needs_approval`），三个地方都用它：deepAgents 的 `interrupt_on`、中间件告诉
模型的那句话、执行层在直接调用路径上的判定。三处各写一套，批准过了又被拦一次
就是这么来的。

执行层在 agent 路径上**不再问一遍**：审批已经由 HITL 负责，工具能跑到执行层就
说明人已经批过了（`ExecutionContext.approval_handled` / `intent.pre_approved`）。
直接调 API 的那条路径不走图，仍然由执行层自己决定要不要挂起。

### 3.6 审批：挂起 → 批准 → 图恢复

```text
POST /api/v1/agent/chat
   │
   ▼ 模型调工具 → 命中 interrupt_on → LangGraph 挂起（checkpoint 里）
   │
   ├─ 响应 status = "pending_approval"，pending 列出待审批的调用
   │
   ▼ 人工处理
   │
   ├─ POST /api/v1/agent/{thread_id}/approve
   │     → Command(resume={"decisions": [{"type": "approve"}]})
   │     → 图恢复，工具执行，**agent 自己接着往下跑**
   │
   └─ POST /api/v1/agent/{thread_id}/reject
         → Command(resume={"decisions": [{"type": "reject"}]})
         → 工具不执行，agent 收到拒绝消息后继续
```

**这跟改造前最大的不同**：以前审批后 agent 那一轮就结束了，结果要等用户说下一句
才回流给它。现在图恢复，agent 接着跑，结果直接回到它的上下文。

审批状态存在 **LangGraph 的 checkpoint 里**，不在调用记录表里 —— 图挂在哪儿只有
它知道。

> 直接调 API 的那条路径（`POST /api/v1/capabilities/{key}/invoke`）不走图，
> 仍然是"生成 pending 记录 → `POST /api/v1/invocations/{id}/approve` 执行"。
> 两条路径各有各的入口，但都落到同一套执行流水线。

### 3.7 模型提前知道哪些工具会要人批

审批规则抽成了单一事实来源（`tool_policy.py::approval_required_tools()` +
`policy.py::approval_decision()`），中间件拿它做判定，同时**提前告诉模型**：

```text
当前权限模式：自动。
审批按风险档位：只读、写入是低档，直接执行；执行命令、联网是中档，需要人工
审批；破坏性操作是高档，永远需要人工审批。
以下工具需要人工批准才能执行：delete、execute。
每次触发审批都会中断当前任务等人工处理，调用前请把参数一次做对。
另外：平时不用人批的工具，碰到命令里含删除/写设备等特征、或抓取的是内网地址，
也会命中参数级风险规则，单独问一次。
```

### 3.8 风险：参数级规则只能上调

`execution/risk.py::assess()` 是参数级风险评估的唯一入口。声明里的 `risk` 是
**基线**；参数级规则可以把它**上调**，不能下调。

**破坏性命令特征**（8 条正则，命中即顶到 `destructive` + 管理员放行 + 算威胁
信号）：`rm -rf` 一类（递归 / 强制删除）、`dd if=`（直接写设备）、`mkfs`
（格式化文件系统）、`chmod -R 777`（递归放开权限）、`chown -R`（递归改属主）、
`curl … \| sh` 与 `wget … \| sh`（下载并直接执行脚本）、`> /dev/sd*` 与
`/dev/nvme*`（写裸设备）。

**源码里的破坏性调用**：`shutil.rmtree` / `os.remove` / `os.unlink` / `os.rmdir`。

**按参数名读内容，不按工具名读** —— 参数名是 `command` / `code` / `script` /
`source` / `program` 五个。按工具名写死的话，`execute`（参数叫 `command`）能盖住，
`run_python`（参数叫 `code`）就漏了 —— 一段 `shutil.rmtree("/")` 完全逃逸。

**URL 指内网**（SSRF）：`localhost` 与 8 段内网 CIDR（`127/8`、`10/8`、
`172.16/12`、`192.168/16`、`169.254/16`、`::1/128`、`fc00::/7`、`fe80::/10`）。
域名查不出 IP 时返回 False —— DNS 重绑定那类手法得在解析之后再判一次。

**威胁信号 vs 分类调整**：`RiskAssessment.threat` 区分两件不同的事 ——
"会向固定出口发数据"是**分类调整**（给这次调用贴标签），"出口由模型决定"、
"命令含 rm -rf"、"URL 指内网"是**威胁信号**。参数级的挂起**只由威胁信号触发**：
声明级已经放过一次了，再挂一次等于同一次调用让人批两遍。不分开就会踩一个很
实际的坑 —— 平台的向量化端点会被评估成 write，于是**每查一次知识库都要人工
审批**。

### 3.9 失败分层：不同类型给模型不同指令

失败按类型分层，因为不同类型的失败**模型该有的反应不一样**：

| 类型 | 错误码 | 给模型的指令 |
| --- | --- | --- |
| **格式类** | `SCHEMA_INVALID` | 改正参数后重试；同一能力连续 3 次就阻塞转人工 |
| **语义类** | `INVALID_ARGUMENTS` / `EXECUTION_ERROR` / `OUTPUT_INVALID` | 检查参数与目标是否一致，别重复同样的调用；**扣任务置信度** |
| **权限类** | `PERMISSION_DENIED` / `RISK_BLOCKED` | **不许重试**，直接终止 |
| **审批类** | `APPROVAL_REQUIRED` | 挂起等人工，批准后恢复 |
| **熔断** | `CIRCUIT_OPEN` | 换一条路，别再重复调用 |

指令由 `CapabilityError.to_text()` 统一渲染：可重试的说"（可以修正参数后重试）"，
不可重试的说"（换个思路，不要重复同样的调用）"。

**任务置信度**从调用记录算出来（格式类扣 10、语义类扣 25、成功回补 5），
不是内存里的状态 —— 跨进程、跨请求都一致。低于阈值（默认 40）就标记阻塞转人工。

阻塞有两道线：置信度低于阈值，或者同一能力的格式类错误攒够次数。

### 3.10 留痕：调用记录

每次调用写一条 `invocation`（`models/invocation.py`）。

**两列参数的差别很重要**：

| 列 | 内容 | 谁看 |
| --- | --- | --- |
| `arguments` | **脱敏后**的参数 | 人（排障、界面展示） |
| `payload` | **原始**参数 | 只有执行层 —— 审批通过后要按它执行，脱敏了就执行不了 |

`status` 有八个取值：`pending` / `approved` / `denied` / `running` / `succeeded` /
`failed` / `cancelled` / `expired` / `blocked`。`stages` 存各阶段结论，有了它就能
一眼看出"这次调用卡在哪一关"。

**两层失败判定** `is_failure(record)`：

1. 记录的状态（失败 / 被阻塞 / 被拒绝 / 审批过期）；
2. **退出码非零** —— 工具本身跑成了（记录是 `succeeded`），但**要做的事没做成**。

第二层是代码 agent 最常见的失败形态。`run_python` 的职责是"在沙箱里跑一段代码
并把退出码与输出带回来" —— 代码抛异常时这个职责**履行了**，所以记录是成功的。
但从 agent 的角度那次尝试就是失败了：它想算的东西没算出来。只看状态的话，
反思与复盘在**最需要它们的场景里**都是空的。

这条判定被三处共用 —— 反思块（每轮注入）、复盘的候选筛选、复盘的复发率统计。
放在一处，它们才不会各说各话。

**脱敏**在 `execution/redaction.py`：6 条凭据正则（OpenAI / Anthropic / GitHub /
AWS / Google / `password|secret|api_key|access_token` 赋值）。**只脱敏落库的部分**
—— 给模型看的文本保持原样（模型需要看到真实内容才能干活）。

**文件工具的记录由治理中间件写**（它们不走执行层），字段少一些：没有分阶段结论，
只有结果摘要与置信度。

### 3.11 技能：数据库 → SKILL.md → 渐进披露

skill 是清单里的第三类成员：**一组原子能力 + 一份"怎么用它们"的知识**。

| 字段 | 作用 |
| --- | --- |
| `knowledge` | **核心**：核心做法 |
| `checklist` / `examples` / `constraints` | 检查项 / 正反例 / 约束与禁区 |
| `tags` | 检索用的领域标签 |
| `tool_ids` | 关联的原子能力（`skill_tool` 关联表，不是 JSON 数组 —— 避免悬挂 id，方便反查，删工具时能级联解绑） |

一次对话里技能的流程：

```text
用户消息 + 可选 skill_ids
   │
   ▼ skills_export.export_skills()
   │  数据库里的技能 → <工作区外>/.skills/<会话>/<技能名>/SKILL.md
   │  （frontmatter 写 name / description，正文顺序：知识 → 检查项 → 约束 → 示例 → 承载的能力）
   │  目录名必须合法：小写 ASCII 字母 / 数字 / 单个连字符
   │  中文名转不出来 → 退化成 skill-<sha1[:8]>，中文名写进 description
   ▼ create_deep_agent(skills=["/skills/"]) → backend 把 "/skills/" 路由到技能目录
   ▼ 渐进披露：提示词里只有技能的名字和描述，模型觉得相关了才去读正文
   ▼
模型产出 tool call → 治理中间件 → 执行
```

**技能知识不进系统提示词** —— 提前全塞进去就把"按需加载"退化成"每次都全量加载"。

**当前没有出厂内置技能** —— `builtin=1` 这个标记和"内置技能不允许删除"的保护
都在，但没有任何种子数据，技能全部靠用户 / 运营通过接口创建。

> **行为变化**：技能**不再收敛工具集**。以前命中一个技能会把暴露给模型的工具
> 裁到"技能成员 + 出厂内置能力"；现在 deepAgents 的技能是**知识**的渐进披露，
> 不是工具的子集 —— `tool_ids` 只作为"这个技能适用于哪些工具"的说明与预览信息，
> 不再影响 agent 能调什么。

### 3.12 MCP：一个外部工具怎么变成模型可调的工具

链路是五步，**其中两步是手工的**：

1. 用户建一条 `mcp_server` 记录（name / transport / command|url / env / headers）；
2. 用户**手动**建一条 `type=mcp` 的 tool 记录 —— `source` 是服务器名，
   `config.tool_name` 是远端工具名，`risk` / `permission` **人工填**；
3. 注册表把这条 tool 记录转成 `CapabilitySpec`（`locator = {server, tool_name}`）；
4. `tooling._build_mcp_tool` 连上服务器取远端工具，**入参 schema 以 MCP server
   侧声明为准**（`found.args_schema`）；
5. 执行走统一流水线（`McpAdapter` → `MCPManager.find_tool` → `ainvoke`）。

入参契约由 server 说了算，不看我们这边的声明 —— 两边对不上时以 server 为准，
不然调过去一定失败。

MCP server 记录本身在统一清单里是**通道**（`kind=mcp`），权限写死 `confirm`：
走外部 server 意味着平台管不到那侧的副作用。

### 3.13 我们自己的内置能力（16 个）

全部 `permission=none` —— 审批由风险档位决定。

| key | 风险 | 档位 | 超时 | 幂等 | 做什么 |
| --- | --- | --- | --- | --- | --- |
| `run_python` | execute | 中 | 60s | ✗ | 在沙箱里跑一段 Python，返回退出码 / 输出 / 耗时 |
| `web_fetch` | network | 中 | 20s | ✓ | 抓 http/https 页面并转纯文本 |
| `web_search` | network | 中 | 20s | ✓ | 走 Tavily / Serper / Bing 搜索 |
| `git_status` | read | 低 | 30s | ✓ | 当前工作目录的分支、未提交改动、文件列表 |
| `git_worktree_add` | write | 低 | 180s | ✗ | 从当前仓库开一个新 worktree（独立检出 + 新分支） |
| `git_worktree_list` | read | 低 | 30s | ✓ | 列出当前仓库的所有 worktree |
| `git_worktree_remove` | destructive | 高 | 180s | ✗ | 删掉一个平台创建的 worktree 目录（不删分支） |
| `git_commit` | write | 低 | 60s | ✗ | 把当前工作目录的全部改动提交一次 |
| `git_merge` | destructive | 高 | 120s | ✗ | 把一个分支合进当前分支，冲突时列出冲突文件 |
| `delegate_worker` | execute | 中 | 1800s | ✗ | 把一个子任务交给子 agent：独立 worktree 里完成并提交 |
| `memory_search` | read | 低 | 20s | ✓ | 召回记忆（会话内 + 跨会话），返回摘要与 id |
| `memory_read` | read | 低 | 20s | ✓ | 按 id 取一条记忆的完整正文 |
| `memory_write` | write | 低 | 20s | ✓ | 写入一条记忆（同内容合并成同一条） |
| `rag_search` | read | 低 | 30s | ✓ | 在 RAG 知识库里检索，返回标题、来源与摘要 |
| `rag_read` | read | 低 | 30s | ✓ | 按 id 取一篇文档的完整正文 |
| `invocation_recent_failures` | read | 低 | 20s | ✓ | 列本会话最近几次没成功的能力调用，带错误码与完整报错 |

几个必须记住的点：

- **git_* 与 `delegate_worker` 只认当前工作目录**。会话没绑 git 目录时它们不会
  出现在模型的工具列表里（`AgentService._visible_specs`）—— 给模型一批用不了的
  工具，它会试了再报错，浪费轮次。
- **git_* 全都不幂等** —— 重复执行会重复建目录 / 重复提交，所以超时不重试。
- **worktree 相关的能力超时给得特别长**（180s / 120s）：检出大仓库要时间。
- **`delegate_worker` 的 worker 自己不挂人工审批**（没有界面），防线是物理隔离
  —— 独立 worktree + 断网沙箱容器。
- **`memory_*` / `rag_*` 的注解是 `egress_target=fixed`**（出口平台配好），所以
  按低档处理、不按 network 拦 —— 否则 auto 模式下每查一次知识库 / 记忆都要人批。
  它们的声明风险是 `read`，运行时会被**分类调整**成 `write`（仍然低档），不触发
  威胁信号。
- **`run_python` 声明了超时重试但实际不重试** —— 它不幂等，重跑可能把副作用
  做两遍。执行层把这类超时降级成不可重试，并在错误信息里写明"不要重复执行同样
  的命令"。
- **`invocation_recent_failures` 存在的原因**：平台早就把每次调用的轨迹写进了
  `invocation` 表，但那张表只有人在管理界面看得见 —— agent 自己查不到。于是它会
  在同一轮里反复试同一个失败的做法：不是它不长记，是**没有记可读**。
- `run_bash` **不存在** —— deepAgents 的 `execute` 就是它（命令进同一个沙箱），
  两个功能重复的工具留着只会让模型选错。它连同 10 个文件类出厂能力在启动同步时
  被 `_retire_superseded()` 停用。

### 3.14 子代理

deepAgents 自带的通用子代理**不继承**我们自定义的中间件 —— 它的中间件是按默认
槽位继承的，`CapabilityMiddleware` 不在里面，子代理的工具调用就不会经过风险
判定和留痕。

所以我们自带一份子代理规格（`builder.py::_general_subagent()`），显式带上同一套
治理中间件和同一份 `interrupt_on`：

```text
主 agent ── task 工具 ──▶ 子代理（isolated，有自己的上下文）
                            └─ 工具调用同样经过 CapabilityMiddleware
                               → 同样挂起等人批、同样写调用记录
```

### 3.15 agent 侧怎么用能力

一次对话的完整链路：

```text
POST /api/v1/agent/chat  { message, thread_id, skill_ids?, provider_id? }
   │
   ▼ services/agent.py::AgentService.chat
   ① 分配工作区 resolve_workspace(thread_id) → WORKSPACE_ROOT/<thread_id>
   ② 选模型 _resolve_model(provider_id) → 默认供应商，没配置就退回 .env
   ③ 加载能力清单 load_capabilities() → CapabilityRegistry.load()
   ④ 建执行上下文 _build_context() → 工作区 + 本次用到的 MCP 供给通道 + DB 会话
   │
   ▼ agents/runner.py::_build
   ⑤ 导出技能 export_skills(specs, 技能目录, skill_ids)
   ⑥ 组装提示词 assemble_prompt(CODE_AGENT_PROMPT, runtime_notes)
   │
   ▼ agents/builder.py::build_agent
   ⑦ 能力 → 模型可调工具 build_tools(specs, context, skip_names=FS_TOOL_NAMES)
   ⑧ 装 middleware CapabilityMiddleware + TodoListMiddleware
   ⑨ 组装子代理 显式带同一套治理中间件
   │
   ▼ create_deep_agent(model, tools, system_prompt, checkpointer, middleware,
                       backend, interrupt_on, skills, memory, subagents)
   │
   ▼ 模型循环：思考 → 调工具 → 结果回灌 → 再思考
   ▼ CapabilityMiddleware.awrap_tool_call：参数级风险 → 执行 → 留痕
```

四个关键点：

**① 文件工具不走我们的执行层**。它们由 deepAgents 直接执行，我们只在中间件里
补一条调用记录。**我们自己的工具**（联网 / `run_python` / MCP / git / 记忆 /
知识库）仍然走执行层七阶段。

**② 工具的 coroutine 不执行能力，只构造意图**（`execution/tooling.py`）：

```python
async def call(**kwargs: Any) -> str:
    intent = CapabilityIntent(capability_key=spec.key, arguments=dict(kwargs), spec=spec, ...)
    result = await execute(intent, context)
    return result.to_text()
```

早先工具函数被 LangChain 直接调用，**绕开了执行层** —— 没有日志、没有超时、
没有重试、没有策略检查。现在 agent 内部调用和直接调 API 走同一条路。

**③ 模型看到的参数表就是真正生效的那份约束**。`input_schema` 一份声明两处用：
给模型看（LangChain 的 `args_schema`），和给平台校验（执行层 ① 阶段）。用户自建
的能力常常不填入参，这时从实现函数签名兜一份 —— 给模型一张空参数表是很糟的，
它不知道该传什么。

**④ 出错时给模型的是分层指令，不是裸 traceback**。见 §3.9。

### 3.16 AGENTS.md 不是记忆

工作区里的 `AGENTS.md` 是**工作区级、agent 自己维护的说明文件**：每轮全量注入
系统提示词、不受预算控制、不做相关性排序。文件不存在时由
`builder.py::_ensure_memory_files()` 建一个空壳。`create_deep_agent(memory=["/AGENTS.md"])`
把内容注入每轮的系统提示词。

**平台的记忆模块是另一回事**：短期记忆落 `memory_item` 表（一个 thread 内），
长期记忆落 RAG 的专用集合（名字 `agent-memory`，跨会话），共两层。agent 通过
`memory_search` / `memory_read` / `memory_write` 三个内置能力访问它们 —— 那是
**能力调用**，走统一流水线，因此有风险评估、审批判定和调用留痕；不是往提示词里
塞一坨文本。

### 3.17 不变量：改错了会出事的地方

这一节是本文档里最值钱的部分 —— 这些坑是**实测复现过的**，不是设想。

| 不变量 | 改错了会怎样 |
| --- | --- |
| 审批判定只有一份（`policy.needs_approval`），三处共用 | 模型被告知"直接放行"实际却被拦下，或者批准过了又被拦一次 |
| **审批闸的位置不能随权限模式变**（`builder._interrupt_on()` 永远挂全部候选工具，`when` 谓词运行时重读模式） | 挂起期间用户把模式切到「绕过权限」，图被重建成没有闸的样子，checkpoint 里那条挂起的中断"看不见"了，恢复变成空转 —— 接口返回 200，工具根本没跑，界面表现是"审批后无法继续" |
| 契约以代码为准，策略字段听数据库 | 弄反了：运营改了参数 schema，模型看到新契约、代码里的实现接不住，调用必然失败；反过来每次启动把策略冲回默认，治理就成了摆设 |
| 参数级规则只能上调风险，不能下调 | 声明了 `destructive` 的能力被参数规则判成 `read`，绕过治理 |
| 参数级挂起只由**威胁信号**触发 | 平台自配的向量化端点被评估成 write，于是每查一次知识库 / 记忆都要人工审批 |
| 出厂能力下线时要**清空执行入口**（`source` 置空） | 执行入口指向已经删掉的模块，一旦被调到就 import 失败 |
| `arguments` 脱敏、`payload` 不脱敏 | 反过来：审批通过后按脱敏过的参数执行，拿到的是错的入参；或者把凭据明文展示出去 |
| 执行层不抛异常，失败返回结果对象 | 异常穿到 middleware，模型看到的是裸 traceback 而不是分层指令 |
| 不幂等能力的超时不重试 | 重跑把副作用做两遍；`run_python` 跑两遍等于把数据写两遍 |
| `write_file` / `edit_file` 标为不幂等 | 声明成幂等，执行层会拿上一次的旧结果直接返回，把"文件已变"的错误吞掉 —— agent 以为改成功了，其实文件没动 |
| `delete` 是高档，只有 bypass 放它 | 删掉的回不来 |
| 任一阶段失败都走到 ⑦ 留痕 | 失败的调用查不到 —— 出问题就断线 |
| 沙箱不可用时不静默降级 | LLM 生成的代码在没有隔离的环境里执行 |
| 技能导出目录挂在工作区外面 | agent 的文件工具看得见并可能改写这些导出文件 |
| 技能目录名必须小写 ASCII / 数字 / 单连字符 | 目录名与 frontmatter 的 `name` 不一致，deepAgents 的技能中间件认不出来 |
| builtin 的 `source` 只能引用白名单里的 `模块:工厂函数` | 任何能调工具管理接口的人都能让平台 import 并执行任意 Python 代码 |
| `_gates` 只增不删（并发闸门） | 长会话跑久了内存缓慢增长 —— 见 §4 |

---

## 4. 已知限制

每条都是**现在还成立**的，写清现状与影响面。

| # | 限制 | 现状 | 影响面 |
| --- | --- | --- | --- |
| 1 | **并发配额是进程内计数** | `execution/concurrency.py` 的 `LIMITER` 是进程内单例，`_gates` 按 `thread_id` 计数 | 多副本部署时每个副本各自计数，不是全局配额 —— 一个会话在 N 个副本上就能同时跑 N×限额的能力 |
| 2 | **熔断器也是进程内状态** | `execution/breaker.py::get_breaker()` 是全局单例，滑动窗口存在内存里 | 多副本部署时每个副本各判各的，坏掉的能力在别的副本上照样被反复调用 |
| 3 | **审批过期只覆盖调用记录表那条路** | `services/invocation.py::expire_stale_approvals()` 只把 `invocation` 表里的 `pending` 置为 `expired`；图中断（checkpoint）那条路没有对应的过期机制 | agent 路径上的挂起不会自动过期，挂在那里等人批；人一直不批，图就一直挂着 |
| 4 | **MCP 注解继承没打通** | `policy.py::Annotations.from_mcp()` 已经实现（MCP 的驼峰提示字段 → 平台蛇形命名），但**全仓无调用点** | MCP 工具的风险等级**目前全靠人工填**。填错就绕过治理 —— 一个能删库的 MCP 工具被填成 `read`，就自动放行了 |
| 5 | **MCP 工具的登记完全靠手工** | 没有"探测后一键登记"；`GET /mcp-servers/{id}/tools` 只把远端工具列出来（name + description），登记仍要用户手写一条 `type=mcp` 的 tool 记录 | 登记成本高，且第二步手写的那条记录（`config.tool_name`、`risk`、`permission`）没有任何校验能替用户填对 |
| 6 | **MCP server 的 env / headers 明文入库** | `models/mcp_server.py` 里 `env` / `headers` 是普通 JSON 列，不走 Fernet 加密 | 凭据以明文形式躺在数据库里，备份、只读账号、SQL 注入都能拿到 |
| 7 | **MCPManager 每次调用都新建 client** | `mcp_client.py::MCPManager` 每次 `list_tools` / `find_tool` 都 `MultiServerMCPClient({...})` 新建一个，没有连接池 | 每次调用都要重新建连 —— 延迟叠加，也吃掉了 server 侧的长连接优势 |
| 8 | **没有出厂内置技能** | `skill` 表的 `builtin=1` 标记与"内置技能不允许删除"的保护都在，但**没有任何种子数据** | 新环境开箱没有可用的技能知识；技能检索命中率为 0 时，模型看到的技能列表是空的 |
| 9 | **暴露给模型的工具集没有检索收窄** | `builder.py::build_agent()` 的 `allowed_tool_keys` 参数**从来没有被传入过**（调用方 `runner.py` 不传），实际传的是 `None` = 不限制 | 能力集退化成全量暴露 —— 模型每轮都看到全部工具，选型靠描述文字而不是靠候选集收敛 |
| 10 | **沙箱镜像不可扩展** | `SANDBOX_IMAGE` 默认 `python:3.13-slim`，第三方脚本的依赖靠镜像预装，运行时装包没有通路 | 镜像里没有的包就装不上；为一个新依赖改镜像意味着整个平台换镜像 |
| 11 | **待审批记录存明文参数** | `_finish()` 写 `payload=dict(intent.arguments)`，**不走脱敏**（脱敏的是 `arguments` 那列） | 待审批的调用把原始参数明文躺在表里 —— 凭据类参数也一样 |
| 12 | **执行摘要的配对不分轮次** | `runner.py::_attach_summaries` 用 `repo.list_recent(thread_id)` 拿"这个会话最近 N 条"记录，按工具名 + 参数配对；`_match_record` 还有一条"兜底按顺序拿一条"的路径 | 这一轮显示的耗时与结果摘要有可能来自**上一轮** —— 尤其在上一轮用了同名工具、参数又恰好相同的时候 |
| 13 | **并发闸门的 `_gates` 字典只增不删** | `concurrency.py::_gate()` 只往 `self._gates` 里塞，没有淘汰逻辑 | 会话数一多，字典只涨不落 —— 长跑的进程里是一处缓慢的内存泄漏 |
| 14 | **并发闸门只管住了"自己调能力"那条路** | deepAgents 自带的文件工具（`execute` / `write_file` / `edit_file`）**不走执行层流水线** —— 它们直接落到 backend。闸门在 `pipeline.py::_invoke` 之前拦，拦不到它们 | agent 一次回复发八个工具调用，八个文件操作会**一起跑** —— 闸门形同虚设 |
| 15 | **谁把哪个会话提成了 bypass，没有留痕** | 会话级权限模式写文件（`app/agents/permissions.py`），写入时不记"谁在什么时候改的" | 提权这件事本身没有审计线索 —— 出事后查不出是谁提的 |
| 16 | **平台无鉴权** | 所有接口都不校验身份（这是平台级的既有决策） | 能调工具管理接口的人可以让平台 import 并执行任意 Python 代码 —— `source` 白名单校验只挡了越界的写法，接口本身没有身份。知道 `thread_id` 就能读写别人的会话、文件与记忆 |
| 17 | **文件与命令那 8 个工具不在流水线里** | `registration/builtin/__init__.py` 明写"文件与命令类工具不在这里 —— 它们由 deepAgents 的文件系统中间件提供"。于是 `ls` / `read_file` / `write_file` / `edit_file` / `delete` / `glob` / `grep` / `execute` **不走七阶段**，只在 `intent/middleware.py::_record()` 落一份简化的调用记录（没有分阶段结论） | **"每次能力调用都过同一条流水线"这句话对文件 / 命令族不成立** —— 而这恰恰是风险最高的一族（`delete` 不可逆、`execute` 能跑任意命令）。它们**仍过**参数级风险判定（`middleware.py` 的 `assess_call`），但**没有** schema 校验、权限校验、幂等去重、熔断、结果验证 |
| 18 | **技能全量拼接是死代码** | `app/capabilities/prompt.py::assemble_prompt()` 把技能的 knowledge / checklist / constraints / examples 全量拼进系统提示词，但全仓只有两个调用点 —— `capabilities/__init__.py` 的再导出，与 `services/capability.py` 的技能预览接口。agent 主路径走 `context/service.py::assemble()`，技能那条路是导出成 `SKILL.md`（框架的渐进披露） | 留了两条路，活的只有一条。读代码的人会以为技能正文进的是系统提示词 —— 实际没有 |
| 19 | **脱敏只认 6 类凭据** | `execution/redaction.py` 只认 `sk-` / `sk-ant-` / `gh[pousr]_` / `AKIA` / `AIza` / `password=` 六类，掩码 `***`，且**只脱敏落库副本** —— 给模型看的原文不动 | 邮箱、手机号、身份证这类**个人信息**在轨迹里是明文。框架的 `PIIMiddleware` 能补上，但它会改写消息体，与"原样落库 `Invocation.arguments`"的语义冲突 —— 两者方向相反，只能叠加不能替换 |

---

## 5. 相关文件

| 关注点 | 文件 |
| --- | --- |
| 出厂源（16 条内置声明） | `backend/app/capabilities/registration/builtin/__init__.py` |
| 各内置能力的实现 | `backend/app/capabilities/registration/builtin/{sandbox,web,git,delegate,memory,rag,invocation}.py` |
| 声明字段定义 | `backend/app/capabilities/registration/spec.py` |
| 风险 / 权限 / 重试 / 注解 / 审批判定 | `backend/app/capabilities/registration/policy.py` |
| **文件工具的策略映射表** | `backend/app/capabilities/registration/tool_policy.py` |
| 出厂源 → 数据库同步（含下线） | `backend/app/capabilities/registration/registry.py` |
| schema 校验 / 给模型看的入参模型 | `backend/app/capabilities/registration/{validator,args_model}.py` |
| **backend 组装** | `backend/app/capabilities/backends/assembly.py` |
| **docker 沙箱后端与容器复用** | `backend/app/capabilities/backends/{sandbox_backend,container_pool}.py` |
| **技能导出** | `backend/app/capabilities/skills_export.py` |
| 意图结构 / 幂等键 / 意图生成 | `backend/app/capabilities/intent/{intent,resolver}.py` |
| **治理中间件** | `backend/app/capabilities/intent/middleware.py` |
| 执行流水线 | `backend/app/capabilities/execution/pipeline.py` |
| 参数级风险规则 | `backend/app/capabilities/execution/risk.py` |
| 执行适配器 | `backend/app/capabilities/execution/adapters.py` |
| 能力 → 模型可调工具 | `backend/app/capabilities/execution/tooling.py` |
| 并发闸门 / 熔断 | `backend/app/capabilities/execution/{concurrency,breaker}.py` |
| 脱敏 | `backend/app/capabilities/execution/redaction.py` |
| 失败分层与置信度 | `backend/app/capabilities/execution/task_state.py` |
| 提示词组装 / 错误码 | `backend/app/capabilities/{prompt,errors}.py` |
| MCP 连接 | `backend/app/capabilities/mcp_client.py` |
| **agent 组装** | `backend/app/agents/builder.py` + `backend/app/agents/runner.py` |
| agent 业务入口 | `backend/app/services/agent.py` |
| 统一清单对外服务 / 工具 / 技能 / MCP 业务 | `backend/app/services/{capability,tool,skill,mcp_server}.py` |
| 审批服务 | `backend/app/services/invocation.py` |
| 数据模型 | `backend/app/models/{tool,skill,skill_tool,mcp_server,invocation}.py` |
| 接口层 | `backend/app/api/v1/{capability,tool,skill,mcp_server,invocation}.py` |
| 前端页面 / 表单 / 风险显示 | `frontend/src/pages/CapabilitiesPage.tsx`、`frontend/src/features/capabilities/{ToolForm,SkillForm,MCPForm}.tsx`、`risk.ts` |

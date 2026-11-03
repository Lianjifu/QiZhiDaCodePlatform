# 能力模块：能力与实现

> 状态：**已实现**（2026-11-01，deepAgents 迁移后）
>
> 关联文档：[rag-architecture.md](rag-architecture.md)（RAG 服务层）、
> [plans/capability-followups.md](../plans/capability-followups.md)（能力层待办与已知限制）、
> [plans/agent-migration-deepagents.md](../plans/agent-migration-deepagents.md)（deepAgents 迁移方案与决策记录）、
> [memory-module.md](memory-module.md)（记忆模块计划）

这份文档梳理**能力模块**已经做完的能力，以及它是怎么实现的。
未实现的部分、已知限制与排期，都在 `plans/` 下，不在这里重复。

---

## 1. 能力模块是什么

**能力模块 = 平台所有"能被调用、能被治理"的东西的统一体系。**

一句话概括它的目标：**让每一次能力调用都经过同一条可声明、可校验、可审计、可拦截的流水线。**

早先能力层的形态是"能跑"但不是"可治理"：能力靠描述文字注册，执行靠一个
`try/except` 包住，谁调了、调了什么、为什么失败，只能查到一半；更糟的是
**agent 内部的工具调用完全绕过了执行层**，于是没有日志、没有超时、没有重试、
没有策略检查。

能力模块把这件事补上，方法是**三个动词**——正好是同一条流水线上的三步：

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

### 与 deepAgents 的分工

2026-11-01 之后，**agent 底座换成了 deepAgents**。分工是：

| 谁 | 提供什么 | 我们怎么接 |
| --- | --- | --- |
| deepAgents | 文件工具（`ls` / `read_file` / `write_file` / `edit_file` / `delete` / `glob` / `grep` / `execute`）、规划（`write_todos`）、子代理（`task`）、技能（`SKILL.md`）、记忆（`AGENTS.md`）、摘要、prompt caching | 换成我们的 backend（文件本地、命令进沙箱） |
| 我们 | 能力清单与声明、治理中间件、执行流水线、联网工具、MCP 工具、`run_python`、技能知识、调用记录 | 作为 deepAgents 的 `tools` / `middleware` / `subagents` 接进去 |

**我们不再自己实现文件工具** —— 路径安全、读写语义、结果校验都归 deepAgents 的
`FilesystemBackend`（虚拟模式：阻断 `..`，绝对路径映射进工作区）。我们只保留
它没有的那部分。

### 清单里有三类成员

| 成员 | 是什么 | 能不能被模型直接调 |
| --- | --- | --- |
| **tool** | 原子能力，最小可执行单元 | ✅ 有 input/output 契约 |
| **mcp** | 供给通道，一个外部 server 提供的一组能力 | ❌ 它是通道，通道上的工具才是能力 |
| **skill** | 能力组合：原子能力 + "怎么用它们"的知识 | ❌ 本身不执行，导出成 `SKILL.md` 交给 deepAgents 渐进披露 |

三者共用同一个 `CapabilitySpec` 结构，字段按 `kind` 取用。

### 边界的核心约定

**模型提议，意图层收敛。**

模型仍然决定"下一步调哪个能力"——它看得见对话历史，这是它不可替代的优势。
意图层做的是把模型这次 tool call **翻译并约束**成一个结构化意图。意图层的
产出是**一次调用**，不是多步计划（多步计划由模型的 ReAct 循环产生）。

---

## 2. 模块提供的能力

### 2.1 统一能力清单（对外出口）

编排层和前端都从这一份清单拿能力，不再各自去拼 tool / mcp / skill 三张表。

| 能力 | 接口 | 说明 |
| --- | --- | --- |
| 列出全部成员 | `GET /api/v1/capabilities` | 按 `kind` / `tag` / `enabled` 过滤 |
| 取任意成员 | `GET /api/v1/capabilities/{key}` | 跨三类成员按 key 取 |
| 取完整声明 | `GET /api/v1/capabilities/{key}/spec` | 契约 + 策略（schema / 风险 / 权限 / 超时 / 重试 / 注解 / 信任模式） |
| 直接调一次 | `POST /api/v1/capabilities/{key}/invoke` | 调试用；**走的是同一条执行流水线**，不是另一条捷径 |

**数据库是唯一事实来源**。代码里的出厂清单只是"出厂源"，负责首次安装 + 补空字段，
运营改过的描述、风险等级、权限要求不会被启动流程冲掉。

### 2.2 治理能力

| 能力 | 落在哪 | 说明 |
| --- | --- | --- |
| 声明式契约 | 注册层 | 输入输出 schema、风险、权限、超时、重试、幂等，全是声明字段 |
| 工具策略映射表 | 注册层 | `tool_policy.py`：deepAgents 的文件工具没有声明，策略用一张表补上 |
| 七阶段流水线 | 执行层 | 我们自己的工具（联网 / `run_python` / MCP）的必经之路（见 §5.4） |
| 中间件治理 | 意图层 | **所有**工具调用（含 deepAgents 的文件工具）都经过它：工具收敛、参数级风险挂起、留痕 |
| 失败分层 | 执行层 + 错误类型 | 不同失败类型给模型不同指令（见 §5.5） |
| 任务置信度 | 执行层 | 从调用记录算出来，低于阈值就阻塞转人工 |
| 超时分级 | 执行层 | 每个能力声明自己的 `timeout_s`，执行层再套一层兜底 |
| 幂等闸门 | 执行层 | 只对幂等能力自动重试 |
| 指数退避 + 熔断 | 执行层 | 滑动窗口统计错误率与延迟，坏了就快速失败 |
| 人工审批 | 中间件 + LangGraph 中断 | 挂起等人批，批准后**恢复图**，agent 自己接着跑 |
| 留痕 | 执行层 + 中间件 | 每次调用写一条 `invocation` |

### 2.3 三种来源怎么执行

| 类型 | source | 声明方式 |
| --- | --- | --- |
| `builtin` | 白名单里的 `模块:工厂函数` | 出厂源登记 |
| `http` | 留空 | `config` 里写 method/url/headers/body 模板；**parameters 必填** |
| `mcp` | MCP 服务器名 | `config.tool_name`；注解从 MCP 侧继承（`Annotations.from_mcp`） |

http 类型的 body 模板里可以用 `{占位符}` 引用模型给的参数；GET 类请求的参数进
query string，其余进 JSON body。

---

## 3. 声明：一份模板长什么样

模板 = `CapabilitySpec`（运行时读的统一结构）+ `BuiltinSpec`（出厂声明）。
字段分三组：

### 3.1 身份

| 字段 | 取值 | 说明 |
| --- | --- | --- |
| `key` | `web_fetch` … | 模型看到的工具名，必须是合法标识符 |
| `display_name` | 抓取网页 | 给人看的名字 |
| `description` | 自由文本 | **给模型看的**，检索和选型都靠它 |
| `tags` | `("网络", "抓取")` | 检索用的领域标签，命中标签权重更高 |
| `kind` | tool / mcp / skill | 清单里三类成员共用这一个结构 |
| `origin` | builtin / user | 出厂内置 or 用户自建 |
| `enabled` | bool | 停用的能力不进可用集合 |

### 3.2 契约

| 字段 | 说明 |
| --- | --- |
| `input_schema` | JSON Schema。**声明了 properties 就默认拒绝额外字段** |
| `output_schema` | JSON Schema。执行层据此校验返回值（校验时忽略多余字段） |
| `source_type` | builtin / http / mcp —— 决定用哪个适配器执行 |
| `locator` | 执行入口：builtin 是 `模块:工厂函数`，http 是 method/url/headers/body 模板，mcp 是 server + tool_name |

### 3.3 策略（治理）

| 字段 | 取值 | 说明 |
| --- | --- | --- |
| `risk` | read / write / execute / network / destructive | 这能力**最坏**能造成什么 |
| `permission` | none / confirm / admin | 谁来放行这次调用 |
| `timeout_s` | 秒 | 单次调用上限 |
| `retry` | `RetryPolicy` | 次数 / 退避 / 可重试错误码白名单 / 是否要求幂等 |
| `annotations` | read_only / destructive / idempotent / open_world / egress_target / egress_data | 工具自述，词汇对齐 MCP tool annotations |

### 3.4 deepAgents 的文件工具没有声明 —— 用一张表补上

文件工具由 deepAgents 注册，我们手里没有 `CapabilitySpec`。它们的治理策略放在
`registration/tool_policy.py` 的映射表里，是三样东西的**单一来源**：

- `interrupt_on` 集合（哪些工具要人批）；
- 风险基线（写进调用记录，参数级规则只能上调它）；
- 幂等标记（决定能不能自动重试）。

| 工具 | 风险 | 档位 | 权限 | 幂等 | 审批 |
| --- | --- | --- | --- | --- | --- |
| `ls` / `read_file` / `glob` / `grep` | read | 低 | none | ✓ | 自动放行 |
| `write_file` / `edit_file` | write | 低 | none | ✓ | 自动放行 |
| `delete` | destructive | 高 | none | ✗ | **永远**要人批 |
| `execute` | execute | 中 | none | ✗ | balanced / strict 要人批 |

`delete` 是高档：写文件能靠再写一遍补救，**删掉的回不来** —— 信任模式再宽松
也要人批。

我们自己的工具（`web_fetch` / `web_search` / `run_python` / MCP）走同一套判定 ——
它们的 `permission` / `risk` 来自声明。两边合起来才是完整的工具集，判定函数
只有一个（`policy.needs_approval`）。

### 3.5 风险档位与权限是两个正交维度

- `risk` —— 这能力**最坏**能造成什么（描述能力本身）；
- `permission` —— **谁来放行**这次调用（描述运行时策略）。

**审批按风险档位判**（用户定的口径：只有中高档才需要审批）：

| 档位 | 风险等级 | 要不要人批 |
| --- | --- | --- |
| 低 | read / write | 自动放行 —— 改错了能改回来 |
| 中 | execute / network | 要人批 —— 会在进程外产生副作用 |
| 高 | destructive | **永远**要人批 —— 不可逆 |

`permission` 是档位之上的两个口子：`confirm` = 运营额外要求人确认，
`admin` = 管理员放行（信任模式放宽不了）。出厂能力的 `permission` 一律 `none`
—— 档位说了算，声明不重复表达一遍。

信任模式只决定**中档**放不放心行：

| 模式 | 放行范围 | 用途 |
| --- | --- | --- |
| `strict` | 中高档要人批；标了 `confirm` 的低档能力也要人批 | 生产 |
| `balanced`（默认） | 中高档要人批 | 默认 |
| `permissive` | 只有高档要人批（中档也放行） | 本地开发 |

信任模式只能**放宽**中档，放宽不了高档这种"不可逆"的操作。

风险还会被**参数级规则上调**（规则是纯函数，便于单测、便于向模型解释"这次
为什么要审批"）：

| 触发 | 上调到 | 是威胁信号吗 |
| --- | --- | --- |
| 命令命中 `rm -rf` / `dd if=` / `mkfs` / `curl … \| sh` / 写裸设备 | `destructive` + 管理员放行 | ✅ |
| `web_fetch` 的 URL 指向内网地址 | `destructive` + 管理员放行（SSRF 尝试） | ✅ |
| 注解说开放世界，且**出口由模型决定** | `network` | ✅（SSRF 面） |
| 注解说会破坏性改动 | `write` | ✅ |
| 注解说开放世界，但**出口是平台配好的**且有数据外发 | `write` | ❌ 只是分类调整 |

**最后一行是"分类调整"，不是威胁信号** —— 平台自配的向量化端点、数据出内网，
都是常态。参数级的挂起只由威胁信号触发：声明级已经放过一次了，再挂一次等于
同一次调用让人批两遍。不分开就会踩一个很实际的坑 —— 记忆检索会被评估成 write，
于是**每查一次记忆都要人工审批**。

### 3.6 出口维度：会不会出进程，与出口是不是模型说了算

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

## 4. 已交付的工具

### 4.1 deepAgents 提供的文件工具（8 个）

| key | 做什么 | 关键约束 |
| --- | --- | --- |
| `ls` | 列目录下的文件与子目录 | 路径相对工作区根 |
| `read_file` | 读文本文件（带行号） | `offset`/`limit` 分段读大文件 |
| `write_file` | 写入（覆盖），自动建父目录 | 写类，strict 下要人批 |
| `edit_file` | 精确字符串替换 | **原文必须唯一**，不唯一就报错 |
| `delete` | 删除文件 / 目录 | **永远要人批**，不可重试 |
| `glob` | 按模式找文件，如 `**/*.py` | 返回相对工作区根的路径列表 |
| `grep` | 按正则搜内容 | 返回 `文件:行号:内容` |
| `execute` | 执行 shell 命令 | 进沙箱；命令写进脚本文件再执行，不做 `sh -c` 拼接 |

**路径安全归 deepAgents**：`FilesystemBackend(root_dir=工作区, virtual_mode=True)`
阻断 `..`，把绝对路径映射进工作区（`/etc/passwd` → `<工作区>/etc/passwd`），
要求 resolve 后仍在 root 内。

**"当前目录"这个概念整体消失**：所有路径都相对工作区根目录。会话级 `cd`
没有了 —— `mv` / `cp` / `mkdir -p` 靠 `execute` 跑（`cd` 在命令里）。

### 4.2 我们自己的内置能力（3 个）

| key | 名称 | 参数 | 风险 | 档位 | 权限 | 超时 | 重试 | 幂等 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `run_python` | 执行 Python | `code` | execute | 中 | none | 60s | TIMEOUT×2 | ✗ |
| `web_fetch` | 抓取网页 | `url` | network | 中 | none | 20s | NETWORK_ERROR×3 | ✓ |
| `web_search` | 联网搜索 | `query` `max_results?` | network | 中 | none | 20s | NETWORK_ERROR×3 | ✓ |

| 能力 | 做什么 | 约束 |
| --- | --- | --- |
| `run_python` | 在沙箱里跑一段 Python | 返回退出码 / 输出 / 耗时；**一律进沙箱** |
| `web_fetch` | 抓 http/https 页面并转纯文本（去脚本、去样式） | URL 指向内网地址 → 判为 SSRF，顶到 `destructive` |
| `web_search` | 走 Tavily / Serper / Bing 搜索 | 没配 Key 就明说"请设置 `WEB_SEARCH_PROVIDER` 和 `WEB_SEARCH_API_KEY`"，不静默失败 |

`run_python` 保留是因为 deepAgents 没有等价物；`run_bash` **删掉** —— deepAgents
的 `execute` 就是它（命令进同一个沙箱），两个功能重复的工具留着只会让模型选错。

> ⚠️ `run_python` 虽然声明了「超时重试」，但因为**不幂等**，实际**不会自动重试**：
> 超时只说明"不知道执行完没有"，重跑可能把副作用做两遍。执行层会把这类超时
> 降级成不可重试，并在错误信息里写明"不要重复执行同样的命令"。

### 4.3 沙箱

| 模式 | 后端 | 隔离 |
| --- | --- | --- |
| `docker`（默认） | 我们的 `DockerSandbox`（`BaseSandbox` 子类） | `--network none --cpus 1 --memory 512m --pids-limit 256 --cap-drop ALL --security-opt no-new-privileges`，只挂载当前会话的工作区 |
| `local`（开发） | deepAgents 的 `LocalShellBackend` | **没有任何隔离**，仅限本地开发 |

默认是 `docker`：平台执行的是 LLM 生成的代码，隔离是安全底线而不是可选项。
`local` 要**显式**打开。

这条规则**一处**说了算 —— `execute`（deepAgents 的命令工具）与 `run_python`
（我们的执行能力）都看 `SANDBOX_TYPE`。两边各写一套，迟早跑偏：曾经
`execute` 恒定走 docker、`run_python` 看配置，看起来是"更安全"，实际是没人
说得清哪个工具跑在哪。

两条硬约束：

1. **命令写进脚本文件再执行**，不做 `sh -c "<用户输入>"` 拼接。
2. **挂载预检**。macOS 上 Docker Desktop / Colima 只共享部分路径，没共享时
   docker **不报错**，而是挂一个空目录进去，容器里的命令就在空工作区里跑，
   报错还特别误导。`DockerSandbox._ensure_mounted()` 先放标记文件、进容器确认
   看得见，看不见就明确报"工作区没有挂载进容器"并给出解决办法。

> 本机（macOS + Colima）要跑沙箱，`WORKSPACE_ROOT` 必须放在 `$HOME` 下。

---

## 5. 实现

### 5.1 落点总览

| 层 | 目录 | 负责 |
| --- | --- | --- |
| 注册层 | `app/capabilities/registration/` | 声明、编目、安装 —— 产出**统一能力清单**；文件工具的策略映射表 |
| 意图层 | `app/capabilities/intent/` | 治理中间件（工具收敛 / 参数级风险 / 留痕）+ 一次 tool call → 一条意图 |
| 执行层 | `app/capabilities/execution/` | 七阶段流水线：校验 → 授权 → 风险 → 审批 → 调用 → 验证 → 留痕 |
| 后端 | `app/capabilities/backends/` | deepAgents 的 backend：文件本地、命令进沙箱 |

### 5.2 注册：出厂源 → 数据库 → 统一清单

出厂源在 `registration/builtin/__init__.py`，现在只剩三条：

```python
BuiltinSpec(
    key="run_python",
    source="app.capabilities.registration.builtin.sandbox:make_run_python",
    display_name="执行 Python",
    description="在工作区的沙箱里执行一段 Python 代码，返回退出码和输出。",
    tags=("代码", "执行"),
    permission=PermissionLevel.CONFIRM,
    risk=RiskLevel.EXECUTE,
    timeout_s=60,
    retry=_RETRY_TIMEOUT,
    annotations=_EXEC,
    input_schema=_obj({"code": {"type": "string", ...}}, ["code"]),
    output_schema=_obj({...}, ["content", "exit_code"]),
)
```

启动时 `registry.py::sync_builtins()` 把它同步进 `tool` 表（`origin=builtin`），
**只补空字段** —— 运营改过的内容不会被启动流程冲掉。

**一条例外：契约以代码为准。** 入参 / 出参 schema **无条件**用出厂声明覆盖 ——
内置能力的实现是代码里写死的函数签名，契约跟着实现走。数据库里那份要是能
漂移，模型看到的就是旧契约，调用会被参数校验直接拦下。策略字段（风险等级、
权限要求、超时、重试、启用状态、描述）仍然听数据库的 —— 那些本来就该由
运营调。

读清单时（`_tool_spec()`）同样优先用代码里的契约。

同一次同步还会**下线被 deepAgents 接管的出厂能力**（`_retire_superseded()`）：
`read_file` / `write_file` / `edit_file` / `glob` / `grep` / `list_files` /
`move_file` / `copy_file` / `make_dir` / `change_directory` / `run_bash` ——
停用并清空执行入口。它们的历史调用记录留着，审计时还能查。

> ⚠️ `source` 只能引用白名单里登记过的 `模块:工厂函数`。允许任意 `模块:函数`
> 等于允许任何能调工具管理接口的人让平台 import 并执行任意 Python 代码 ——
> 所以越界的 source 在 schema 校验层直接 422。

注册层把三张表聚合成一份清单：`tool` → 原子能力，`mcp_server` → 供给通道，
`skill` → 能力组合。

### 5.3 backend 组装：文件本地 + 命令进沙箱

`backends/assembly.py::build_backend()` 组出 deepAgents 的 `CompositeBackend`：

```text
CompositeBackend(
    default = DockerSandbox(工作区)            # execute 永远走 default
    routes = {
        "/":        FilesystemBackend(工作区),   # 文件操作：本地
        "/skills/": FilesystemBackend(技能目录), # 技能：工作区外面
    }
)
```

`CompositeBackend` 按**路径前缀**路由文件操作，而**命令执行不可按路径路由，
固定走 default backend** —— 正好对上我们要的组合。

一个必须成立的一致性：容器只挂载会话工作区，所以容器里写的文件落在同一个
工作区目录，`execute` 造出的文件随后能被文件工具读到。

### 5.4 执行：七阶段流水线

`execute()` 在 `execution/pipeline.py`，负责**我们自己的工具**（联网 /
`run_python` / MCP）。顺序是刻意的 —— 便宜的确定性检查在前，昂贵的人介入在中，
副作用在后：

```text
⓪ 任务状态    load_task_state()     execution/task_state.py
               置信度 / 重试阈值 / 是否已阻塞
① schema 校验 validate_arguments()  registration/validator.py   ← 权威校验
② 权限校验    _check_permission()   pipeline.py
               停用 / 不在允许集 → PERMISSION_DENIED
③ 风险评估    assess()              execution/risk.py
               声明的 risk + 参数级规则（破坏性命令 / SSRF）
④ 审批等待    _decide_approval()    pipeline.py
               按风险档位 + permission + 信任模式 → approved / pending / denied
⑤ 实际调用    _invoke()             pipeline.py
               幂等去重 → 超时 → 重试 → 适配器
⑥ 结果验证    _verify_output()      pipeline.py
               契约校验：output_schema
⑦ 结果回写    _finish()             pipeline.py
               写 invocation 表（脱敏参数 + 各阶段结论 + 置信度）
```

第 ⑤ 步的内部顺序：

```text
幂等命中？──是→ 复用上次结果，直接返回（不再执行副作用）
   │否
   ▼
熔断打开？──是→ 快速失败（CIRCUIT_OPEN），别把时间耗在坏掉的能力上
   │否
   ▼
for attempt in 1..max_attempts:
    适配器执行（BuiltinAdapter / HttpAdapter / McpAdapter）
    ├─ 成功 → 记熔断样本 → 返回
    └─ 失败 → 记熔断样本 → 判断能不能重试 → 指数退避后重试
```

**能不能重试，四个条件都要满足**：

1. 还有次数；
2. 运行时错误标了 `retryable`（声明允许 ≠ 运行时值得）；
3. 错误码在 `retry_on` 白名单里；
4. **能力是幂等的**（`require_idempotent=true` 时）—— 不幂等的操作重试可能把
   副作用做两遍。

退避是指数的：`base × 2^(n-1)`，封顶 `max_backoff_s`（默认 30s）。

**效果确认没了**：文件类能力由 deepAgents 提供，它的 `WriteResult` / `ReadResult`
自带错误字段，"写没写成功"由它自己回答。

### 5.5 治理落在中间件层

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

两处中断的载荷形状不同，但恢复值统一成 deepAgents HITL 的格式：

```json
{"decisions": [{"type": "approve"}]}
```

**"要不要人批"的判定只有一份**（`registration/policy.py::needs_approval`），
三个地方都用它：deepAgents 的 `interrupt_on`、中间件告诉模型的那句话、执行层
在直接调用路径上的判定。三处各写一套，批准过了又被拦一次就是这么来的。

执行层在 agent 路径上**不再问一遍**：审批已经由 HITL 负责，工具能跑到执行层就
说明人已经批过了（`ExecutionContext.approval_handled`）。直接调 API 的那条路径
不走图，仍然由执行层自己决定要不要挂起。

### 5.6 审批：挂起 → 批准 → 图恢复

```text
POST /api/v1/agent/chat
   │
   ▼ 模型调工具 → 命中 interrupt_on
   │
   ▼ LangGraph 挂起（checkpoint 里）
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
才回流给它（"结果回流"机制）。现在图恢复，agent 接着跑，结果直接回到它的上下文。

所以那套回流机制（`intent/notify.py`）连同 `invocation.notified_at` 一起删掉了 ——
留着就成了同一份结果送两遍。

审批状态存在 **LangGraph 的 checkpoint 里**，不在调用记录表里 —— 图挂在哪儿只有
它知道。查待审批列表用 `GET /api/v1/agent/{thread_id}/pending`。

> 直接调 API 的那条路径（`POST /api/v1/capabilities/{key}/invoke`）不走图，
> 仍然是"生成 pending 记录 → `POST /api/v1/invocations/{id}/approve` 执行"。
> 两条路径各有各的入口，但都落到同一套执行流水线。

### 5.6.1 模型提前知道哪些工具会要人批

审批规则抽成了单一事实来源（`tool_policy.py::approval_tool_names()` +
`policy.py::approval_decision()`），中间件拿它做判定，同时**提前告诉模型**：

```text
当前信任模式：balanced。
审批按风险档位：只读、写入是低档，直接执行；执行命令、联网是中档，需要人工
审批；破坏性操作是高档，永远需要人工审批。
以下工具需要人工批准才能执行：delete、execute。
每次触发审批都会中断当前任务等人工处理，调用前请把参数一次做对。
另外：平时不用人批的工具，碰到命令里含删除/写设备等特征、或抓取的是内网地址，
也会命中参数级风险规则，单独问一次。
```

判定只有一份（`policy.needs_approval`），三处都用它 —— 模型被告知"直接放行"、
实际却被拦下，或者批准过了又被拦一次，都是因为各写了一套规则。

### 5.6.2 执行摘要回到调用方

agent 响应里的 `tool_calls` 带执行摘要：

```json
{
  "name": "read_file",
  "args": {"file_path": "a.txt"},
  "invocation_id": 42,
  "ok": true,
  "status": "succeeded",
  "error_code": null,
  "duration_ms": 12,
  "blocked": false
}
```

摘要**从调用记录来**，不从工具消息来 —— deepAgents 的工具消息没有我们那套
artifact 结构，而调用记录是唯一事实来源（执行层与治理中间件都往里写）。

### 5.7 失败分层：不同类型给模型不同指令

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

### 5.8 留痕

每次调用写一条 `invocation`（`models/invocation.py`）：脱敏后的参数、原始
payload、各阶段结论、耗时、任务置信度。

**文件工具的记录由治理中间件写**（它们不走执行层），字段少一些：没有分阶段结论，
只有结果摘要与置信度。

```text
GET    /api/v1/invocations               调用记录分页
GET    /api/v1/invocations/{id}          调用详情（含各阶段结论）
POST   /api/v1/invocations/{id}/approve  批准并执行待审批的调用（直接调用路径）
POST   /api/v1/invocations/{id}/deny     拒绝
POST   /api/v1/invocations/{id}/cancel   取消
```

### 5.9 技能：数据库 → SKILL.md → 渐进披露

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
   │  （frontmatter 写 name / description，正文写知识）
   │  目录名必须合法：小写 ASCII 字母 / 数字 / 单个连字符
   │  中文名转不出来 → 退化成哈希名，中文名写进 description（模型照样认得）
   ▼ create_deep_agent(skills=["/skills/"])
   │  backend 把 "/skills/" 路由到技能目录
   ▼ 渐进披露：
   │  提示词里只有技能的名字和描述
   │  模型觉得相关了才去读正文
   ▼
模型产出 tool call → 治理中间件 → 执行
```

**技能知识不进系统提示词** —— 提前全塞进去就把"按需加载"退化成"每次都全量加载"。

**当前没有出厂内置技能** —— `builtin=1` 这个标记和"内置技能不允许删除"的保护
都在，但没有任何种子数据，技能全部靠用户/运营通过接口创建。

> **行为变化**：技能**不再收敛工具集**。以前命中一个技能会把暴露给模型的工具
> 裁到"技能成员 + 出厂内置能力"；现在 deepAgents 的技能是**知识**的渐进披露，
> 不是工具的子集 —— `tool_ids` 只作为"这个技能适用于哪些工具"的说明与预览信息，
> 不再影响 agent 能调什么。

技能管理接口：

```text
GET    /api/v1/skills                   分页查询（enabled 过滤）
POST   /api/v1/skills                   新增（tool_ids 里的工具必须存在）
GET    /api/v1/skills/{id}              详情（含 tool_ids / tool_names）
PUT    /api/v1/skills/{id}              更新（tool_ids 整体替换）
DELETE /api/v1/skills/{id}              删除（内置技能 builtin=1 拒绝，只能停用）
GET    /api/v1/skills/{id}/preview      预览挂载效果：会带哪些能力、往提示词里加什么
```

### 5.10 记忆：AGENTS.md

工作区里的 `AGENTS.md` 是会话的**长期记忆**：内容由 agent 自己维护，改完下一轮
就生效。文件不存在时由 `builder.py::_ensure_memory_files()` 建一个空壳。

`create_deep_agent(memory=["/AGENTS.md"])` 把内容注入每轮的系统提示词。

> 真正的记忆模块（短期 / 长期两层）还没做，见
> [memory-module.md](memory-module.md)。现在只有一个"agent 能自己
> 改的文件"。

### 5.11 子代理

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

`tests/test_subagents.py` 守的就是这件事：把中间件从子代理规格里去掉，
子代理的工具调用就一条记录都不留。

### 5.12 agent 侧怎么用能力

一次对话的完整链路：

```text
POST /api/v1/agent/chat  { message, thread_id, skill_ids?, provider_id? }
   │
   ▼ services/agent.py::AgentService.chat
   ① 分配工作区     resolve_workspace(thread_id) → WORKSPACE_ROOT/<thread_id>
   ② 选模型         _resolve_model(provider_id) → 默认供应商，没配置就退回 .env
   ③ 加载能力清单   load_capabilities() → CapabilityRegistry.load()
   ④ 建执行上下文   _build_context() → 工作区 + 本次用到的 MCP 供给通道 + DB 会话
   │
   ▼ agents/runner.py::_build
   ⑤ 导出技能       export_skills(specs, 技能目录, skill_ids)
   ⑥ 组装提示词     assemble_prompt(CODE_AGENT_PROMPT, runtime_notes)
   │
   ▼ agents/builder.py::build_agent
   ⑦ 能力 → 模型可调工具   build_tools(specs, context, skip_names=FS_TOOL_NAMES)
   ⑧ 装 middleware         CapabilityMiddleware + TodoListMiddleware
   ⑨ 组装子代理            显式带同一套治理中间件
   │
   ▼ create_deep_agent(model, tools, system_prompt, checkpointer, middleware,
                       backend, interrupt_on, skills, memory, subagents)
   │
   ▼ 模型循环：思考 → 调工具 → 结果回灌 → 再思考
   │
   ▼ CapabilityMiddleware.awrap_tool_call
      参数级风险 → 执行 → 留痕
```

四个关键点：

**① 文件工具不走我们的执行层**。它们由 deepAgents 直接执行，我们只在中间件里
补一条调用记录。**我们自己的工具**（联网 / `run_python` / MCP）仍然走执行层七阶段。

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
给模型看（LangChain 的 `args_schema`），和给平台校验（执行层 ① 阶段）。
用户自建的能力常常不填入参，这时从实现函数签名兜一份 —— 给模型一张空参数表
是很糟的，它不知道该传什么。

**④ 出错时给模型的是分层指令，不是裸 traceback**。见 §5.7。

---

## 6. 接口清单

```text
GET    /api/v1/capabilities             统一能力清单（kind/tag/enabled 过滤）
GET    /api/v1/capabilities/{key}        按 key 取任意成员
GET    /api/v1/capabilities/{key}/spec   能力的完整声明（契约 + 策略）
POST   /api/v1/capabilities/{key}/invoke  直接调一次能力（调试用，返回结构化结果）

GET    /api/v1/invocations               调用记录分页
GET    /api/v1/invocations/{id}          调用详情（含各阶段结论）
POST   /api/v1/invocations/{id}/approve  批准并执行待审批的调用
POST   /api/v1/invocations/{id}/deny     拒绝待审批的调用
POST   /api/v1/invocations/{id}/cancel   取消待审批的调用

GET    /api/v1/skills                   技能列表
POST   /api/v1/skills                   新增技能
GET    /api/v1/skills/{id}              技能详情
PUT    /api/v1/skills/{id}              更新技能
DELETE /api/v1/skills/{id}              删除技能
GET    /api/v1/skills/{id}/preview      预览挂载效果

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

POST   /api/v1/agent/chat               对话一轮（挂起时 status=pending_approval）
POST   /api/v1/agent/stream             SSE 流式
GET    /api/v1/agent/{thread_id}/pending 待审批的调用
POST   /api/v1/agent/{thread_id}/approve 批准挂起的调用，恢复图
POST   /api/v1/agent/{thread_id}/reject  拒绝挂起的调用，恢复图
```

对话时用 `skill_ids` 指定要挂载的技能；不传则导出全部启用的技能，由模型按
描述自己挑。

```json
POST /api/v1/agent/chat
{ "message": "帮我审查这段代码", "skill_ids": [1], "provider_id": 2 }
```

响应里的 `tool_calls` 带执行摘要（成功 / 失败 + 调用记录 ID + 耗时），
详见 §5.6.2。

---

## 7. 已知限制

这一节的条目全部记在 [plans/capability-followups.md](../plans/capability-followups.md)，
连同各自的现状、影响面和排期。摘要：

| 限制 | 计划 |
| --- | --- |
| 并发配额未生效 | [待办](../plans/capability-followups.md#b-并发配额未生效) |
| 审批无自动过期 | [待办](../plans/capability-followups.md#c-审批无自动过期) |
| MCP 注解继承没打通 | [待办](../plans/capability-followups.md#d-mcp-注解继承没打通) |
| 没有出厂内置技能 | [待办](../plans/capability-followups.md#e-没有出厂内置技能) |
| 外部工具的依赖环境 | [待办](../plans/capability-followups.md#f-外部工具的依赖环境) |
| 待审批记录存明文参数 | [待办](../plans/capability-followups.md#g-待审批记录存明文参数) |
| 能力接口无鉴权 | 用户已明确推迟，见[待办](../plans/capability-followups.md#h-能力接口无鉴权) |

> 「结果回流要等用户说下一句」这条**已经解决** —— 审批改成 LangGraph 中断，
> 批准后图恢复，agent 自己接着跑。见 §5.6。

---

## 8. 文件速查

| 关注点 | 文件 |
| --- | --- |
| 模板声明（出厂源） | [app/capabilities/registration/builtin/__init__.py](../../app/capabilities/registration/builtin/__init__.py) |
| 声明字段定义 | [app/capabilities/registration/spec.py](../../app/capabilities/registration/spec.py) |
| 风险 / 权限 / 重试 / 注解 | [app/capabilities/registration/policy.py](../../app/capabilities/registration/policy.py) |
| **文件工具的策略映射表** | [app/capabilities/registration/tool_policy.py](../../app/capabilities/registration/tool_policy.py) |
| 出厂源 → 数据库同步（含下线） | [app/capabilities/registration/registry.py](../../app/capabilities/registration/registry.py) |
| schema 校验 | [app/capabilities/registration/validator.py](../../app/capabilities/registration/validator.py) |
| 执行类能力实现 | [app/capabilities/registration/builtin/sandbox.py](../../app/capabilities/registration/builtin/sandbox.py) |
| 网络类能力实现 | [app/capabilities/registration/builtin/web.py](../../app/capabilities/registration/builtin/web.py) |
| **backend 组装** | [app/capabilities/backends/assembly.py](../../app/capabilities/backends/assembly.py) |
| **docker 沙箱后端** | [app/capabilities/backends/sandbox_backend.py](../../app/capabilities/backends/sandbox_backend.py) |
| **技能导出** | [app/capabilities/skills_export.py](../../app/capabilities/skills_export.py) |
| 意图生成 | [app/capabilities/intent/resolver.py](../../app/capabilities/intent/resolver.py) |
| **治理中间件** | [app/capabilities/intent/middleware.py](../../app/capabilities/intent/middleware.py) |
| 执行流水线 | [app/capabilities/execution/pipeline.py](../../app/capabilities/execution/pipeline.py) |
| 参数级风险规则 | [app/capabilities/execution/risk.py](../../app/capabilities/execution/risk.py) |
| 执行适配器 | [app/capabilities/execution/adapters.py](../../app/capabilities/execution/adapters.py) |
| 能力 → 模型可调工具 | [app/capabilities/execution/tooling.py](../../app/capabilities/execution/tooling.py) |
| 失败分层与置信度 | [app/capabilities/execution/task_state.py](../../app/capabilities/execution/task_state.py) |
| 熔断 | [app/capabilities/execution/breaker.py](../../app/capabilities/execution/breaker.py) |
| 提示词组装 | [app/capabilities/prompt.py](../../app/capabilities/prompt.py) |
| 错误码 | [app/capabilities/errors.py](../../app/capabilities/errors.py) |
| **agent 组装** | [app/agents/builder.py](../../app/agents/builder.py) + [app/agents/runner.py](../../app/agents/runner.py) |
| agent 业务入口 | [app/services/agent.py](../../app/services/agent.py) |
| 统一清单对外服务 | [app/services/capability.py](../../app/services/capability.py) |
| 审批服务 | [app/services/invocation.py](../../app/services/invocation.py) |
| 技能业务 | [app/services/skill.py](../../app/services/skill.py) |
| 技能数据模型 | [app/models/skill.py](../../app/models/skill.py) + [app/models/skill_tool.py](../../app/models/skill_tool.py) |
| 调用记录 | [app/models/invocation.py](../../app/models/invocation.py) |

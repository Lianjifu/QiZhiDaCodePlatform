# Agent 层迁移到 deepAgents：deepAgents 为主

> 状态：**方案重做，待实施**（2026-11-01）
>
> 方向（2026-11-01 修订）：**以 deepAgents 的实现为主，我们自己写的实现能删就删。**
> 判据只有一条：**deepAgents 有对应物 → 删我们的；deepAgents 没有 → 留我们的。**
>
> 关联文档：[implementation/capability-module.md](../implementation/capability-module.md)（当前能力模块的实现，迁移后会被大幅裁剪）、
> [memory-module.md](../implementation/memory-module.md)、[capability-followups.md](capability-followups.md)

---

## 1. 修订后的判据与结论

上一版方案是"deepAgents 做外壳，能力层做 backend" —— 能力层基本原样保留。
这一版反过来：**能力层里凡是 deepAgents 已经做过的部分，直接删掉。**

### 1.1 deepAgents 已经做掉的（→ 删我们的）

| deepAgents 提供 | 我们的对应实现 | 行数 | 删掉后谁接手 |
| --- | --- | --- | --- |
| 文件工具 `ls`/`read_file`/`write_file`/`edit_file`/`glob`/`grep` | `registration/builtin/fs.py` | 439 | deepAgents 的 fs 工具（走我们的 sandbox backend） |
| 路径安全（backend 的 root 隔离） | `registration/builtin/paths.py` | 42 | 容器边界（只挂载工作区） |
| 效果验证（fs 操作自带） | `registration/builtin/verify.py` | 132 | deepAgents 自己校验返回值 |
| 技能系统（SKILL.md + 渐进披露） | `intent/planner.py` + `capabilities/prompt.py` | 177 | deepAgents 的 SkillsMiddleware |
| 提示词拼装（USER→BASE→SUFFIX） | `capabilities/prompt.py` | 49 | deepAgents |
| 审批挂起 | `intent/notify.py` + 执行层阶段④ | 122 + 部分 | deepAgents 的 HITL + `interrupt()` |
| 上下文摘要 / 消息淘汰 / prompt caching | — | 0 | deepAgents |
| 规划（write_todos） | — | 0 | langchain 的 TodoListMiddleware |
| 子代理（task） | — | 0 | deepAgents 的 SubAgentMiddleware |

### 1.2 deepAgents 没有的（→ 留我们的）

| 我们的东西 | 为什么 deepAgents 替代不了 | 位置 |
| --- | --- | --- |
| **Docker 沙箱隔离** | deepAgents 只有 `BaseSandbox`（抽象基类，要自己接服务商）和 `LocalShellBackend`（`subprocess.run(shell=True)`，**无隔离**）。我们的 `--network none --cap-drop ALL --memory 512m` 没有任何对应物 | `registration/builtin/sandbox.py` |
| **调用留痕（invocation 表）** | LangSmith tracing 是给开发看的，不是业务审计表 | `models/invocation.py` + `services/invocation.py` |
| **任务置信度 / 阻塞判定** | deepAgents 没有这个概念 | `execution/task_state.py` |
| **幂等 + 熔断** | deepAgents 没有 | `execution/breaker.py` + pipeline 里的幂等段 |
| **参数级风险规则**（`rm -rf`、SSRF） | deepAgents 的 permissions 只能按路径 allow/deny/interrupt | `execution/risk.py` |
| **MCP 供给通道** | deepAgents 不管 MCP；我们是 `langchain-mcp-adapters` 接的 | `app/api/v1/mcp_server.py` + `capabilities/mcp_client.py` |
| **联网工具**（web_fetch / web_search） | deepAgents 没有网络工具 | `registration/builtin/web.py` |
| **凭据脱敏** | deepAgents 没有 | `execution/redaction.py` |

### 1.3 规模

现状 7537 行（capabilities 4711 + agents/services/api 2826）。
预计**净删 2000 行左右，保留 + 重写约 2500 行**。

---

## 2. 新架构

```text
services/agent.py           业务入口（改：挂起态、审批恢复）
  └─ agents/runner.py       一轮对话（改：interrupt 检测与恢复）
       └─ agents/builder.py
            ├─ backends/composite.py         ★新：CompositeBackend 组装
            │     ├─ default = DockerSandbox（我们的 docker 沙箱，实现 BaseSandbox）→ execute 走这里
            │     └─ routes["/"] = FilesystemBackend(root_dir=工作区) → 文件操作走这里
            ├─ intent/middleware.py          ★瘦身：只留「工具收敛 + 参数级风险 + 留痕」
            ├─ TodoListMiddleware             规划
            ├─ SubAgentMiddleware             子代理（我们自带规格）
            ├─ SkillsMiddleware               技能（DB → SKILL.md 导出）
            ├─ MemoryMiddleware               记忆（AGENTS.md）
            ├─ HumanInTheLoopMiddleware       审批
            └─ deepagents.create_deep_agent(...)
```

**CompositeBackend 的组合方式**（deepAgents 原生支持）：

- 文件操作按**路径前缀**路由 → `FilesystemBackend(root_dir=工作区, virtual_mode=True)`：
  本地执行，快；`virtual_mode=True` 会阻断 `..`/`~`，并要求 resolve 后仍在 root 内 ——
  与我们现在的 `paths.py` 等价，所以 `paths.py` 可以删。
- **命令执行不可按路径路由**，固定走 default backend → 我们的 `DockerSandbox`：
  `docker run --network none --cap-drop ALL --memory 512m …`，隔离保持不变。

一个必须成立的一致性：**容器里写的文件要能被随后的文件工具读到**。
我们的沙箱只挂载会话工作区，所以容器内的写操作落在同一个工作区目录，
`execute` 造出的文件随后能被 `read_file` 读到。P0 要验这一条。

关键变化：**治理从"执行层流水线"上移到"中间件层"**。
deepAgents 的工具调用（含它的 fs 工具）都会经过中间件的 `awrap_tool_call`，
所以我们在那一层照样能看到每一次工具调用的名字和参数 ——
风险判定、审批挂起、留痕都在那里做，能力不需要"自己实现一遍"。

---

## 3. 逐项处置

### 3.1 删除

| 文件 | 行数 | 为什么能删 |
| --- | --- | --- |
| `registration/builtin/fs.py` | 439 | deepAgents 的 fs 工具覆盖同样的 10 个操作，且是它们的原生实现 |
| `registration/builtin/paths.py` | 42 | 路径边界改由容器保证（只挂载工作区，`--network none`） |
| `registration/builtin/verify.py` | 132 | fs 操作的结果由 deepAgents 自己校验（`ReadResult`/`WriteResult` 自带错误字段） |
| `intent/planner.py` | 128 | 技能检索退役 —— deepAgents 让模型自己按 description 挑技能 |
| `intent/resolver.py` | 98 | 没有"把 tool call 翻译成意图"这一步了 —— deepAgents 直接调工具 |
| `intent/notify.py` | 122 | 审批走 HITL，恢复后 agent 自己继续，不需要"下一轮注入通知" |
| `capabilities/prompt.py` | 49 | 提示词由 deepAgents 拼 |
| `registration/args_model.py` | 90 | pydantic 参数模型是我们自己的工具才需要；fs 工具自带 schema，我们自己的工具用声明直接生成 |
| `registration/validator.py` | 104 | 同样的理由：schema 校验由 deepAgents / 工具自身的契约承担 |

合计约 1200 行。

### 3.2 保留但重写

| 文件 | 现在 | 变成 |
| --- | --- | --- |
| `registration/builtin/sandbox.py` | 288 行，两个独立能力（run_python / run_bash） | `BaseSandbox` 子类：只实现 `execute()`（docker run）+ `upload_files`/`download_files`/`id`，**约 120 行** |
| `registration/builtin/web.py` | 198 行 | 保留，但降级为"我们的工具"（不是"能力"），走 langchain 工具形态 |
| `registration/builtin/__init__.py` | 493 行，14 个能力的出厂声明 | 只留 `web_fetch` / `web_search` / `run_python` 三条（**约 100 行**）。fs 类能力由 deepAgents 提供，不再由我们声明 |
| `registration/spec.py` | 97 行 | 瘦身：保留"我们的工具"（web_*）需要的字段；删掉给 fs 能力用的契约字段 |
| `registration/policy.py` | 232 行 | 保留风险/权限/重试/注解；`approval_decision` 改成"生成 `interrupt_on` 集合" |
| `registration/registry.py` | 211 行 | 从"统一能力清单"改成"**工具来源汇总**"：deepAgents fs 工具 + MCP 工具 + web 工具 |
| `intent/intent.py` | 97 行 | 瘦身：不再需要"意图"这个中间结构，只留留痕需要的最小信息 |
| `intent/middleware.py` | 228 行 | 重写：工具收敛、参数级风险 → `interrupt()`、每次调用写 `invocation`、置信度更新 |
| `execution/pipeline.py` | 510 行 | **大幅重写**：七阶段 → 单个"执行 + 留痕 + 验证"的钩子（**约 150 行**） |
| `execution/adapters.py` | 270 行 | 瘦身：删 builtin 适配器（fs 走 sandbox backend），留 http/mcp 适配器 |
| `execution/tooling.py` | 171 行 | 保留：把我们自己的工具（web_*、MCP）变成 langchain 工具 |
| `execution/context.py` | 142 行 | 保留但瘦身：工作区、沙箱配置、DB 会话；**删掉 cwd 机制**（`cwd`/`workdir`/`set_cwd`/`restore_cwd`/`_save_cwd` 与 `.qzd_state/` 状态文件，约 60 行） |
| `execution/result.py` | 101 行 | 保留：结果结构 |
| `execution/risk.py` | 113 行 | 保留：**参数级规则正好对着 deepAgents 的 `execute(command)` 与我们的 web `url`** |
| `execution/task_state.py` | 133 行 | 保留：置信度与阻塞 |
| `execution/breaker.py` | 134 行 | 保留：熔断 |
| `execution/redaction.py` | 58 行 | 保留：脱敏 |
| `errors.py` | 89 行 | 保留 |

### 3.3 彻底删掉的能力（行为变化，要认）

| 能力 | 之前 | 之后 |
| --- | --- | --- |
| `move_file` / `copy_file` / `make_dir` / `change_directory` | 有 | **删掉**（已定）。`mv`/`cp`/`mkdir -p` 靠 `execute` 跑；**`cd` 没有等价物** —— 当前目录固定为工作区根，会话级 cd 消失 |
| `run_python` | 有 | **保留**（已定）—— deepAgents 无等价物，且我们的实现把代码写成文件再跑，没有 shell 转义问题 |
| `delete` | 没有 | **保留并给模型**（已定）—— 写类操作，走审批（`interrupt_on={"delete": True}`）。需要在能力声明里补一条：risk=write、permission=confirm、不幂等 |
| `list_files` | 有（`list_files`） | 变成 deepAgents 的 `ls`（工具名变了） |

> **"当前目录"这个概念整体消失**：所有路径都相对工作区根。
> 连带要删的是 `ExecutionContext` 里的 cwd 机制 ——
> `cwd` / `workdir` / `set_cwd` / `restore_cwd` / `_save_cwd` 以及工作区外的
> `.qzd_state/<工作区名>.json` 状态文件（约 60 行）。
> 好消息：路径语义变简单了，"相对当前目录"这套解析规则（`paths.py` 里最绕的一半）也没了。

---

## 4. 治理落在哪：中间件层

deepAgents 的所有工具调用（它的 fs 工具、我们的工具）都经过中间件的
`awrap_tool_call`。所以治理在这一层做：

```text
模型产出 tool call
→ 我们的中间件 awrap_tool_call：
   ① 工具集收敛（allowed_tool_keys）
   ② 风险评估（命令含 rm -rf / URL 指内网 → 命中就 interrupt 挂起等人批）
   ③ 执行（调用真正的工具）
   ④ 结果验证 + 写一条 invocation（脱敏参数 + 各阶段结论）
   ⑤ 更新任务置信度，低于阈值 → 标记阻塞
```

与上一版方案相比，**审批仍然只有一套**：
- 声明级（`execute`/`web_*`/写类工具）→ deepAgents 的 `interrupt_on`（工具执行前抛中断）；
- 参数级（`rm -rf`、SSRF）→ 我们在中间件里调 `langgraph.types.interrupt()`。

两处中断的载荷形状不同，审批接口要能识别两种（见上一版方案的 3.3）。

### 4.1 工具名 → 策略的映射表

fs 工具不再由我们声明，但**治理策略还得有地方放**。用一张按工具名的映射表
（配置或代码常量）：

| 工具 | 风险 | 审批 | 幂等 | 备注 |
| --- | --- | --- | --- | --- |
| `read_file` / `ls` / `glob` / `grep` | read | 否 | ✓ | 只读 |
| `write_file` / `edit_file` | write | 是（写类） | ✓ | |
| `delete` | write | 是 | ✗ | **新能力**（以前没有）：不可重试 —— 删了就是删了 |
| `execute` | execute | 是 | ✗ | 命令内容另走参数级规则 |
| `web_fetch` / `web_search` | network | 是 | ✓ | |
| `run_python` | execute | 是 | ✗ | |

这张表同时是三样东西的来源：`interrupt_on` 集合、风险基线、留痕里的风险字段。
**它是"能力声明"在新架构下的替代品** —— 从"每个能力一份声明"变成"每个工具一行"。

---

## 5. 后端组装：CompositeBackend（文件本地 + 命令进沙箱）

deepAgents 的 `CompositeBackend` 按**路径前缀**路由文件操作，
而**命令执行不可按路径路由，固定走 default backend** —— 正好对上我们要的组合：

```python
CompositeBackend(
    default=DockerSandbox(...),                                   # execute → docker 沙箱
    routes={"/": FilesystemBackend(root_dir=workspace)},           # 文件操作 → 本地
)
```

### 5.1 我们的 docker 沙箱 → `BaseSandbox` 子类

`BaseSandbox` 是"只实现 `execute()`，其余由它在沙箱内跑命令实现"的抽象基类。
我们的沙箱正好是这个形状：

```python
class DockerSandbox(BaseSandbox):
    def execute(self, command, *, timeout=None) -> ExecuteResponse: ...   # docker run
    def upload_files(self, files) -> list[FileUploadResponse]: ...         # 写进挂载的工作区
    def download_files(self, paths) -> list[FileDownloadResponse]: ...     # 从工作区读
    @property
    def id(self) -> str: ...
```

隔离参数原样保留（`--network none --cap-drop ALL --memory 512m --pids-limit 256`），
只挂载会话工作区。`BaseSandbox` 的文件操作实现（`ls`/`cat`/`grep`/python 脚本）
在容器里跑 —— 但既然文件操作已经路由到 `FilesystemBackend`，它主要只用得上
`execute()`。

### 5.2 路径安全交给 deepAgents

`FilesystemBackend(root_dir=工作区, virtual_mode=True)`（默认）：

- 路径视为虚拟路径，锚定在 `root_dir`；
- 阻断 `..` 与 `~`；
- `resolve()` 后必须仍在 root 内（符号链接指到外面同样被拒）；
- 符号链接循环直接报错。

这与我们 `paths.py` 的保证等价 → **`paths.py` 删掉**。

### 5.3 一致性要求

容器只挂载会话工作区 → 容器里写的文件落在同一个工作区目录，
`execute` 造出的文件随后能被 `read_file` 读到。**P0 必须验这一条** ——
如果两边看到的不是同一个目录，模型的"先跑命令再读文件"就会读到空。

---

## 6. 技能：从 DB 导出成文件

DB 仍是技能的**来源**（`skill` / `skill_tool` 表 + 管理接口保留），
但**载体**换成 SKILL.md：

| 我们的字段 | SKILL.md | 作用 |
| --- | --- | --- |
| `name` | 目录名 + frontmatter `name` | 必须一致 |
| `description` | frontmatter `description` | 进技能索引，模型据此选 |
| `tool_ids` | frontmatter `metadata.include_tools` | 读了才披露这些工具 |
| `knowledge` / `checklist` / `examples` / `constraints` | 正文 | 读了才知道 |

导出时机：每次组装 agent 前把启用的技能导成文件（写到工作区**外面**的目录，
别让模型看见自己的技能定义），`skills=["/skills"]`。

**注意工具名翻译**：deepAgents 披露的工具名是它自己的（`read_file`/`write_file`/
`execute`/`ls`…），我们的 `tool_ids` 存的是能力 key，要翻译。

---

## 7. API 面的变化

| 接口 | 变化 |
| --- | --- |
| `POST /api/v1/agent/chat` | 返回值多一种"挂起审批中"的状态 |
| `POST /api/v1/invocations/{id}/approve` | 从"直接执行这次调用"改成"**恢复图**"（`ainvoke(Command(resume=…))`） |
| `GET /api/v1/invocations` | 语义从"调用记录 + 待审列表"变成"**纯审计流水**"（审批状态在 checkpoint 里） |
| `GET /api/v1/capabilities` | 从"统一能力清单"变成"工具来源汇总"（fs 工具 + 我们的工具 + MCP） |
| `GET/POST /api/v1/skills` | 保留（DB 是技能的来源），但加一个"导出成 SKILL.md"的预览接口 |
| `/api/v1/tools` | 保留（用户自建工具） |
| `/api/v1/mcp-servers` | 保留 |

---

## 8. 实施步骤

一次性交付，内部按依赖顺序做，每步跑通再进下一步。

| 步 | 内容 | 完成判据 | 状态 |
| --- | --- | --- | --- |
| **0** | **P0 实测**（最关键）：① 容器里写的文件能否被随后的文件工具读到；② `execute` 走 docker 的单次开销；③ `FilesystemBackend` 虚拟路径的越界拦截是否与我们现在的保证等价 | 三条都有实测结论 | ✅ |
| **1** | 装依赖；`DockerSandbox(BaseSandbox)` + `CompositeBackend` 组装 + `create_deep_agent` 跑通一次 `write_file` + 一次 `execute` | 文件读写与命令执行都正常；隔离参数生效；两边看到同一个目录 | ✅ |
| **2** | 中间件重写：工具收敛 + 参数级风险 interrupt + 留痕 + 置信度 | 每次工具调用都有一条 `invocation`；`rm -rf` 挂起等人批 | ✅ |
| **3** | 审批链路打通：HITL + 我们的 interrupt 两种载荷 + 审批接口恢复图 | 挂起 → approve → 图恢复并执行完 → 记录齐全 | ✅ |
| **4** | 删掉 fs.py / paths.py / verify.py / planner.py / notify.py，注册层与执行层瘦身，`ExecutionContext` 的 cwd 机制删掉 | 无残留引用；`ruff` + 测试通过 | ✅（`resolver.py` / `prompt.py` / `args_model.py` / `validator.py` 保留，见 §11.1） |
| **5** | 子代理（自带规格 + 显式带中间件）、技能导出、记忆、摘要 | 子代理的工具调用同样留痕；技能能按需披露 | ✅ |
| **6** | `delete` 的策略补上（risk=write / permission=confirm / 不幂等 / 永远要人批）+ API 契约调整 + 回归测试 + 文档更新 | 现有测试按新契约调整后全过 | ✅ |

### 第 0 步的实测结论（2026-11-01 已做）

跑 `scripts/p0_deepagents_spike.py`，用真实 agent（`create_deep_agent` + 假模型）
走完整工具链，结论：

| 测项 | 结论 |
| --- | --- |
| 文件工具走本地 `FilesystemBackend` | ✅ 写 + 读都通，0.2ms |
| **一致性**：容器写的文件宿主读得到 | ✅ 同一个工作区目录，看得到 |
| `execute` 走 docker 沙箱 | ✅ 正常；10 次工具调用（含 2 次起容器）总耗时 766ms |
| 路径越界 | ✅ `/etc/passwd` → not found；`../../etc/passwd`、`~/secret` → "Path traversal not allowed" |
| 容器看不见宿主文件系统 | ✅ |
| 容器内断网 | ✅ |

**前提成立，可以往下走。**

顺带发现并已处理的一个真问题：**工作区没被 docker 共享时，docker 不会报错，
而是挂一个空目录进去**，容器里的命令就在空工作区里跑，报错还特别误导
（`cannot open /workspace/xxx.sh: No such file`）。
macOS 上 Colima 默认只共享 `$HOME`，项目目录（`/Users/LIANJIFU/...`）不在其中。

已在 `DockerSandbox._ensure_mounted()` 里加挂载预检：第一次执行前放标记文件、
进容器确认看得见，看不见就明确报"工作区没有挂载进容器"并给出解决办法。

> 本机（macOS + Colima）要跑沙箱，`WORKSPACE_ROOT` 必须放在 `$HOME` 下。
> 这是环境限制，不是代码问题 —— Linux 生产环境没有这个限制。



---

## 9. 风险与"会失去什么"

### 9.1 会失去的（要认）

| 失去 | 影响 | 补法 |
| --- | --- | --- |
| 文件级审计 | 一次文件读写不再是一条 `invocation`（变成一串命令） | 在中间件层按 tool call 记（fs 工具的调用在中间件里看得见） |
| 逐能力的声明 / schema | fs 能力的契约不再由我们声明，改用 deepAgents 的 | 接受；我们的工具（web_*、run_python）仍走声明 |
| 四个文件能力（move/copy/mkdir/cd） | 用户可见的功能减少 | 已定：`mv`/`cp`/`mkdir` 靠 `execute`；`cd` 无等价物，会话级 cd 取消 |
| 会话级当前目录 | 所有路径固定相对工作区根 | 接受（语义变简单） |
| 技能检索打分 | 不再预先决定挂哪些技能 | deepAgents 让模型自己选；或用 `pinned_skills` 预挂载 |
| `assemble_prompt` 的技能注入 | 技能知识改成按需读 | 提示词里要求"动手前先读 SKILL.md" |

### 9.2 风险

| 风险 | 级别 | 说明 |
| --- | --- | --- |
| 沙箱与文件的一致性 | **高** | `execute` 在容器里写、`read_file` 在宿主读，必须确认看到的是同一个目录（P0 验） |
| 子代理不继承中间件 | **高** | 默认 general-purpose 子代理只继承"能覆盖默认槽位"的中间件；必须自己提供规格并显式带上我们的中间件，否则参数级风险与工具收敛在子代理里失效 |
| 审批契约变更 | 中高 | `chat` 多一种返回态；审批接口从"执行"变成"恢复图" |
| delete 是新能力 | 中 | 我们的风险分级、幂等标记里都没有它的位置，要补一条声明 + 审批 + 不可重试 |
| 留痕粒度变粗 | 中 | 见 9.1 |
| 提示词结构变化 | 中 | 技能知识按需读之后，模型可能不读就用错工具 |
| 沙箱语义变化 | 中 | 我们的 fs 是在宿主 Python 里做的，deepAgents 的是 `FilesystemBackend` 的虚拟路径 —— 错误信息与边界行为不一样 |
| Beta 依赖 | 低中 | deepAgents 是 Beta；保留 `create_agent` 回退开关 |

---

## 10. 决策记录

| 决策 | 选择 | 时间 |
| --- | --- | --- |
| 总体方向 | **以 deepAgents 实现为主，我们自己的实现能删就删** | 2026-11-01 |
| 文件工具 | 用 deepAgents 的 fs 工具（走我们的 sandbox backend） | 2026-11-01 |
| 审批机制 | deepAgents 的 HITL 挂起 | 2026-11-01 |
| 参数级风险规则 | 也做成挂起审批（自建 `interrupt()`） | 2026-11-01 |
| 接入范围 | 规划 / 子代理 / skills / 记忆 / 摘要，一次性全上 | 2026-11-01 |
| 依赖 | 接受 deepagents 0.7.32 的附带依赖 | 2026-11-01 |
| **文件操作与命令执行的位置** | **文件本地（`FilesystemBackend`，virtual_mode）+ 命令进 docker 沙箱（`CompositeBackend` 组合）** | 2026-11-01 |
| **move/copy/mkdir/cd** | **删掉，靠 `execute` 凑；`cd` 无等价物，会话级 cd 取消** | 2026-11-01 |
| **run_python** | **保留** | 2026-11-01 |
| **delete 工具** | **保留并给模型**（写类操作，走审批；补一条能力声明） | 2026-11-01 |
| **`delete` 的审批强度** | **永远要人批**（信任模式再宽松也要）—— 写文件能补救，删掉的回不来 | 2026-11-01 |
| **保留 `prompt.py` / `resolver.py` / `validator.py` / `args_model.py`** | 见 §11 实施记录 —— 这四个模块在删掉 fs 能力之后**仍有活干**，照 §3.1 删掉会留下空洞 | 2026-11-01 |

**已无待定项。** 剩下的都是实施细节，按第 8 节的步骤走。

---

## 11. 实施记录（2026-11-01 已完成）

迁移已实施完毕：172 个测试通过，`ruff` 干净。下面记的是**实施中与原方案不一致
的地方** —— 方案本身是判断，落地时的偏差才是别人接手时最容易踩的坑。

### 11.1 实际删掉的

| 文件 | 原计划 | 实际 |
| --- | --- | --- |
| `registration/builtin/fs.py` | 删 | ✅ 删（592 行） |
| `registration/builtin/paths.py` | 删 | ✅ 删（路径安全归 `FilesystemBackend`） |
| `registration/builtin/verify.py` | 删 | ✅ 删（效果确认由 deepAgents 的结果对象承担） |
| `intent/planner.py` | 删 | ✅ 删（技能检索退役，改为导出 `SKILL.md`） |
| `intent/notify.py` | 删 | ✅ 删（审批改走图恢复，不再需要结果回流） |
| `intent/resolver.py` | 删 | ❌ **保留** —— 直接调 API 的路径还要用它把 tool call 翻译成意图 |
| `capabilities/prompt.py` | 删 | ❌ **保留** —— 系统提示词仍然由我们拼（deepAgents 只追加它自己那几段） |
| `registration/args_model.py` | 删 | ❌ **保留** —— 我们自己的工具要用声明生成 pydantic 参数模型 |
| `registration/validator.py` | 删 | ❌ **保留** —— 我们自己的工具的入参 / 出参契约校验还要它 |

原方案把这四个模块和 fs 能力一起归进"deepAgents 已经做掉的"。实际执行时发现：
它们服务的是**我们自己的工具**（`web_fetch` / `web_search` / `run_python` / MCP），
不是 fs 能力。删掉它们会留下"工具的入参没人校验、agent 调 API 没人翻译意图"
两个空洞。fs 能力删掉之后，这四个模块的**职责变窄了**（不再管 fs 能力），但没有
消失。

**`create_agent` 回退开关没做**（§9.2 里列为 Beta 依赖的缓解措施）。理由：
deepagents 已锁定到 0.7.32 精确版本，"回退"实际等于降级依赖；保留一条并行的
`create_agent` 代码路径会让刚清掉的实现又长回来。真出问题时回退依赖版本比回退
代码路径更现实。

### 11.2 实施中发现的几个真问题

| 问题 | 处理 |
| --- | --- |
| **工作区没被 docker 共享时，docker 不报错，而是挂一个空目录进去** —— 容器里的命令在空工作区里跑，报错还特别误导（`cannot open /workspace/xxx.sh`） | 加 `DockerSandbox._ensure_mounted()` 预检：放标记文件、进容器确认看得见，看不见就明确报"工作区没有挂载进容器"并给出解决办法 |
| **技能目录名必须合法**（小写 ASCII 字母 / 数字 / 单个连字符，且要和 frontmatter 的 `name` 一致）—— 中文技能名转不出来 | 退化成 `skill-<hash>` 目录名，**中文名写进 frontmatter 的 `description`**，模型照样认得出是哪个技能 |
| **deepAgents 自带的通用子代理不继承我们的中间件** —— 它的中间件按默认槽位继承，`CapabilityMiddleware` 不在里面 | 自带一份子代理规格，显式挂上同一套治理中间件与 `interrupt_on`。`tests/test_subagents.py` 守住这件事（去掉中间件 → 子代理零留痕） |
| **`invoke` 不能按路径路由**，固定走 `CompositeBackend` 的 default —— 正好对上"文件本地、命令进沙箱" | 无需处理，这是 `CompositeBackend` 的语义 |
| **`SANDBOX_TYPE=local` 之前只对我们自己的执行器生效**，迁移后 deepAgents 的 `execute` 也要认 | `local` → deepAgents 的 `LocalShellBackend`（无隔离，仅开发）；`docker` → 我们的 `DockerSandbox` |
| **审批恢复是另一次 HTTP 请求** —— checkpointer 必须是**文件**而不是内存，否则图接不上 | `conftest.py` 的 checkpointer 改成临时目录里的文件 |
| **审批判了两遍**：deepAgents 的 HITL 放行之后，执行层的审批阶段又判了一次"要人批" —— **批准了等于没批准**，`run_python` / `web_fetch` 这些我们自己的工具在 balanced 模式下批准后照样不执行 | 执行层不再重复问：agent 路径上审批由 HITL 负责（`ExecutionContext.approval_handled`），工具能跑到执行层就说明人已经批过了 |
| **同一次调用让人批两遍**：声明级（HITL）批过之后，参数级（`rm -rf`）又挂一次 | 参数级只管**声明级放行过的那类调用**：平时不用人批的工具，碰到破坏性命令才单独问一次。一次调用一次审批 |
| **"要不要人批"有三套判定**：文件工具按风险表算、我们的工具按 permission 算、执行层又按另一套算 | 并成一份（`policy.needs_approval`），三处（`interrupt_on` / 告诉模型的提示 / 执行层）都用它 |
| **沙箱模式有两套**：`execute`（deepAgents 的命令工具）恒定走 docker，`run_python`（我们的执行能力）看 `SANDBOX_TYPE` —— 看着"更安全"，实际没人说得清哪个工具跑在哪 | 并成一条规则：两个工具都看 `SANDBOX_TYPE`，并且**默认值改成 `docker`**（平台执行 LLM 生成的代码，隔离是安全底线，不是可选项）；`local` 要显式打开 |

### 11.3 下线被接管的能力

`registry.sync_builtins()` 现在多了一步 `_retire_superseded()`：把
`read_file` / `write_file` / `edit_file` / `glob` / `grep` / `list_files` /
`move_file` / `copy_file` / `make_dir` / `change_directory` / `run_bash` 这些
出厂内置能力**停用并清空执行入口**。

不这么做的后果：能力清单里多出一堆实际不存在的工具；执行入口指向已经删掉的
模块，一旦被调到就 import 失败。

历史调用记录留着 —— 审计时还能查"以前有没有调过 `read_file`"。

### 11.4 验证脚本

| 脚本 | 验什么 |
| --- | --- |
| `scripts/p0_deepagents_spike.py` | 文件工具本地、容器写的文件宿主读得到、`execute` 走 docker、路径越界被拦、容器看不见宿主、容器内断网 |
| `scripts/p1_approval_spike.py` | 声明级审批：挂起 → approve → 工具执行 → agent 继续 |
| `scripts/p1_param_risk_spike.py` | 参数级审批：`rm -rf` 挂起 → reject → 工具不执行、模型收到拒绝消息 |
| `scripts/p2_deepagents_features_spike.py` | 技能导出并进提示词、记忆进提示词、工具集齐全（含 `task` / `delete`） |

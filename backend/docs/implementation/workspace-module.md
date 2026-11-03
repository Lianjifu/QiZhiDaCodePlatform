# 工作目录（环境）与多智能体协作

> 状态：**已实现**（2026-11-03）
>
> 关联文档：[能力模块](capability-module.md)、[后端存储](backend-strategy.md)、
> [编码助手](../README.md#编码助手deepagents--langgraph)

---

## 这套东西解决什么

会话默认在平台自带的**空白工作区**里干活 —— agent 改不到任何真实代码。这一套
让它能：

1. 把一个**真实的代码仓库**绑成工作目录；
2. 从仓库派生 **worktree**（独立检出 + 独立分支）；
3. 派**子 agent** 在自己的 worktree 上并行干活（多智能体）。

---

## 两个根目录

```text
agent 的工作目录  = 绑定的环境（可能是真实仓库）
会话工作区        = 平台自己的目录（上传件 / 技能导出 / 产物 / 文件页）
```

绑了环境之后两者**不是同一个地方**。分开的原因是：上传件不能落进用户的仓库里。

实现上，`ExecutionContext` 有两个字段：

| 字段 | 是什么 |
| --- | --- |
| `workspace` | agent 的文件工具根 + 命令执行的 cwd |
| `session_workspace` | 上传件、技能导出、产物、文件页 |

后端组装 backend 时按前缀路由（`app/capabilities/backends/assembly.py`）：

```text
/            → agent 的工作目录（环境）
/uploads     → 会话工作区的 uploads/   ← 绑了环境才挂
/.memory     → 会话工作区（AGENTS.md）  ← 绑了环境才挂
/skills      → 技能导出目录
/.artifacts/ → 对话历史外置
```

---

## 白名单：平台没有鉴权时的唯一闸门

```text
REPO_ROOTS="/Users/me/ops,/Users/me/work"   # 允许作为工作目录的根目录
WORKTREE_ROOT="/Users/me/qzd-worktrees"      # worktree 的落点
```

- `REPO_ROOTS` **默认空 = 一个都不许**。不配置就完全不能用这个功能，
  而不是"默认什么都能用"；
- 校验比较的是 `resolve()` 之后的真实路径 —— 软链接指向外面的一律拒绝；
- `WORKTREE_ROOT` 单独配，是因为 **worktree 要挂进沙箱容器**：容器运行时
  （Colima 只共享 `$HOME`、Docker Desktop 默认共享 `/Users`）只能挂载被共享的
  路径。放在仓库旁边则大概率挂不进去，容器里的命令会报
  "工作区没有挂载进容器"。

绑定关系存在**文件**里（`<工作区根>/.qzd/bindings/<会话目录名>.json`），不建表
—— `resolve_workspace()` 是同步函数，被文件接口等一大片地方调用；文件存储
同步可读、跨进程可见（审批恢复可能落在另一个进程上）。

---

## 选中仓库 = 自动开 worktree

用户选一个仓库，平台**自动**派生一份独立检出并绑上 —— 分支名由平台生成
（`qzd/<仓库>-<随机>`），不用先填。想自己命名分支时走「Worktree」入口。

绑定可以带一个 `label`（用户起的名字）—— 界面上显示的是它，不是目录名。

选目录靠 `GET /api/v1/environments/browse`：能往下钻、往回退，也能粘路径。
**目录浏览也是读宿主机的文件系统**，所以同样受白名单约束。

## git 是平台能力

沙箱镜像里没有 git；worktree 里的 `.git` 是个指向主仓库的**指针文件**，容器里
解析不了。所以 git 一律走**宿主侧**（`app/git/service.py`），暴露成 builtin
能力：`git_status` / `git_worktree_add` / `git_worktree_list` /
`git_worktree_remove` / `git_commit` / `git_merge`。

几个约定：

- **参数数组**，不拼 shell 字符串（分支名、路径都是外部输入）；
- 每次调用都有超时，输出截断；
- **没绑工作目录时，这些能力不出现在模型的工具列表里** —— 给模型一批用不了的
  工具，它会试了再报错，白烧轮次（过滤在
  `AgentService._visible_specs`）。

---

## 多智能体：派子 agent

```text
主 agent（工作区 A）
   │  delegate_worker(任务)
   ▼
worker（worktree B，分支 qzd/xxx）  ← 独立目录、独立分支、独立沙箱容器
   │  做完提交
   ▼
主 agent 拿到分支与结论 → 分支不会自动合回主线，由人手动合
```

worker 与主 agent 的三处不同（`app/agents/worker.py`）：

| | 主 agent | worker |
| --- | --- | --- |
| 工作目录 | 会话绑定的环境 | 自己的 worktree |
| 人工审批 | 有（前端点批准） | **无** |
| 历史 | 有 checkpointer，多轮 | 无，一次性执行 |

**审批那一条是明确的安全取舍**：worker 没有界面，挂起就等不到人批，所以
`build_agent(allow_interrupt=False)` + `CapabilityMiddleware(interrupt_enabled=False)`。
唯一的防线是**物理隔离** —— worker 只在自己的 worktree 里干活，命令在断网容器
里跑、只挂载那个 worktree。

递归封顶两层：worker 的能力清单里**没有** `delegate_worker` 本身。

### 三个容易踩的坑

| 坑 | 后果 | 做法 |
| --- | --- | --- |
| worker 继承外层图的 ambient config | **主会话的 checkpoint 被冲掉，而且不报错** | 显式传 `thread_id` + 不带 checkpointer |
| worker 沿用父会话的执行标识 | 内层工具调用跟外层抢同一个并发名额 → **自己把自己堵死** | worker 用自己的短标识（容器名会截断到 48 字符，不能带父会话 id） |
| 恢复挂起的节点会重放那一轮的工具 | 同一个 worktree 建两遍 | 建 worktree **幂等**（存在就复用） |

---

## 触发方式

**由模型自己判断**（`app/agents/prompts.py` 准则 10）：能不能拆、拆成几块、
什么时候切分支，都是模型的判断。平台不提供"并行派 N 个"的控件。

---

## 遗留与已知限制

- **平台没有鉴权** —— 知道 `thread_id` 就能把会话绑到白名单内的任意目录、
  让平台建分支跑 git、派子 agent 在仓库里写代码执行命令。涉及**源码仓库**，
  比现有的文件接口更严重。白名单是唯一的闸门。
- **worker 里的破坏性命令无人审批**（上面说了，物理隔离是唯一防线）。
- 分支**不自动合并** —— 用户明确选的：合并前先看它改了什么。
- 沙箱镜像里没有 git —— agent 想用 git 必须调 `git_*` 能力，在 `execute` 里
  敲 git 一定失败（提示词里写明了）。
- worktree 只在**平台创建**的才能删；用户自己 `git worktree add` 出来的我们不碰。

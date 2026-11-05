# 工作目录（环境）与多智能体派活

> 一句话：会话可以绑到一个真实目录（仓库或平台派生的 worktree）上干活，agent 也能把子任务派给跑在**自己独立 worktree** 里的子 agent。
>
> 前端入口：`/environments`（「工作目录」页，`frontend/src/pages/EnvironmentsPage.tsx`）；聊天页初始状态下的工作目录条（`frontend/src/features/environments/EnvironmentBar.tsx`）；右侧状态面板的「协作」段（`frontend/src/features/sessions/SessionStatusPanel.tsx`）。

---

## 1. 这个模块是什么

平台自带的会话工作区是**空的**——agent 在里面改不到任何真实代码。这块能力把
两件事接到那个空白上：

1. **环境（工作目录）**：把白名单下的一个目录绑给会话，绑上之后 agent 的文件
   工具根与命令 cwd 就是那个目录。仓库还可以派生出 **worktree**（独立检出 +
   独立分支），供多个 agent 并行改同一份代码。
2. **派活（多智能体）**：主 agent 调 `delegate_worker`，平台给子 agent 建一份
   独立 worktree，让它在那边跑完、提交，把分支与结论交回主 agent。

三块代码各管一段：

| 位置 | 管什么 |
| --- | --- |
| `app/environments/` | 白名单、路径规则、环境的发现与绑定、worktree 的建与删 |
| `app/git/service.py` | 宿主侧的 git 调用（状态、worktree、提交、合并） |
| `app/agents/worker.py` | 子 agent 的生命周期：建 worktree、跑、提交 |

git 能力本身**不走 HTTP**——它是注册层里的 builtin 能力（`app/capabilities/registration/builtin/git.py`），
只有会话绑了环境才会出现在模型的工具列表里。

### 两个目录要分清

`ExecutionContext` 上有两个路径字段，绑了环境之后它们**不是同一个地方**：

| 字段 | 是什么 |
| --- | --- |
| `workspace` | agent 的文件工具根 + 命令执行的 cwd（= 绑定的环境） |
| `session_workspace` | 会话工作区：上传件、技能导出、产物、文件页看的东西 |

分开的原因是**上传件不能落进用户的仓库里**。后端组装 backend 时按前缀路由
（`app/capabilities/backends/assembly.py`）：

```text
/            → agent 的工作目录（环境）
/uploads     → 会话工作区的 uploads/    ← 绑了环境才挂
/.memory     → 会话工作区（AGENTS.md）   ← 绑了环境才挂
/skills      → 技能导出目录
/.artifacts/ → 对话历史外置
```

没绑环境时两者相同（`AgentService.resolve_agent_workspace` 返回同一个路径），
行为与从前完全一致。

---

## 2. 接口清单

前缀 `/api/v1`，router 前缀 `/environments`（`backend/app/api/v1/environments.py`）。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/environments` | 列出白名单下的目录、仓库与已存在的 worktree。响应带 `enabled`（配没配白名单）与 `stale`（这份清单是不是上一次的） |
| GET | `/environments/roots` | 只读配置里的白名单根目录，**不扫盘** |
| GET | `/environments/browse?path=` | 浏览一个目录的子目录（选文件夹用）；不给 `path` 就浏览第一个白名单根 |
| POST | `/environments` | 从仓库创建 worktree。body：`repo_path`（必填）、`branch`（不给就自动生成）、`base` |
| DELETE | `/environments?path=` | 删除一个平台创建的 worktree（分支还留在仓库里） |
| GET | `/environments/worktrees?repo_path=` | 列 worktree；不给 `repo_path` 就扫白名单下的所有仓库 |
| GET | `/environments/binding/{thread_id}` | 查会话当前绑的工作目录（没绑时 `path` 是空串） |
| POST | `/environments/binding/{thread_id}` | 把会话绑到某目录。body：`path`、`label` |
| DELETE | `/environments/binding/{thread_id}` | 解绑（回到平台自带的会话工作区） |

业务失败统一走 `AppError` 的响应体（`code` / `error_code` / `message` / `details`）：
路径不合法是 **422**（`ValidationError`），目录/仓库不存在是 **404**（`NotFoundError`）。

git 相关的六个能力是 builtin，**没有 HTTP 接口**：

| 能力 key | 作用 | 风险档 |
| --- | --- | --- |
| `git_status` | 看当前分支、未提交改动 | read |
| `git_worktree_add` | 从当前仓库开一份独立检出 | write |
| `git_worktree_list` | 列当前仓库的所有 worktree | read |
| `git_worktree_remove` | 删一个平台创建的 worktree | destructive |
| `git_commit` | 提交当前目录的全部改动 | write |
| `git_merge` | 把一个分支合进当前分支 | destructive |

另有 `delegate_worker`（execute 档），派子 agent 干活。

---

## 3. 实现要点

### 3.1 白名单：平台没有鉴权时的唯一闸门

平台所有接口都不校验身份（用户已明确推迟鉴权）。所以「这个路径能不能当
工作目录」这道闸门是**唯一**的防线，配置项有两个：

```text
REPO_ROOTS="/Users/me/ops,/Users/me/work"   # 允许作为工作目录的根目录
WORKTREE_ROOT="/Users/me/qzd-worktrees"      # worktree 的落点
```

对应 `app/core/config.py` 的 `repo_roots` / `worktree_root`，规则在
`app/environments/paths.py`：

- **`repo_roots` 默认空 = 一个都不许。** 不配置就完全不能用这个功能，而不是
  「默认可用」。反过来那个默认值等于把宿主文件系统开放出去：任何人都能让
  平台去读写自己的仓库、别的项目、甚至 `~/.ssh`。
- `repo_roots()` 读配置 → 逗号分隔 → `expanduser()` → `resolve()` → **忽略
  不存在或不是目录的项并记 warning** → 去重。拼错的路径不会让整个功能瘫掉。
- `is_allowed(path)` 比较的是 **resolve 之后的真实路径**：目录里放一个指向
  `/etc` 的软链接过不了关。允许的根集合 = `repo_roots()` ∪ `{worktree_root()}`。
- `require_allowed(path)` 校验并返回解析后的路径；`repo_roots()` 为空时明确
  报错「平台还没有配置可用的仓库目录（REPO_ROOTS）」。

`worktree_root` 单独配，是因为 **worktree 要挂进沙箱容器**：容器运行时
（Colima 只共享 `$HOME`、Docker Desktop 默认共享 `/Users`）只能挂载被共享的
路径。默认落在 `<workspace_root>/worktrees`，正好在共享区里。

白名单根下有两个目录名不出现在「可绑定的目录」列表里：
`RESERVED_DIRNAMES = {".git", ".qzd"}`（`.qzd` 是平台自己放绑定文件的地方）。
候选扫描另外跳过**所有 `.` 开头的隐藏目录**。

### 3.2 绑定：存文件，不建表

绑定关系存在 `<workspace_root>/.qzd/bindings/<会话目录名>.json`
（`app/environments/store.py`），**不是数据库表**。两个理由：

1. `resolve_workspace()` 是**同步函数**，被文件接口、agent service 等一大片
   地方调用。绑定一旦进库，这个函数就得变成 async，牵动的东西太多。
2. **文件跨进程可见**——审批恢复可能落在另一个 worker 进程上，绑定关系必须
   所有进程都读得到（与权限模式文件、`app/agents/checkpointer.py` 同一个理由）。

文件名用 `workspace_dir_name(thread_id)`——与工作区目录同名，一眼能对上，
而且天然避开了路径穿越（那个函数会过滤字符并加哈希）。

`Binding` 的字段：`path` / `kind`（`folder` 或 `worktree`）/ `name` / `branch` /
`repo` / `bound_at`。`load_binding()` 读不出来（文件坏了或不存在）返回
`None` 且**不抛异常**——绑定读不出来不该让整个会话挂掉，退回默认工作区就行。

### 3.3 worktree 的建与删

`EnvironmentService.create_worktree(repo_path, branch, base, reuse)`：

```text
require_allowed(repo_path)        →  GitService.repo_root()（不是仓库则 404）
validate_branch(branch or auto_branch(root.name))
worktree_path(root.name, slugify(name.replace("/", "-")))
    = <worktree_root>/<仓库名>/<slug>
禁止建在主仓库内部
目标已存在 → reuse=False 报「worktree 已经存在」/ reuse=True 复用
GitService(root).worktree_add(target, name, base=base,
                               create_branch=name not in branch_list())
invalidate_environment_cache()
```

**`reuse=True` 是幂等**，不是可选优化：LangGraph 恢复挂起的节点时会把那一轮
里的工具调用**整个重放一遍**，不幂等的调用就会做两遍。worker 内部建 worktree
走的就是 `reuse=True`（`app/agents/worker.py`）。

分支已存在时不再 `-b`（`git worktree add` 会因为分支已存在而报错），改成
`git worktree add <路径> <分支>`。

`EnvironmentService.remove_worktree(path)`：

```text
require_allowed(path)
_is_platform_worktree(resolved)   ← 必须在 worktree_root 之下
                                    否则「只能删除平台创建的 worktree」
目录不存在 → 404
worktree_remove(force=True) + worktree_prune()
目录还在 → shutil.rmtree
invalidate_environment_cache()
```

**用户自己 `git worktree add` 出来的目录不碰**——那可能是人家正在用的检出。

### 3.4 命名规则

`app/environments/naming.py`：

- `auto_branch(repo_name)` → `qzd/{slugify(repo_name)[:24]}-{uuid4().hex[:4]}`，
  例如 `qzd/demo-1a2b`。带 `qzd/` 前缀，一眼看出是平台派出去的。
- `validate_branch(branch)` **交给 git 自己判定**（`git check-ref-format --branch`，
  timeout 10s），非法就报 `ValidationError`。不用正则——git 的规则比正则准，
  正则总差一条。
- `slugify(text, max_length=48)` 只留 `\w`（含中文）、`.`、`-`，其余换成 `-`；
  挑不出字符时返回 `"task"`。

### 3.5 git 是平台能力，不是 agent 敲的命令

沙箱镜像（默认 `python:3.13-slim`）里**没有 git**；worktree 里的 `.git` 是
一个指向主仓库的**指针文件**，容器里解析不了。所以 git 一律走宿主侧的
`app/git/service.py`，暴露成 builtin 能力。

那里的几条约定：

- **参数数组**传给 git，不拼 shell 字符串（分支名、路径都是外部输入）。
- 默认超时 30s；worktree 检出用 `WORKTREE_TIMEOUT = 180`。
- 输出超过 `MAX_OUTPUT = 20000` 字符截断。
- commit 时若用户没配 git 身份，用
  `-c user.name=QiZhiDa Agent -c user.email=agent@qzd.local` 兜底——不然
  `git commit` 直接失败，worker 的产出就留不下来。
- 扫目录时用 `repo_info()` **一次**问出「是不是仓库 / 当前分支 / 主仓库目录」
  （`git rev-parse --is-inside-work-tree --path-format=absolute --git-common-dir
  --abbrev-ref HEAD`）。分开问是三次进程启动，扫十几个目录就是好几秒。

**没绑工作目录时，这六个能力不出现在模型的工具列表里**——给模型一批用不了的
工具，它会试了再报错，白烧轮次。过滤在 `AgentService._visible_specs`：

```python
if Path(agent_workspace).resolve() == Path(session_workspace).resolve():
    return [spec for spec in specs
            if spec.key != "delegate_worker" and not spec.key.startswith("git_")]
```

主提示词里那几行协作说明也随之条件注入（`app/agents/prompts.py` 的
`COLLABORATION_PROMPT`，只在真的有 git / 派活能力时才加）。

### 3.6 归属校验：worker 能删掉别人的 worktree（实测复现过）

这是本模块最值钱的一条不变量。**缺陷**：能力 `git_worktree_remove` 的 `path`
参数**由模型给**，它直接把这个路径交给 `EnvironmentService.remove_worktree()`；
而那个方法只校验「在白名单内 + 是平台建的 worktree」，**不校验归属**。

实测复现：worker A 用 worker B 的路径调这个能力，**B 的 worktree 被真删了**。
再加上 worker 跑在 `permissive`（= bypass）且没有审批界面，一个 worker 能干掉
同仓库其他 worker 的产出。

**现在有两层防线**（`backend/docs/reviews/agent-autonomy-audit-2026-11-05.md` 的
R1b）：

**能力层**——`git_worktree_add` / `git_worktree_remove` / `git_merge` 从 worker
的能力清单里整个剔掉（`app/agents/worker.py` 的 `WORKER_BLOCKED_GIT_KEYS`），
worker 根本看不到这三个工具。worker 只留 `git_status` 与 `git_commit`。

**实现层**——`git_worktree_remove` 增加同仓库校验
（`app/capabilities/registration/builtin/git.py` 的 `_same_repo`，配套 `_own_repo`）：

```python
def _own_repo(context) -> str:
    return str(GitService(context.workspace).common_dir())   # 不是仓库就空串

def _same_repo(context, path) -> bool:
    own = _own_repo(context)
    if not own:
        return False
    other = GitService(path).common_dir()
    return bool(other) and str(other) == own
```

**用 `common_dir` 而不是仓库根**，因为 worktree 里的 `.git` 是指针文件：同一个
仓库的多个 worktree，`common_dir`（主仓库的 `.git` 目录）相同；不同仓库则不同。
这正是「是不是同一个仓库」的判据。

> 改错的后果：去掉 `_same_repo` 那一段，模型只要给出一个别人的 worktree 路径
> 就能删掉它——删掉的是目录连同里面的未提交改动，不可逆。

注意主 agent 仍然持有 `git_worktree_remove`，同仓库校验挡的是**跨仓库**。
同仓库内的其他 worktree（比如某个 worker 的）主 agent 仍能删——那一档是
destructive 风险，走人工审批（只有 bypass 放它）。

### 3.7 多智能体派活

`app/agents/worker.py::run_worker`：

```text
主 agent（工作区 A）
   │  delegate_worker(任务)
   ▼
worker（worktree B，分支 qzd/xxx）  ← 独立目录、独立分支、独立沙箱容器
   │  做完提交
   ▼
主 agent 拿到分支与结论 → 分支不会自动合回主线，由人手动合
```

**为什么自己跑，而不是用 deepAgents 自带的 `task` 工具**：那个工具派的子代理
**和主 agent 共用同一个工作区**——两个子代理同时改同一目录就是互相覆盖。

worker 与主 agent 的三处不同：

| | 主 agent | worker |
| --- | --- | --- |
| 工作目录 | 会话绑定的环境 | 自己的 worktree |
| 人工审批 | 有（前端点批准） | **无** |
| 历史 | 有 checkpointer，多轮 | 无（`checkpointer=None`），一次性执行 |

**审批那一条是明确的安全取舍**：worker 没有界面，挂起就等不到人批，所以
`build_agent(allow_interrupt=False)` + `trust_mode="permissive"`。唯一的防线
是**物理隔离**——worker 只在自己的 worktree 里干活，命令在断网容器里跑、
只挂载那个 worktree。这条取舍是用户明确选的。参数级风险判定**照跑照记**
（`interrupt_enabled=False` 时仍然 `assess_call` 算一遍，风险写进调用记录，
命中规则时打 warning），只是不再拿去拦人——「不问人」和「不判定」是两件事。

**递归封顶两层**：worker 的能力清单里没有 `delegate_worker` 本身
（`NO_DELEGATE_KEY`），它派出去的子代理也无法再派。

worker 跑完，平台代它提交：工作区脏就 `git.commit(f"worker: {title}")`；
工作区干净说明它自己提交过了（有 `git_commit` 能力），用 `head_sha()` 跟跑前的
HEAD 对比来判断。

三个踩过的坑（注释里都写着）：

| 坑 | 后果 | 做法 |
| --- | --- | --- |
| worker 继承外层图的 ambient config | **主会话的 checkpoint 被冲掉，而且不报错** | 显式传 `thread_id` + 不带 checkpointer |
| worker 沿用父会话的执行标识 | 内层工具调用跟外层抢同一个并发名额 → **自己把自己堵死** | worker 用短标识 `w{uuid4().hex[:8]}`（容器名会截断到 48 字符，带上父会话 id 会撞名） |
| 恢复挂起的节点会重放那一轮的工具 | 同一个 worktree 建两遍 | 建 worktree **幂等**（`reuse=True`） |

### 3.8 前端

**聊天页（初始状态）**：对话框**下方**有一个按钮，显示当前绑定的名字（没绑
就显示「会话工作区」）与分支。点开是选择器，清单分三段：

| 选什么 | 会发生什么 |
| --- | --- |
| 仓库 | **自动开一个新分支 + 一份独立检出**并绑上（分支由平台生成） |
| 本地目录 | 直接绑上，agent 就在那个目录里干活 |
| Worktree | 直接绑上已有的检出 |

**没有「新建 worktree」那一档**——选中仓库就会自动分配，独立检出不是一个需要
单独配置的动作。「新建工作区」是另一件事：起个名字 + 选一个本地目录。

目录选择是一个能往下钻的浏览器（点子目录进去、上一级退回、或直接粘路径），
**只能选白名单内的目录**——目录浏览也是读宿主机的文件系统，白名单是唯一的闸门。

清单是**懒加载**的：打开选择器时才去拉（扫一遍要一秒多，每个目录都要问一次
git）。开场只问 `GET /environments/roots`——那个只读配置，几毫秒。

**只在初始状态出现**——聊起来之后不占地方，要换目录去「工作目录」页。

**`/environments` 页**：分「允许的根目录 / 仓库 / Worktree / 目录」四段，能新建
worktree、删除 worktree。白名单没配时，页面明确显示「平台还没有配置可用的仓库
目录」并说明 `REPO_ROOTS` 怎么配，而不是给一个空列表让人猜。

**状态面板的「协作」段**：当前绑在哪个目录、哪个分支、仓库里还有哪些 worktree，
并写明「派出去的子 agent 会留下分支——分支不会自动合回主线，要合由你来合」。

### 3.9 清单缓存

扫一遍白名单要一秒多（每个候选目录都要问 git），而仓库状态不是秒秒变的东西，
所以有 `LIST_CACHE_TTL_SECONDS = 15.0` 的短缓存（`app/environments/service.py`）。

三种情况：

- **有新鲜的缓存** —— 直接给（微秒级）；
- **有过期的缓存** —— 先给旧的，同时后台重扫（用户不等），响应里的 `stale`
  字段为 `true`，前端据此显示「正在更新工作目录…」；
- **什么都没有**（服务刚起） —— 老老实实扫一遍。

新建/删掉 worktree 会调 `invalidate_environment_cache()`，它**标记过期但
不丢弃缓存**——丢掉的话用户刚建完回到列表页又得干等一秒多。服务启动时
`warm_environment_cache()` 在后台先扫一遍，第一个读的人就不用等。

---

## 4. 已知限制

- **平台没有鉴权** —— 白名单是唯一的闸门。知道 `thread_id` 就能把会话绑到
  白名单内的任意目录、让平台建分支跑 git、派子 agent 在仓库里写代码执行
  命令。涉及**源码仓库**，比现有的文件接口更严重。
- **worker 里的破坏性操作无人审批** —— 唯一防线是物理隔离（断网容器、只挂载
  自己那个 worktree）。worker 跑在 `permissive`（= bypass）上，参数级风险
  判定照跑照记但不拦人。这是用户明确选的取舍。
- **沙箱镜像里没有 git** —— agent 在 `execute` 里敲 git 一定失败（worktree 的
  `.git` 是指针文件，容器内解析不了）。git 必须走 `git_*` 平台能力。
- **worktree 只删平台创建的** —— 用户自己 `git worktree add` 出来的目录不碰。
- **分支不自动合并** —— 用户明确选的：合并前先看它改了什么。要合就用
  `git_merge`，或由人手动合。
- **环境清单缓存 15 秒** —— 仓库状态不是秒秒变的东西，这是一致性与速度的
  取舍。过期时先给旧数据再后台刷新，响应里的 `stale` 字段告诉前端「正在更新」。
- **只扫根目录 + 一级子目录**（`_candidate_dirs`）—— 更深的不扫，仓库可能很大。
- **并发配额是进程内的**（`capability_max_concurrency`，默认 4，按会话限）
  —— 多副本部署时每个副本各自计数，不是全局配额。
- **`POST /environments` 没有暴露 `reuse` 参数** —— 重复建同一分支直接 422
  「worktree 已经存在」。只有 worker 内部调用走 `reuse=True`。
- **代码注释与实现不符**：`app/api/v1/environments.py` 的 `create_worktree`
  docstring 说 worktree 落在白名单根目录下的 `.qzd-worktrees/`——这是错的。
  实际落在 `settings.worktree_root`（默认 `<workspace_root>/worktrees`），
  路径按 `<worktree_root>/<仓库名>/<slug>` 组织；`RESERVED_DIRNAMES` 里只有
  `.git` 与 `.qzd`，没有 `.qzd-worktrees`。以 `app/environments/paths.py` 为准。

---

## 5. 相关文件

后端：

- `backend/app/environments/paths.py` —— 白名单与路径规则（`repo_roots` /
  `worktree_root` / `is_allowed` / `require_allowed` / `worktree_path`）
- `backend/app/environments/naming.py` —— `auto_branch` / `validate_branch` / `slugify`
- `backend/app/environments/store.py` —— 绑定关系的文件存储
- `backend/app/environments/service.py` —— 环境的发现、绑定、worktree 的建与删、清单缓存
- `backend/app/git/service.py` —— 宿主侧 git 调用
- `backend/app/api/v1/environments.py` —— HTTP 接口
- `backend/app/schemas/environment.py` —— 请求/响应模型
- `backend/app/capabilities/registration/builtin/git.py` —— git 能力与 `_same_repo` 归属校验
- `backend/app/capabilities/registration/builtin/delegate.py` —— `delegate_worker`
- `backend/app/agents/worker.py` —— 子 agent 生命周期与能力收窄
- `backend/app/services/agent.py` —— `resolve_agent_workspace` 与 `_visible_specs`
- `backend/app/core/workspace.py` —— `workspace_dir_name` / `resolve_workspace`

前端：

- `frontend/src/pages/EnvironmentsPage.tsx` —— 「工作目录」页
- `frontend/src/features/environments/EnvironmentBar.tsx` —— 聊天页的工作目录条与选择器
- `frontend/src/features/environments/FolderBrowser.tsx` —— 目录浏览器
- `frontend/src/features/environments/format.ts` —— 显示规则与过滤
- `frontend/src/api/resources.ts` —— 接口封装

测试：

- `backend/tests/test_environments.py` —— 用**真的 git** 跑（worktree、分支、
  合并这些东西用 mock 测不出真问题）
- `backend/tests/test_delegate_worker.py`
- `backend/tests/test_autonomy_fixes.py` —— R1 / R1b 等修复的回归测试

# 后端存储方案（deepAgents backends）

> 状态：**已实现**（2026-11-02）
>
> 关联文档：[implementation/context-module.md](context-module.md)（上下文模块，已实现）、
> [implementation/memory-module.md](memory-module.md)（记忆模块，已实现）、
> [implementation/capability-module.md](capability-module.md)（能力层，已实现）

deepAgents 把"文件从哪来、命令在哪跑"抽象成 **backend**。我们现在的形态是
`CompositeBackend(default=DockerSandbox, routes={"/": FilesystemBackend(工作区), ...})`。

本方案要做三件事：

1. 说清六种 backend 各自**是什么、什么时候该用**（哪些我们该用、哪些明确不用）；
2. 修掉实测出来的**三个真实缺陷**（下面 §3）；
3. 给出一条**能撑住 code agent 真实工作流**的路径 —— 尤其是 execute 的
   环境不持久这件事。

---

## 0. 结论速览

| 面 | 结论 |
| --- | --- |
| 主力组合 | `CompositeBackend(default=DockerSandbox, routes={"/": FilesystemBackend(工作区), …})` —— **保持** |
| 文件操作 | 走宿主 `FilesystemBackend`（快、路径已经是虚拟路径） |
| 命令执行 | 走 `DockerSandbox`（隔离）+ **容器按会话复用**（装好的依赖跨轮次保留） |
| `StateBackend` | **不用** —— 文件进 checkpoint，每次 edit 重写全量，体积爆炸 |
| `StoreBackend` | **暂不用** —— 跨会话文件存储有价值，但"不在文件系统里"这个语义要先想清楚 |
| `ContextHubBackend` | **不用** —— 依赖 LangSmith，我们不接 |
| 已修 | ① 沙箱路径空间不一致 ② `ls("/")` 看不到子路由 ③ execute 环境不持久 |

---

## 1. 六种 backend 是什么

deepAgents 的 `BackendProtocol` 有 9 个方法：`ls / read / grep / glob / write /
edit / delete / upload_files / download_files`（各带异步包装）。**读与写可以
落在不同 backend 上** —— 协议不禁止，Composite 就是这么用的。

`SandboxBackendProtocol` 只多两个成员：`id` 属性和
`execute(command, *, timeout=None) -> ExecuteResponse`。

| backend | 文件存哪 | 跨会话 | 有 execute | 我们的判断 |
| --- | --- | --- | --- | --- |
| `FilesystemBackend` | 宿主文件系统（`root_dir`） | 天然跨会话 | ❌ | ✅ **主力** |
| `LocalShellBackend` | 宿主文件系统 + `subprocess` | ✅ | ✅ | ⚠️ 仅本地开发（无隔离） |
| `DockerSandbox`（我们的） | 宿主工作区（挂载进容器） | ✅ | ✅ | ✅ **命令执行** |
| `StateBackend` | LangGraph state 的 `files` channel | ❌ 单 thread | ❌ | ❌ **不用** |
| `StoreBackend` | LangGraph `BaseStore` | ✅ 跨 thread | ❌ | ⏸ **暂不用** |
| `ContextHubBackend` | LangSmith Hub 的 agent repo | ✅ | ❌ | ❌ **不用** |

### 1.1 `FilesystemBackend` —— 文件操作的主力

```python
FilesystemBackend(root_dir=None, virtual_mode=True, max_file_size_mb=10)
```

- `virtual_mode=True`（默认）阻断：路径含 `..`、`~` 开头、resolve 后越出
  `root_dir`；写用 `O_NOFOLLOW`（软链写入失败）。
  **实测确认**：`read("/../../etc/passwd")` → `ValueError: Path traversal not allowed`。
- `virtual_mode=False` 是**完全无隔离**的，别用。
- `max_file_size_mb` **只管 grep 的 Python fallback**，不是 read 上限。
  read 走分页（`offset`/`limit`）；二进制按扩展名判定，整份 base64 塞进
  `FileData(encoding="base64")`，**无大小上限**。

### 1.2 `StateBackend` —— 为什么不用

文件存在 LangGraph 的 `files` state channel，也就是**会被 checkpointer 序列化**。
我们用 SQLite：每个文件内容都进 checkpoint，**每次 edit 重写整份**。

一个几百 KB 的文件，几轮编辑之后 checkpoint 就是几 MB —— 而且我们已经有
SQLite checkpointer 在存对话历史了，再塞一份文件内容进去是雪上加霜。

> deepAgents 自己也警告这件事：官方 middleware 检测到 blobs 路由到
> StateBackend 时会强制关掉 `offload_binary_content`。

**适用场景**是"无沙箱的纯对话 + 临时虚拟文件"，不是我们这种要读写真实代码
的 code agent。

### 1.3 `StoreBackend` —— 什么时候才该上

基于 LangGraph `BaseStore`（长期存储，独立于对话历史，跨 thread 存在）。
`NamespaceFactory` 从 runtime 解析命名空间，按用户/项目隔离。

**语义上它不是文件系统**：agent `write("/memories/x")` 之后，
沙箱里 `cat /memories/x` **看不见** —— 它在 store 里，不在磁盘上。

对我们来说，它的价值是"跨会话的、长期的文件"（比如用户上传的项目资料）。
但这跟记忆模块的职责会重叠 —— **记忆模块已经解决了跨会话的"经验"存储**，
而且是带评分、带召回的。再引入 StoreBackend 存"文件"，要先把两者的边界划清。

**结论：暂不引入。** 等真有"跨会话的非代码文件"需求时再说。

### 1.4 `ContextHubBackend` —— 不用

依赖 LangSmith Hub 的 agent repo。我们不接 LangSmith。

---

## 2. `CompositeBackend` 的路由语义（实测）

```python
CompositeBackend(default=..., routes={...}, artifacts_root="/")
```

- **路由匹配**：最长前缀优先 + 边界强制（`"/skills"` 不会误匹配 `"/skillsx"`）。
- **`execute` 不可路由**，永远走 `default`。
- **`artifacts_root` 不是路由**，只是给 middleware 用的路径前缀：
  `{artifacts_root}/conversation_history/`、`/large_tool_results`、`/blobs`。
  这些路径随后按**普通路由**走 —— 所以 `artifacts_root` **必须配对应 route**，
  否则掉进 `default`（= 沙箱）。我们已用 `/.artifacts/` + route，语义正确。

### 2.1 聚合行为（实测）

| 操作 | path 为 None 或 `"/"` 时 |
| --- | --- |
| `ls` | ⚠️ **只搜 default + 命中 `/` 的那个 route** —— 看不到 `/skills/`、`/.artifacts/` |
| `glob` | ✅ 搜 default + 所有 route，路径重映射回前缀 |
| `grep` | ✅ 搜 default + 所有 route |

实测输出：

```text
ls('/')      -> ['/main.py']                    ← 看不到 /skills/、/.artifacts/
glob('**/*.md') -> ['/.artifacts/h.md', '/skills/SKILL.md']
grep('y')    -> ['/skills/SKILL.md']
```

**为什么**：`route "/"` 命中后走的是"单 backend 分支"，root 聚合分支不可达。

---

## 3. 三个必须修的缺陷

### 3.1 沙箱路径空间不一致（最危险，潜伏中）

`BaseSandbox` 的文件方法（`ls`/`read`/`grep`/`glob`）是**在沙箱里跑命令**
实现的，路径**原样**进容器：

```python
# BaseSandbox.read
result = self.execute(_build_read_cmd(file_path, offset, limit))
```

实测它生成的命令里路径是 base64 编码的虚拟路径：

```text
path = base64.b64decode('Lw==').decode('utf-8')   # = "/"
```

而我们的容器里，工作区挂在 `/workspace` —— 虚拟路径 `/a.txt` 在容器里
**根本不存在**（那是容器的根目录）。

**今天为什么没炸**：route `"/"` 把所有文件操作都截给了 `FilesystemBackend`，
`DockerSandbox` 的文件方法是**死代码**。一旦哪天去掉 route `"/"`（比如为了
让沙箱统一处理文件），立刻全线崩溃。

而且 `upload_files`/`download_files` 我们实现的是**工作区相对**路径
（`_safe_path` 解析到 `self.workspace`），与沙箱内的 `/workspace/...` 又是
另一套基准 —— 三方不一致。

**修法**（二选一）：

| 方案 | 做法 | 代价 |
| --- | --- | --- |
| **A. 明确边界**（推荐） | 保持 route `"/"` 走宿主 `FilesystemBackend`；在 `DockerSandbox` 的类文档里写死"**只提供 execute**，文件方法不实现也不应被调用"；加断言，若 `DockerSandbox` 的文件方法被调用就报错 | 0 |
| B. 路径翻译 | `DockerSandbox` 覆写所有文件方法，把虚拟路径翻译成 `self.mount + path` | 每个文件操作一次 `docker run`（0.5–1s 冷启），编辑尤其慢 |

**选了 A**（✅ 已实现）：`DockerSandbox` 的文件方法全部覆写成
`NotImplementedError`，误用立刻炸出来。B 是"沙箱统一处理文件"的完整形态，
等真的需要时再做 —— 但要做就做全，别只翻译一半。

### 3.2 `ls("/")` 看不到子路由

agent 用 `ls /` 探索工作区时，看不到 `/skills/` 和 `/.artifacts/` —— 而
`glob`/`grep` 却能搜到它们。同一个后端集合，三种操作三种可见性，模型会困惑。

**修法**：包一层 `PlatformBackend`（✅ 已实现）—— `ls("/")` 时把
`/skills/`、`/.artifacts/` 作为目录条目补进结果。改动小、语义清楚。

实测现在：

```text
ls('/')          -> ['/.artifacts/', '/main.py', '/skills/']
ls('/skills/')   -> ['/skills/SKILL.md']
```

（另一个思路是把 skills/artifacts 做成工作区里的**真实子目录**，用 `.qzd/`
前缀藏起来。但那样 agent 就能看见目录名 —— 反而更糟。）

### 3.3 execute 的环境不持久（对 code agent 最要命）

实测（`LocalShellBackend`，与 Docker 语义相同 —— 每次都是新进程/新容器）：

```text
第一次: export FOO=hello; cd /; pwd  ->  /  hello
第二次: echo 当前FOO=$FOO; pwd       ->  当前FOO=（空）  /path/to/ws
```

**每次 `execute` 都是全新环境**：环境变量、`cd`、装好的依赖、后台进程全丢。

对 code agent 这是硬伤 —— 用户说"装一下 pnpm"、"设个环境变量"、
"cd 到 src 目录"，agent 装完下一轮又没了。

根因是我们的 `docker run --rm`：**每次都是新容器**。

**修法**：把容器**按会话复用**，不再每次新建（✅ 已实现）。

```text
创建：docker run -d --name qzd-sbx-<会话> … （后台常驻）
执行：docker exec -w /workspace qzd-sbx-<会话> sh <脚本>"
销毁：会话结束 / 超时未使用 → docker rm -f
```

这样环境变量、`cd`、已装的依赖都留着。

代价与要处理的：
- **容器生命周期**：什么时候销毁？会话结束没人知道（平台没有"会话结束"这个
  事件，跟记忆模块的短期记忆是同一个问题）。→ 用**空闲超时**：N 分钟没执行
  就回收。
- **并发**：同一会话的两个请求同时 execute 怎么办？→ 容器内 `cd` 之类的状态
  本来就是共享的，串行化执行（同一会话一个执行锁）。
- **资源占用**：常驻容器占内存。→ 空闲超时回收 + 限制并发会话数。
- **崩溃**：容器挂了要能自愈。→ 执行前探活，死了就重建。

> ⚠️ 这条是本方案里**改动最大**的一条。`docker exec` 取代 `docker run`
> 是执行路径的核心变更，涉及沙箱的锁、生命周期、探活。

---

## 4. 目标形态

```python
CompositeBackend(
    default=DockerSandbox(workspace, …),          # 只提供 execute
    routes={
        "/": FilesystemBackend(root_dir=workspace),   # 文件操作走宿主（快）
        "/skills/": FilesystemBackend(root_dir=skills_dir),
        "/.artifacts/": FilesystemBackend(root_dir=artifacts_dir),
    },
    artifacts_root="/.artifacts/",
)
```

**保持现状的路由结构**，补三件事：

| 改动 | 对应缺陷 | 规模 |
| --- | --- | --- |
| `DockerSandbox` 明确"只提供 execute" + 断言 | §3.1 | 小 |
| `ls("/")` 补虚拟目录项 | §3.2 | 小 |
| 容器按会话复用（`docker exec`） | §3.3 | **大** |

### 4.1 为什么文件操作继续走宿主

- **快**：宿主 `FilesystemBackend` 是普通文件操作；沙箱方案每次一次
  `docker run`（0.5–1s 冷启），`edit` 可能多次。
- **路径已经对**：`FilesystemBackend(virtual_mode=True)` 用的就是虚拟路径，
  天然一致。
- **工作区是真实的**：用户在文件管理器里就能看到 agent 改了什么 —— 这是
  code agent 的基本要求，不是缺陷。

### 4.2 容器复用的具体设计

```text
app/capabilities/backends/
  sandbox_backend.py     DockerSandbox（execute 的实现）
  container_pool.py      容器生命周期：创建 / 复用 / 探活 / 回收
```

**池的键**：`thread_id`（会话级）。

**流程**：

```text
execute(command)
  → 拿会话的容器（没有就建，建之前先探活）
  → docker exec qzd_<thread> sh -c "<脚本>"
  → 返回 ExecuteResponse
```

**回收**：
- 空闲超过 `SANDBOX_IDLE_TIMEOUT`（默认 30 分钟）→ `docker rm -f`
- 容器死亡 → 下次执行前探活发现，删掉重建
- 平台重启 → 启动时清理残留容器（按名字前缀）

**并发**：
- 同一会话串行化（一个 `asyncio.Lock`）—— 共享 cwd/环境变量，串行才不打架
- 不同会话并行（各自容器）

**安全**（容器复用后要重新确认的）：
- 仍然断网（`--network none`）
- 仍然只挂载工作区
- 仍然限资源
- ⚠️ **新增**：常驻容器会积累 agent 装的东西、产生的文件 —— 全部在工作区里，
  会话结束要清理
- ⚠️ **新增**：容器长期存活 → 之前"每次新容器"顺带清掉的东西（临时文件、
  后台进程）现在会留着。需要一条清理策略。

---

## 5. 实施情况

| 阶段 | 内容 | 前置 | 状态 |
| --- | --- | --- | --- |
| **B0** | `DockerSandbox` 明确边界：文件方法全部覆写成报错 | — | ✅ 已完成 |
| **B1** | `ls("/")` 补虚拟目录项（`/skills/`、`/.artifacts/`） | — | ✅ 已完成 |
| **B2** | 容器按会话复用：`docker exec` + 空闲回收 + 探活 + 会话内串行 + 容量上限 | B0 | ✅ 已完成 |
| **B3** | 补测试：路由 / 隔离 / 路径空间 / 容器复用 | B1、B2 | ✅ 已完成 |

实施过程中相对本方案的**三处调整**：

### 5.1 容器复用保不住环境变量与 cd

原方案说"容器复用后环境变量、cd 都留着"—— **不准确**。

`docker exec` 每次都是**新进程**：`export FOO=1` 只在那条命令的 shell 里有效，
下一条命令又是干净的 shell。容器复用保住的是**文件系统状态**：

| 保留 | 不保留 |
| --- | --- |
| 装好的包（pip / npm install…） | 环境变量（`export`） |
| 容器里生成的文件 | 当前目录（`cd`） |
| 跑起来的后台进程 | |

实测（`docker exec` 两次）：

```text
第一次: export FOO=hello; cd /tmp; pwd  ->  /tmp  FOO=hello
第二次: echo FOO=$FOO; pwd              ->  FOO=（空）  /workspace
```

要保住后两项得改成"持久 shell + 管道"（coproc），实现复杂、shell 状态跨请求
容易失控 —— **当前不做**。

对策：系统提示词加了一条准则 8，把这个语义告诉模型 —— 要设变量就 export 和
使用写在同一条命令里，要 cd 就用 `cd X && 命令`。

**"装好的依赖保留"才是 code agent 最需要的**（agent 装完 pnpm，下一轮还能用），
这部分拿到了。

### 5.2 复用的容器要校验挂载点

复用容器时它可能是**上次运行**建的，挂载的是旧工作区。挂载不对的话 agent
写的文件会落到别人的目录里 —— 静默但危险。

所以每次复用前查一次容器的挂载源，与当前工作区对不上就重建。

### 5.3 `PlatformBackend` 必须继承 `SandboxBackendProtocol`

deepAgents 的 `supports_execution()` 用 `isinstance` 判断后端能不能执行命令：

```python
if isinstance(backend, CompositeBackend):
    return isinstance(backend.default, SandboxBackendProtocol)
return isinstance(backend, SandboxBackendProtocol)
```

包一层之后 `PlatformBackend` 两个都不是 —— execute 工具会直接报"后端不支持
执行"。所以它继承 `SandboxBackendProtocol` 并转发 `id` / `execute`。

---

## 6. 已知风险

1. **容器复用会积累状态**。常驻容器里装了依赖、留了临时文件、可能还有
   后台进程。以前"每次新容器"顺带清掉这些，现在不会了。
   - 对策：容器空闲回收（默认 30 分钟）+ 容器数上限（默认 32）+ 启动时清理
     残留。**工作区里的文件仍然归会话管**（那是用户的东西，本来就该留着）。
2. **容器复用的安全面变大**。以前每次新容器 = 干净起点；现在 agent 装的
   东西、留的凭据会跨轮次存在。
   - 对策：仍然断网（`--network none`）、只挂工作区、限资源。
3. **并发**：同一会话两个请求同时 execute。
   - 对策：✅ 会话级 `asyncio.Lock`；不同会话并行。
4. **平台重启后的残留容器**。
   - 对策：✅ 启动时按名字前缀清理。
5. **`ls` 的不一致**（模型看到 `ls /` 没有 skills/）。
   - 对策：✅ B1 包一层补虚拟目录项。实测现在 `ls('/')` 返回
     `['/.artifacts/', '/main.py', '/skills/']`。
6. **B0 之后如果有人去掉 route `"/"`**，文件操作会落到 `DockerSandbox`，
   然后被 `NotImplementedError` 拦下 —— 这正是 B0 要的效果（响亮地失败，
   而不是静默给出"文件不存在"）。
7. **环境变量与 cd 不跨命令**。
   - 对策：✅ 提示词准则 8 说清楚；`cd X && 命令` 是可靠写法。
8. **工作区路径必须在 docker 的共享范围内**（macOS 上 Colima 默认共享
   `$HOME`）。路径不在共享范围时，docker 会把它当成**容器内的新目录**创建 ——
   宿主写的文件容器看不见，而且**不报错**。
   - 对策：挂载预检（`_ensure_mounted`）会明确报出来。但预检用的是
     `docker run`，探测的是"能不能挂上"，共享范围外的路径**能挂上**（挂的是
     容器内的新目录），预检会误判为通过。
   - **这条是已知缺陷**：预检检测不出"路径在共享范围外"。真要严格，得比对
     容器内看到的文件与宿主是否一致（预检时写一个标记文件、在容器里读它）。

---

## 7. 待定项

| # | 问题 | 结论 |
| --- | --- | --- |
| 1 | 容器按会话复用，还是按工作区复用？ | **按会话** —— 工作区就是会话级的（`workspace_root/<thread_id>`） |
| 2 | 容器空闲多久回收？ | **30 分钟**（`SANDBOX_IDLE_TIMEOUT`）—— 够覆盖一次连续的编码任务 |
| 3 | 会话结束谁负责销毁容器？ | **没人知道会话结束**（平台没这个事件）→ 靠空闲回收 + 启动时清理 |
| 4 | `StoreBackend` 要不要上？ | **暂不上** —— 与记忆模块的职责会重叠，先把记忆模块用好 |
| 5 | 大文件（base64 无上限）要不要加限制？ | **要** —— 读二进制文件进上下文没有上限是隐患，加个 `max_file_size_mb` 兜底 |

---

## 8. 文件清单

```
app/capabilities/backends/
  assembly.py          组装（挂 PlatformBackend 包装层）
  platform_backend.py  包装层：ls("/") 补虚拟目录项 + 转发
  sandbox_backend.py   DockerSandbox（只提供 execute + 容器复用）
  container_pool.py    容器生命周期：创建 / 复用 / 探活 / 回收 / 容量上限
```

改动的既有文件：

| 文件 | 改什么 |
| --- | --- |
| `app/capabilities/backends/assembly.py` | 挂 `PlatformBackend` 包装层；沙箱带 `session_id` |
| `app/core/config.py` | `SANDBOX_IDLE_TIMEOUT`、`SANDBOX_MAX_CONTAINERS` |
| `app/main.py` | 启动时清理残留容器、后台空闲回收任务、关闭时回收 |
| `app/agents/prompts.py` | 准则 8：说明 execute 的环境语义 |

**不动的**：记忆模块、RAG 层、能力层的执行流水线、上下文模块。

---

## 9. 配置项

| 配置 | 默认 | 说明 |
| --- | --- | --- |
| `SANDBOX_IDLE_TIMEOUT` | 1800 | 容器空闲多少秒后回收（秒） |
| `SANDBOX_MAX_CONTAINERS` | 32 | 同时存活的容器上限 |

---

## 10. 已知会踩的坑

1. **别去掉 route `"/"`**。它现在是"文件操作走宿主"的保证。去掉之后
   `DockerSandbox` 的文件方法会被调用 —— 而它们的路径空间是错的
   （虚拟路径 vs 容器内 `/workspace`）。B0 的 `NotImplementedError` 就是防这个。

2. **别以为 `artifacts_root` 是路由**。它只是路径前缀，那些路径仍按普通
   路由走 —— 没有对应 route 就会掉进 `default`（沙箱）。

3. **别用 `virtual_mode=False`**。那是完全无隔离的，路径能直接穿出去。

4. **`StateBackend` 别为了"省事"接上**。文件进 checkpoint，体积爆炸，而且
   每次 edit 重写全份。

5. **别指望 `export` 和 `cd` 跨命令保留**。`docker exec` 每次都是新进程 ——
   容器复用保住的是**文件系统**（装好的包、生成的文件），不是 shell 状态。
   要设变量就写在同一条命令里（提示词准则 8 说了）。

6. **`docker exec` 与 `docker run` 的挂载语义要一致**。`-w` 指定的目录必须
   在挂载点内，否则 exec 的工作目录不存在。每次 exec 都用 `-w <mount>` 回到
   工作区根 —— 上一轮的 `cd` 不会带过来。

7. **`sleep infinity` 必须用 `sh -c` 包一层**。docker 直接执行命令时**不走
   shell 的 PATH 解析**，会报 "executable file not found in $PATH"。

8. **容器名要按 docker 规则过滤**。`[a-zA-Z0-9][a-zA-Z0-9_.-]*` —— 会话 ID
   里有中文或斜杠时会不合法。

9. **复用容器前要校验挂载点**。容器可能是上次运行建的、挂着旧工作区 ——
   挂载不对的话 agent 写的文件会落到别人的目录里，静默但危险。


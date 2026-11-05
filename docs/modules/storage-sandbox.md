# 存储与沙箱

> 一句话：文件操作在宿主上做（快），命令执行在 docker 容器里做（隔离），
> 容器按会话复用（环境不归零）。
>
> 前端入口：纯后端，无独立页面。文件页（`/files`）看到的是这个模块的产物。

---

## 1. 这个模块是什么

deepAgents 把"文件从哪来、命令在哪跑"抽象成 **backend**。本平台用两个：

```text
CompositeBackend(
    default = DockerSandbox(会话工作区),          # execute 永远走 default
    routes = {
        "/":          FilesystemBackend(工作区),   # 文件操作：宿主本地
        "/skills/":   FilesystemBackend(技能目录), # 技能导出目录（工作区外）
        "/uploads":   FilesystemBackend(上传件),   # 绑了环境后的上传件（会话目录）
        "/.memory":   FilesystemBackend(会话目录), # AGENTS.md（会话目录）
        "/.artifacts/": FilesystemBackend(产物目录),# 对话历史外置（工作区外）
    },
    artifacts_root = "/.artifacts/",
)
```

**为什么这么分工**：

| | 文件操作 | 命令执行 |
| --- | --- | --- |
| 落在哪 | 宿主 `FilesystemBackend` | docker 容器 |
| 为什么 | 快（普通文件操作）；路径已经虚拟（`virtual_mode` 用的就是虚拟路径，天然一致）；工作区是真实的，用户在文件管理器里就能看到 agent 改了什么 | 平台执行的是 LLM 生成的代码，**隔离是安全底线不是可选项** |

两个必须成立的一致性：

1. **容器只挂载会话工作区** → 容器里写的文件落在同一个工作区目录 →
   `execute` 造出的文件随后能被文件工具读到。
2. **文件操作的根 = 命令的 cwd** → agent 不用在两套路径概念之间来回换。

---

## 2. 接口

这个模块没有对外 HTTP 接口 —— 它是 agent 组装时的一环。

```text
app/capabilities/backends/
  assembly.py          组装（挂 PlatformBackend 包装层）
  platform_backend.py  包装层：ls("/") 补虚拟目录项 + 转发 execute
  sandbox_backend.py   DockerSandbox（只提供 execute + 容器复用）
  container_pool.py    容器生命周期：创建 / 复用 / 探活 / 回收 / 容量上限
```

| 配置 | 默认 | 说明 |
| --- | --- | --- |
| `SANDBOX_TYPE` | `docker` | `docker` 或 `local`。`local` **没有任何隔离**，仅限本地开发 |
| `SANDBOX_IMAGE` | `python:3.13-slim` | 容器镜像 |
| `SANDBOX_TIMEOUT` | 60 | 单次执行超时（秒） |
| `SANDBOX_IDLE_TIMEOUT` | 1800 | 容器空闲多少秒后回收 |
| `SANDBOX_MAX_CONTAINERS` | 32 | 同时存活的容器上限 |

`local` 模式用 deepAgents 的 `LocalShellBackend`（`subprocess.run(shell=True)`，
**无隔离**）。`docker` 模式用我们的 `DockerSandbox`。

**沙箱不可用时不静默降级** —— 宁可让 agent 直接失败，也不要让它在没有隔离的
环境里执行命令。`build_backend` 直接抛异常，调用方（agent 组装）负责翻译成
可读的提示。

---

## 3. 实现要点

### 3.1 隔离参数

```text
docker run -d --name qzd-sbx-<会话> \
    --network none \            # 断网，阻断数据外泄
    --memory 512m \
    --cpus 1 \
    --pids-limit 256 \
    --cap-drop ALL \
    --security-opt no-new-privileges \
    -v <工作区>:/workspace -w /workspace \
    <镜像> sh -c "sleep infinity"
```

`sleep infinity` **必须用 `sh -c` 包一层** —— docker 直接执行命令时**不走 shell
的 PATH 解析**，"sleep infinity" 会被当成可执行文件名去找，找不到就报
`executable file not found in $PATH`。

### 3.2 容器按会话复用

原来是 `docker run --rm` —— **每次执行都是新容器**。后果是 agent 的工作环境
每次归零：

```text
第一次: export FOO=hello; cd /; pwd   ->  /  hello
第二次: echo $FOO=$FOO; pwd            ->  FOO=（空）  /path/to/ws
```

对 code agent 这是硬伤 —— 用户说"装一下 pnpm"、"设个环境变量"、"cd 到 src"，
agent 装完下一轮又没了。

复用之后容器常驻（`docker exec`，不是每次新建）：

| 保留 | 不保留 |
| --- | --- |
| 装好的包（pip / npm install…） | 环境变量（`export`） |
| 容器里生成的文件 | 当前目录（`cd`） |
| 跑起来的后台进程 | |

**环境变量与 `cd` 不保留是 `docker exec` 的固有语义** —— 每次 exec 都是新
进程，进程内的变量与工作目录带不到下一次。要留就得改成"持久 shell + 管道"
（复杂，且 shell 状态跨请求容易失控），当前不做。

所以 agent 装依赖、跑构建这类操作是有效的；但"设个环境变量，下一条命令要用"
这种**同一条命令内**的用法才成立 —— agent 得把 export 和使用写在同一条命令里。
系统提示词有一条准则专门说明这个语义。

**"装好的依赖保留"才是 code agent 最需要的** —— agent 装完 pnpm，下一轮还能用。

### 3.3 生命周期

```text
execute()
  → 取会话的容器（没有就建，建之前先探活）
  → docker exec <容器> sh <脚本>
```

| 事件 | 动作 |
| --- | --- |
| 第一次用到某会话 | `docker run -d` 建常驻容器 |
| 再次使用 | `docker exec` 复用 |
| 空闲超过 `SANDBOX_IDLE_TIMEOUT` | `docker rm -f` 回收 |
| 执行前探活发现容器没了 | 删掉重建 |
| 平台启动 | 清理上次残留的容器（按名字前缀 `qzd-sbx-`） |

**为什么不按"会话结束"回收** —— 平台里**没有"会话结束"这个事件**（没人调
`adelete_thread`，没有会话表，没有归档）。跟记忆模块的短期记忆是同一个问题。
所以靠**空闲超时**：一段时间没人用就回收。这是唯一可靠的信号。

**探活结果缓存 5 秒**（`PROBE_TTL_SECONDS`）—— 探活是一次 docker 子进程
（二十多毫秒），一轮对话里跑几条命令就是几次进程启动。几秒钟足够短：容器
真被外部删了，紧接着的 exec 会失败，那时再重建也来得及。

**复用容器前要校验挂载点** —— 容器可能是上次运行建的，挂着**旧工作区**。
挂载不对的话 agent 写的文件会落到别人的目录里 —— 静默但危险。所以每次复用前
查一次容器的挂载源，与当前工作区对不上就重建。

### 3.4 并发

同一会话的多个请求**串行**执行（一个 `asyncio.Lock`）—— 容器里共享 cwd 和
环境变量，并行会打架。不同会话各自一个容器，可以并行。

### 3.5 挂载预检

macOS 上 Docker Desktop / Colima 运行在 Linux 虚拟机里，**只有被显式共享的
目录**才能 bind mount 进去（Colima 默认共享 `$HOME`）。路径没共享时，
docker **不报错**，而是挂一个**空目录**进去 —— 容器里的命令就在一个空工作区
里跑，报错信息还特别误导（`cannot open /workspace/xxx.sh: No such file`）。

所以执行前先放一个标记文件，进容器确认它看得见；看不见就明确报"工作区没有
挂载进容器"并给出解决办法。

**预检结果按 (工作区, 镜像) 共享** —— 同一个工作区 + 同一个镜像，挂进去的
结果不会变，没必要每次命令都再验一遍。

**预检在会话容器里验，不另起一次性容器** —— 以前这里要 `docker run --rm` 起
一个临时容器（一百多毫秒），验完就删，紧接着还要再起一个常驻容器 —— 两次
容器创建全是白花的钱。

**标记文件要重试几次** —— 容器复用时标记文件是刚写到宿主上的，macOS 的
Docker Desktop 要靠文件共享把它同步进容器，不是立刻的。

> ⚠️ **本机（macOS + Colima）要跑沙箱，`WORKSPACE_ROOT` 必须放在 `$HOME` 下。**

### 3.6 `DockerSandbox` 只提供 execute

deepAgents 的 `BaseSandbox` 的文件方法（`ls`/`read`/`grep`/`glob`）是**在沙箱里
跑命令**实现的，路径**原样**进容器。而我们的容器里工作区挂在 `/workspace` ——
虚拟路径 `/a.txt` 在容器里**根本不存在**（那是容器的根目录）。

**今天为什么没炸** —— route `"/"` 把所有文件操作都截给了 `FilesystemBackend`，
`DockerSandbox` 的文件方法是**死代码**。一旦哪天去掉 route `"/"`（比如为了
让沙箱统一处理文件），立刻全线崩溃。

所以 `DockerSandbox` 的文件方法全部覆写成 `NotImplementedError` —— **响亮地
失败**，而不是静默给出"文件不存在"。这是有意的边界，不是没实现。

同理，`upload_files`/`download_files` 实现的是**工作区相对**路径，与沙箱内的
`/workspace/...` 是另一套基准 —— 三方不一致，所以不实现。

### 3.7 `ls("/")` 补虚拟目录项

agent 用 `ls /` 探索工作区时，看不到 `/skills/` 和 `/.artifacts/` —— 而
`glob`/`grep` 却能搜到它们。**同一个后端集合，三种操作三种可见性，模型会困惑**。

`CompositeBackend` 的聚合行为（实测）：

| 操作 | path 为 None 或 `"/"` 时 |
| --- | --- |
| `ls` | 只搜 default + 命中 `/` 的那个 route —— **看不到** `/skills/`、`/.artifacts/` |
| `glob` | 搜 default + 所有 route，路径重映射回前缀 |
| `grep` | 搜 default + 所有 route |

原因：`route "/"` 命中后走的是"单 backend 分支"，root 聚合分支不可达。

修法是包一层 `PlatformBackend` —— `ls("/")` 时把 `/skills/`、`/.artifacts/`
作为目录条目补进结果。

> 另一个思路是把 skills/artifacts 做成工作区里的**真实子目录**，用 `.qzd/`
> 前缀藏起来。但那样 agent 就能看见目录名 —— 反而更糟。

### 3.8 glob / grep 的结果去重

`CompositeBackend.glob` / `grep` 在 path 为 `"/"` 或 None 时搜**默认后端 +
所有路由**。而默认后端（沙箱）与 route `"/"`（宿主 `FilesystemBackend`）
**指向同一个工作区** —— 每个文件出现两次：

```text
glob('**/*') -> ['/a.py', '/a.py', '/b.md', '/b.md']
grep('x')   -> ['/a.py', '/a.py']
```

agent 用 glob 找产物时会看到一堆重复项。已在 `PlatformBackend` 里去重
（按 path + 行号 / path + 行号 + 行文本），并加了回归测试。

### 3.9 `PlatformBackend` 必须继承 `SandboxBackendProtocol`

deepAgents 的 `supports_execution()` 用 `isinstance` 判断后端能不能执行命令：

```python
if isinstance(backend, CompositeBackend):
    return isinstance(backend.default, SandboxBackendProtocol)
return isinstance(backend, SandboxBackendProtocol)
```

包一层之后 `PlatformBackend` 两个都不是 —— execute 工具会直接报"后端不支持
执行"。所以它继承 `SandboxBackendProtocol` 并转发 `id` / `execute`。

### 3.10 `artifacts_root` 不是路由

`artifacts_root` 只是给 middleware 用的**路径前缀**：
`{artifacts_root}/conversation_history/`、`/large_tool_results`、`/blobs`。
这些路径随后按**普通路由**走 —— 所以 `artifacts_root` **必须配对应 route**，
否则掉进 `default`（= 沙箱）。

本平台用 `/.artifacts/` + 对应 route，语义正确。

**为什么不能是 `/`** —— deepAgents 把**被压缩掉的对话历史**写到
`{artifacts_root}/conversation_history/`。默认的 `/` 会落进工作区 —— 用户在
工作区里就能看到自己的对话历史（含脱敏可能遗漏的内容），而且可能被
`git commit` 一起提交上去。

所以它指到工作区**外面**的目录，跟技能导出目录同理：agent 的文件工具不该
看见这些。

> ⚠️ 另有一个坑：deepAgents 算 `artifacts_root` 用
> `isinstance(backend, CompositeBackend)`，而 `PlatformBackend` 只是**包着**
> CompositeBackend、不是它的子类 —— 判定为 False，产物目录退化成 `/`。
> `context/compaction.py` 的 `_fix_artifacts_root()` 显式把它指回去。

---

## 4. 已知限制

1. **容器复用会积累状态**。常驻容器里装了依赖、留了临时文件、可能还有后台
   进程。以前"每次新容器"顺带清掉这些，现在不会了。
   - 对策：容器空闲回收（默认 30 分钟）+ 容器数上限（默认 32）+ 启动时清理
     残留。**工作区里的文件仍然归会话管**（那是用户的东西，本来就该留着）。

2. **容器复用的安全面变大**。以前每次新容器 = 干净起点；现在 agent 装的东西、
   留的凭据会跨轮次存在。
   - 对策：仍然断网（`--network none`）、仍然只挂工作区、仍然限资源。

3. **环境变量与 `cd` 不跨命令**。
   - 对策：提示词里有专门的准则说明这个语义；`cd X && 命令` 是可靠写法。

4. **工作区路径必须在 docker 的共享范围内**（macOS 上 Colima 默认共享
   `$HOME`）。路径不在共享范围时，docker 会把它当成**容器内的新目录**创建 ——
   宿主写的文件容器看不见，而且**不报错**。
   - 对策：挂载预检会明确报出来。但预检用的是「标记文件在容器里看得见吗」，
     共享范围外的路径**也能挂上**（挂的是容器内的新目录），预检会误判为通过。
   - **这条是已知缺陷**：预检检测不出"路径在共享范围外"。真要严格，得比对
     容器内看到的文件与宿主是否一致。

5. **容器名必须符合 docker 规则** `[a-zA-Z0-9][a-zA-Z0-9_.-]*` —— 会话 ID
   里有中文或斜杠时会不合法，所以先做一次安全过滤（`_UNSAFE` 正则替换 +
   截 48 字符）。

6. **`docker exec` 与 `docker run` 的挂载语义要一致**。`-w` 指定的目录必须在
   挂载点内，否则 exec 的工作目录不存在。每次 exec 都用 `-w <mount>` 回到
   工作区根 —— 上一轮的 `cd` 不会带过来。

7. **`StateBackend` / `StoreBackend` / `ContextHubBackend` 都不接**。
   - `StateBackend`：文件进 LangGraph 的 `files` channel，会被 checkpointer
     序列化 —— 用 SQLite 每个文件内容都进 checkpoint，**每次 edit 重写整份**，
     几百 KB 的文件几轮编辑后 checkpoint 就是几 MB。
   - `StoreBackend`：语义上不是文件系统（agent `write("/memories/x")` 之后
     沙箱里 `cat /memories/x` **看不见**），且与记忆模块的职责重叠 ——
     记忆模块已经解决了跨会话的"经验"存储，而且带评分带召回。
   - `ContextHubBackend`：依赖 LangSmith，平台不接。

8. **大文件（base64）没有上限**。deepAgents 的 `FilesystemBackend` 的
   `max_file_size_mb` 只管 grep 的 Python fallback，不是 read 上限 ——
   read 走分页（`offset`/`limit`），二进制按扩展名判定整份 base64 塞进
   `FileData(encoding="base64")`，**无大小上限**。

9. **本地模式（`SANDBOX_TYPE=local`）没有隔离**。它只适合本地开发。
   `local` 要**显式**打开。

---

## 5. 相关文件

| 文件 | 负责什么 |
| --- | --- |
| `app/capabilities/backends/assembly.py` | 组装（挂 PlatformBackend 包装层；路由表；artifacts_root） |
| `app/capabilities/backends/platform_backend.py` | 包装层：`ls("/")` 补虚拟目录项、glob/grep 去重、转发 execute |
| `app/capabilities/backends/sandbox_backend.py` | `DockerSandbox`：只提供 execute、隔离参数、挂载预检、脚本写入 |
| `app/capabilities/backends/container_pool.py` | 容器生命周期：创建 / 复用 / 探活 / 回收 / 容量上限 / 启动清理 |
| `app/capabilities/execution/context.py` | `ExecutionContext`：工作区、会话工作区、沙箱配置、MCP 通道 |
| `app/core/config.py` | `sandbox_*` 五项配置 |
| `app/main.py` | 启动时清理残留容器、后台空闲回收任务、关闭时回收 |
| `app/context/compaction.py` | `_fix_artifacts_root()`：把产物目录指回 `/.artifacts/` |

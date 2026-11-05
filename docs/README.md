# 功能模块文档

按**功能模块**组织 —— 一篇讲清一个模块：它是什么、接口有哪些、怎么实现的、
哪些地方改错了会出事、现在还差什么。

**只写已经做完的。** 这份文档集里没有计划、没有排期、没有"将要"。
没做的部分散在各篇的「已知限制」里 —— 那是**现状的描述**，不是待办清单。

| 模块 | 文档 | 一句话 |
| --- | --- | --- |
| 编码助手 | [modules/agent-chat.md](modules/agent-chat.md) | 唯一的对话入口：消息流、审批挂起与恢复、权限模式、分支与重新生成、子代理派活 |
| 能力与治理 | [modules/capability.md](modules/capability.md) | 所有工具调用的唯一通路：能力清单 → 意图 → 七阶段流水线 → 留痕；风险判定与人工审批 |
| 记忆 | [modules/memory.md](modules/memory.md) | 短期落表 / 长期落 RAG 两层；三层渐进式召回；写入判据与硬校验 |
| 知识库 | [modules/knowledge.md](modules/knowledge.md) | RAG 服务层：集合 / 文档 / 分块、混合检索；也作为只读能力暴露给 agent |
| 上下文装配 | [modules/context.md](modules/context.md) | 块 + 优先级 + 预算 → 系统提示词；每轮动态注入；压缩与历史外置 |
| 文件 | [modules/files.md](modules/files.md) | 上传件 / 工作产物 / 平台产物三类文件；对话里的多模态输入 |
| 工作目录 | [modules/workspace.md](modules/workspace.md) | 白名单、会话绑定、git worktree；子 agent 的独立检出 |
| 模型接入 | [modules/providers.md](modules/providers.md) | 供应商配置与密钥加密；MCP 服务器接入 |
| 存储与沙箱 | [modules/storage-sandbox.md](modules/storage-sandbox.md) | 文件操作在宿主、命令执行在容器；容器按会话复用 |
| 自我进化 | [modules/evolution.md](modules/evolution.md) | 失败 → 复盘 → 改进落进记忆 / 技能 / 提示词；三层都可撤销 |

**写新文档时**看 [writing-conventions.md](writing-conventions.md) —— 里面是这份
文档集的语气约定、结构模板、**框架的红线**（哪些框架件不能引进、为什么），
以及**跨模块共享的事实**（权限模式、审批的两级、风险分档、沙箱、记忆分层），
每篇都要对得上。

---

## 怎么读

**想知道某个功能怎么用** → 看对应模块的「接口清单」和「实现要点」。

**想改代码** → 先看那一篇的「已知限制」和散落在「实现要点」里的**不变量**
（"改错了会怎样"）。那些不是设想，是**实测复现过的**。

**想知道全平台的风险** → [../backend/README.md](../backend/README.md) 顶部的
「⚠️ 风险（上线前必须处理）」。**平台目前没有鉴权**，这是所有模块共同的前提。

---

## 三个贯穿全平台的约定

### 权限模式

会话级，语义与 Claude Code 对齐：`auto`（默认，按风险档位）、`manual`（只读
不问）、`accept_edits`（只剩破坏性要人批）、`plan`（写类与执行类工具不暴露
给模型）、`bypass`（全放行，受进程级总闸约束）。

**判定只有一个函数** —— `app/capabilities/registration/policy.py::needs_approval()`。
中间件告诉模型的那段说明、执行层的判定、审批闸的谓词，三处都用它。三处各写
一套，就是"批准过了又被拦一次"的来源。

### 审批的两级

| 级 | 看什么 | 挂起点 |
| --- | --- | --- |
| 声明级 | 工具本身 | 工具执行**之前**（deepAgents 的 HITL） |
| 参数级 | 这次调用的参数 | 工具执行**中**（我们自己的 `interrupt()`） |

**一次调用只让人批一次** —— 声明级先于工具执行发生，工具能跑到参数级检查，
说明人已经批过了。

### 风险分档

`read` / `write` → **低**（自动放行）；`execute` / `network` → **中**（要人批）；
`destructive` → **高**（永远要人批，只有 `bypass` 放它）。

参数级规则**只能上调风险，不能下调**。

---

## 相关的其它文档

| | |
| --- | --- |
| [../README.md](../README.md) | 仓库总览：起服务、目录结构、两条要记住的坑 |
| [deployment.md](deployment.md) | **Docker 部署**：两个容器、工作区路径的硬约束、docker socket 的安全边界、常见问题 |
| [../backend/README.md](../backend/README.md) | 后端：分层、目录、配置、迁移、风险 |
| [../frontend/README.md](../frontend/README.md) | 前端：起服务、主题、组件、约定 |

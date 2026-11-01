# 文档索引

后端文档按**状态**分两类，不按主题分：

| 目录 | 放什么 | 判断标准 |
| --- | --- | --- |
| [implementation/](implementation/) | **已经写进代码的**东西：模块是什么、怎么实现的、接口有哪些 | 代码里能跑通 |
| [plans/](plans/) | **还没做的**东西：已知限制、要补的能力、排期与取舍 | 还没写，或写了但不完整 |

一份文档里既有做好的又有没做好的，**拆开**：做好的进 `implementation/`，
没做的进 `plans/`，两边互相链接，不复制内容。

---

## 实现文档

| 文档 | 内容 | 状态 |
| --- | --- | --- |
| [capability-module.md](implementation/capability-module.md) | 能力模块：注册 / 意图 / 执行三层，deepAgents 工具 + 我们自己的工具，七阶段流水线，治理与审批 | ✅ 已实现（2026-11-01，deepAgents 迁移后） |
| [rag-architecture.md](implementation/rag-architecture.md) | RAG 服务层：集合 / 文档 / 分块，分块策略，向量化，混合检索 | ✅ R0 + R1 已实现（2026-11-01） |
| [memory-module.md](implementation/memory-module.md) | 记忆模块：短期落表 / 长期落 RAG，三层渐进式召回，写入判据与硬校验 | ✅ 已实现（2026-11-01） |
| [context-module.md](implementation/context-module.md) | 上下文模块：块抽象 / 配额装配 / 压缩接管（deepAgents 摘要中间件） | ✅ 已实现（2026-11-02） |
| [backend-strategy.md](implementation/backend-strategy.md) | 后端存储：deepAgents 六种 backend 的选型 + 容器按会话复用 | ✅ 已实现（2026-11-02） |

## 计划文档

| 文档 | 内容 | 状态 |
| --- | --- | --- |
| [agent-migration-deepagents.md](plans/agent-migration-deepagents.md) | Agent 层迁移到 deepAgents：能力层 → Backend、审批改 HITL、规划/子代理/skills/记忆/摘要 | ✅ 已实施（2026-11-01，含实施记录与偏差说明） |
| [capability-followups.md](plans/capability-followups.md) | 能力层的已知限制与待办（并发配额、审批过期、MCP 注解链路、鉴权…） | ⬜ 未开始（A 已解决） |
| [rag-followups.md](plans/rag-followups.md) | RAG 层待办（重排、跨集合、增量更新、本地向量模型、规模化） | ⬜ 未开始 |

---

## 约定

- 文档里的相对链接指向文件，改目录层级时要一起改。
- 状态行（`> 状态：**已实现**（YYYY-MM-DD）`）写在文档开头，
  改状态时顺手更新日期。
- 计划文档里的条目按影响面排序，A 类影响可用性，B 类影响能力覆盖面，
  C 类是数据与安全。

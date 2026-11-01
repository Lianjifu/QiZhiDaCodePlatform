"""Agent 层：模型 + 工具 + 循环 的编排。

这一层是**编排层**，不是业务层。分清这两件事很重要：

- 这一层负责：把"能力清单 + 执行环境 + 模型"组装成一张可运行的图，跑它，
  把结果整理成结构化响应；
- 这一层**不**负责：工作区怎么分配、供应商配置怎么存、审批记录怎么落库 ——
  那些是 Service 层的事（见 `app/services/agent.py`）。

约定（与整体分层保持一致）：

- Agent 层只负责"模型 + 工具 + 循环"的编排，不碰数据库；
- 会话持久化等业务职责交给 Service 层；
- 所有副作用（读文件、写文件、执行代码）都必须走 tools/，不允许模型直接拿到 shell。

---

## 模块地图

| 模块 | 管什么 | 谁调它 |
| --- | --- | --- |
| `runner.py` | **一次对话的执行器**：组装、跑图、整理结果、恢复挂起的图 | Service 层 |
| `builder.py` | **图的组装**：工具 / 中间件 / backend / 审批闸怎么拼起来 | runner |
| `llm.py` | **模型工厂**：把供应商配置翻译成 LangChain 聊天模型 | builder、Service |
| `providers.py` | **供应商预设表**：key / base_url / 协议 / 文档地址 | Service、前端 |
| `prompts.py` | **系统提示词**：只讲"怎么做事"，不讲工具细节 | builder、runner |
| `checkpointer.py` | **上下文持久化**：多轮对话靠它记住上一轮 | Service（每请求一次） |

## 一次请求走过的路

```text
POST /api/v1/agent/chat
  │
  ▼ services/agent.py::AgentService.chat
     · 分配工作区、选模型、加载能力清单、建执行上下文
  │
  ▼ agents/runner.py::AgentRunner.run
     │
     ├─ _build()
     │   ├─ 导出技能 → <工作区外>/.skills/<会话>/<技能>/SKILL.md
     │   ├─ 确保记忆 → 工作区/AGENTS.md
     │   ├─ 上下文模块装配系统提示词（app/context/：块 + 配额）
     │   └─ builder.build_agent()
     │        ├─ 我们的工具 → build_tools()（走能力层执行流水线）
     │        ├─ 文件工具   → deepAgents 自带，backend 指向我们的工作区与沙箱
     │        ├─ CapabilityMiddleware  治理：工具收敛 / 参数级风险 / 留痕
     │        ├─ 压缩中间件（接管 deepAgents 的摘要，阈值按窗口比例）
     │        ├─ interrupt_on            声明级审批（挂起等人批）
     │        └─ 自带的 general 子代理（显式挂同一套治理中间件）
     │
     ├─ agent.ainvoke(...)      模型循环：思考 → 调工具 → 结果回灌 → 再思考
     │      └─ 撞上审批 → 图挂在 checkpoint 里 → 本轮结束
     │
     └─ _result_from()  整理成 AgentRunResult（内容 / 工具调用摘要 / 状态 / 待审批）
```

## 三条硬约定

1. **图只能有一个来源** —— `builder.build_agent()` 是唯一组装图的地方。
   不存在"另一套 create_agent 实现"，也不存在第二条工具构造路径。
2. **审批只有一份规则** —— deepAgents 的 `interrupt_on`、中间件告诉模型的那
   句话、执行层的判定，用的都是 `capabilities.registration.policy.needs_approval`。
3. **模型只能通过工具产生副作用** —— 文件、命令、联网都要经过工具，
   工具再经过治理中间件（我们的工具还会再经过执行层流水线）。
"""

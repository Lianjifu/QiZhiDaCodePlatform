# 能力层待办与已知限制

> 状态：**部分已实施**（2026-11-03：B、C 已解决）
>
> 关联文档：[implementation/capability-module.md](../implementation/capability-module.md)（能力模块已实现部分）、
> [memory-module.md](../implementation/memory-module.md)（记忆模块计划）、
> [rag-followups.md](rag-followups.md)（RAG 层待办）

这份文档记录**能力模块还没做完的事**：能力模块的每个已知限制，都是一件待办。
实现细节见 implementation/ 下的能力模块文档，这里只写现状、影响和打算怎么做。

约定：条目按影响面排序，A 类影响可用性，B 类影响能力覆盖面，C 类是数据与安全。

---

## A. 结果回流要等用户说下一句 —— ✅ 已解决

**原状**：审批通过后由审批接口直接执行（不挂起 agent），结果在 agent **下一轮**
开始时才被注入上下文。

**怎么解决的**（2026-11-01，deepAgents 迁移）：审批改走 **LangGraph 中断**。

```text
模型调工具 → 命中 interrupt_on → 图挂在 checkpoint 里
           → 响应 status=pending_approval
           → POST /api/v1/agent/{thread_id}/approve
              → Command(resume={"decisions": [{"type": "approve"}]})
              → 图恢复，工具执行，agent 自己接着往下跑
```

结果直接回到 agent 的上下文，不需要"下一轮再补给它"。那套回流机制
（`intent/notify.py` + `invocation.notified_at`）随之删掉。

**遗留**：审批**状态**现在在 checkpoint 里，不在调用记录表里 —— 查待审批列表要用
`GET /api/v1/agent/{thread_id}/pending`。直接调 API 的那条路径不走图，仍然是
"pending 记录 → `POST /api/v1/invocations/{id}/approve` 执行"。

详见 implementation/capability-module.md §5.6。

---

## B. 并发配额未生效 —— ✅ 已解决

**原状**：`capability_max_concurrency`（默认 4）**只在配置里**，`app/` 下没有任何地方读它。
执行层没有并发闸门。

**怎么解决的**（2026-11-03）：加了一个**会话级**的并发闸门
（`app/capabilities/execution/concurrency.py`），在执行层的调用点
（`execution/pipeline.py` 的 ⑤ 实际调用）拿名额：

- 上限取 `capability_max_concurrency`，**按会话限**（一个会话占满名额不挡别的会话）；
- 拿不到名额就**排队等**，等 `capability_concurrency_wait_seconds`（默认 30s）；
- 等不到就按"执行层繁忙"失败（可重试），不硬等。

回归测试在 `tests/test_concurrency.py`。

**遗留**：语义量是**进程内**的 —— 多副本部署时每个副本各自计数，**不是全局配额**。
要跨副本限流得换成 Redis 之类的共享计数。沙箱容器本身也还没有全局上限
（会话数 × 单会话并发仍可能打满宿主机）。

---

## C. 审批无自动过期 —— ✅ 已解决

**原状**：`capability_approval_timeout`（默认 300s）**只在配置里**，没有后台任务去扫
`pending` 记录。

**怎么解决的**（2026-11-03）：启动时起了个后台任务（`app/main.py` 的
`_expire_stale_approvals`），周期性扫描超时的 pending 记录，置为 `expired`
（`STATUS_EXPIRED` 以前定义了但没人用）。周期取超时时间的一半。

**遗留**：只覆盖**调用记录表**里那条路。图中断（deepAgents 的 HITL）那条路
的挂起状态在 checkpoint 里，不在调用记录表里 —— 那条路靠会话被删除或
`/clear` 清理，不会自动过期。

---

## D. MCP 注解继承没打通

**现状**：`Annotations.from_mcp()` 已实现（`registration/policy.py`），但 MCP 工具
进注册表的完整链路没走通，风险等级目前人工填。

**影响**：MCP 供给通道上的能力，风险等级靠人填 = 填错就绕过治理。
MCP 工具自带 `readOnlyHint` / `destructiveHint` 这类注解，是最可靠的来源。

**打算怎么做**：把注册表的 MCP 同步链路补全 —— 探测 server 工具列表时同步读注解，
用注解推导 `annotations` 与初始 `risk`（`readOnlyHint` → read，`destructiveHint`
→ destructive，其余按现有 MCP 工具的默认口径）。人工填的值**优先**，只在没填时
用注解兜底。

**前置**：无。
**风险**：中。涉及对外暴露风险等级，改完要复核一批已有 MCP 工具的声明。

---

## E. 没有出厂内置技能

**现状**：`skill.builtin=1` 这个标记和"内置技能不允许删除"的保护都在，但没有任何
种子数据。技能全部靠用户/运营通过接口创建，检索只有关键词打分。

**影响**：新用户开箱没有可用技能；技能检索命中率为 0 时能力集退化成全量暴露。

**打算怎么做**：先补 2~3 个种子技能（覆盖文件读写、代码审查、跑测试这类常见场景），
让检索有东西可命中。检索的向量部分可以复用 RAG 层的 `Retriever`（见
[rag-followups.md](rag-followups.md)），但**不是前置** —— 关键词打分先能用着。

**前置**：无。
**风险**：低。

---

## F. 外部工具的依赖环境

**现状**：第三方脚本需要的依赖靠镜像预装；沙箱镜像不可扩展，也没有运行时装包的通路。

**影响**：用户自建能力依赖的库如果不在镜像里，跑起来就缺模块。

**打算怎么做**：两条 —— ① 把沙箱镜像从 `python:3.13-slim` 换成带常用科学计算 /
构建工具的自定义镜像，仍走预装路线；② 给沙箱加运行时装包（pip install 到会话
工作区），要处理离线缓存和权限。

**前置**：无。
**风险**：中。①只影响镜像体积和构建流程；②在沙箱里放开包安装等于放开网络，
得先确认沙箱的网络策略（现在 docker 模式是 `--network none`）。

---

## G. 待审批记录存明文参数

**现状**：`invocation.payload` 存的是**原始参数**（批准后要按它执行），库里是明文。

**影响**：带密钥的命令（`export TOKEN=...`）会明文落库。`capability_redact_patterns`
能脱敏一部分，但 `payload` 这条路目前没走脱敏。

**打算怎么做**：写入前对 `payload` 做脱敏 —— 但**不能直接脱敏后再执行**，否则批准后
执行的参数跟模型发起的原参数不一致。要么存密文（Fernet，`api_key_encryption_key`
已经在用同一套），要么存脱敏副本 + 加密原文两列。

**前置**：无。
**风险**：中。属于存量数据的变更 —— 已经落库的明文 payload 要不要清洗，
取决于是否接受"历史数据不动、新数据加密"。

---

## H. 能力接口无鉴权

**现状**：`/capabilities`、`/invocations`、`/tools`、`/skills`、`/mcp-servers`
目前都没有认证。

**影响**：能调工具管理接口的人可以让平台 import 并执行任意 Python 代码
（`source` 字段的白名单校验只挡了越界的写法，接口本身仍然没有身份）。

**状态**：**用户已明确推迟鉴权**（当前阶段不做）。风险靠 `source` 白名单、
路径沙箱、`risk` / `permission` 声明和人工审批这几层挡着 —— 不是零风险，
只是暂时接受。

**打算怎么做**：等鉴权方案定了再动。真要做时，优先级排在
"能力调用/审批接口"上 —— 那是能触发副作用的那部分。

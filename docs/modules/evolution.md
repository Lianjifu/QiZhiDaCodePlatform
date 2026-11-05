# 自我进化 / 复盘闭环

> 一句话：agent 每一轮失败之后，平台在后台跑一次复盘，让模型答「错在哪、下次怎么改、
> 这条改进属于哪一层」，然后把改进落进**记忆 / 技能 / 提示词**三层之一 —— 并且因为
> 免审批，配了三道闸（范围守卫、全程留痕、事后可撤销）。
>
> 前端入口：`/retrospectives`（`frontend/src/pages/RetrospectivesPage.tsx`）——
> 复盘记录列表 + 撤销 + 度量；页面顶部还有一张「长期记忆」卡片
> （`frontend/src/features/autonomy/LongTermMemoryCard.tsx`）。

## 1. 这个模块是什么

### 1.1 闭环长什么样

```text
一轮结束
   │
   ├─ 这一轮有失败吗？
   │     没有 → 什么都不做（绝大多数轮次走这条，零 LLM 调用）
   │     有   ──→ 冷却期过了吗？
   │                 没有 → 什么都不做
   │                 有   ──→ 抢占这批失败（CAS）
   │                            ├─ 没抢到 → 别人在复盘，空跑
   │                            └─ 抢到 → 调模型 → 解析 → 守卫 → 落地 → 记账
   │
   └─ 落进哪一层？
         记忆 → 下次召回时它在场
         技能 → 下次检索技能时它在场
         提示词 → 下一轮的上下文里多一块「自动积累的补充准则」
```

「失败 → 经验」这条链接上之后，才有「下次做得更好」。这一篇讲的就是这条链。

### 1.2 三层载体，各管各的

| 载体 | 落点 | 边界 |
| --- | --- | --- |
| **记忆** | `MemoryService.remember(scope=long_term, source=model-inferred)`（`SOURCE_MODEL` = `"model-inferred"`） | 长期集合没建好就降级成会话内 |
| **技能** | 追加到已有技能的 `knowledge`；没有匹配就新建 | **碰不到技能的工具集** —— 它不能给 agent 加能力 |
| **提示词** | `prompt_rule` 表 → 上下文里的独立块（优先级 22） | 代码里的 `CODE_AGENT_PROMPT` 永远在前、永远完整 |

**复盘模型只判断"属于哪一层"，不决定要不要改** —— 那是平台的事。
载体不在三种之内、正文是空的，都算没有有效产出（`app/agents/autonomy.py::normalize_proposal`
直接返回 `None`）。

三层各有各的撤销方式：

| 载体 | 撤销怎么做 |
| --- | --- |
| 记忆 | 删掉那一条（`MemoryService.forget_any`） |
| 技能 | 把追加进 `knowledge` 的那段原文删回去 |
| 提示词 | 把那条规则停用（`enabled=False`，不进提示词了） |

### 1.3 与反思记录、记忆模块的关系

复盘不是反思。反思记录（`app/agents/reflection.py`）是**只读**的一层：把最近的失败
做成 `<recent_failures>` 块注入下一轮上下文，让 agent "知道自己刚摔过"。它不进任何
存储、不会改变 agent 的行为。

复盘是**写**的一层：它把改进落进三层存储，下一轮真的不一样。

两者的**失败判定**共用同一份：`app/models/invocation.py::is_failure`。

### 1.4 复盘用犯错的同一个模型

复盘调用的是这一轮 agent 用的模型对象（`AgentRunner._resolve_model()`）——
犯错的同一个模型来复盘，判断力与上下文一致。

> `settings.autonomy_model`（`app/core/config.py:253`）留了一个"用便宜模型复盘"的口子，
> 但**当前代码没有读它** —— 复盘始终走主模型。配置项存在不等于生效。

## 2. 接口清单

`app/api/v1/retrospective.py`，router prefix `/retrospectives`，总前缀 `/api/v1`。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/retrospectives` | 查询复盘记录。query：`thread_id` / `status` / `carrier` / `page` / `page_size`（≤100）。**新的在前**（按 id 倒序） |
| GET | `/retrospectives/metrics` | 度量统计。query：`thread_id`。返回 `by_status` / `by_reject_reason` / `total` / `recurrence` |
| GET | `/retrospectives/{record_id}` | 单条详情。不存在返回 404 |
| POST | `/retrospectives/{record_id}/revert` | 撤销这次复盘做的改动 |

**平台没有"预览某条复盘会落地成什么"的接口** —— 复盘记录的查看只有上面这四个
（列表、度量、详情、撤销）。前端 `frontend/src/api/resources.ts` 里也没有对应的调用。

**没有鉴权**（平台级的既有决策，见 `backend/README.md` 风险段第 4、5 条）。所以这四个
接口**任何人都能调**：能读全部复盘记录，能撤销别人的改进。撤销比只读高一档 ——
它能让 agent 的提示词规则失效、技能的知识少一段、记忆少一条。鉴权补上之前，
这个接口不要暴露到公网。

长期记忆集合的初始化不在本模块（属记忆模块）：`GET /api/v1/memory/long-term`
与 `POST /api/v1/memory/long-term`。进化记录页顶部的卡片调的就是这两个。

## 3. 实现要点

### 3.1 触发：三个条件全满足才复盘

| 条件 | 在哪判 | 为什么 |
| --- | --- | --- |
| 本轮**跑完了**（`status == completed`） | `app/agents/runner.py::_result_from` / `_stream_turn` | 挂起审批时跳过 —— 那一轮还没结束，用户批完还要 resume 接着跑，跑了就重复计一次 |
| 有**未复盘**的失败 | `RetrospectiveService.run` → `list_uncovered_failures` | 没失败就没有可复盘的东西 |
| 距上次复盘**超过冷却期** | `RetrospectiveService._in_cooldown`（默认 300 秒） | "一次连着失败十次就复盘十次" —— 那是同一件事，一次就够 |

绝大多数轮次没有失败 → **零额外 LLM 调用**。

冷却期还有一条：**上一条复盘的状态是 `running` 也算在冷却期内**（那说明另一个复盘
任务正在处理这一批失败，这次不用再跑一遍）。

还有一个**回看窗口**：`autonomy_lookback_seconds`（默认 3600 秒）—— 只看这么长时间内的
失败，更早的失败多半是老账，翻出来只会让复盘跑题。

### 3.2 挂在哪：两个收尾点，不在 service 层

| 挂点 | 覆盖哪条路 |
| --- | --- |
| `AgentRunner._result_from`（`app/agents/runner.py:427`） | `run`（新消息）与 `resume`（审批恢复） |
| `AgentRunner._stream_turn` 尾巴（`app/agents/runner.py:542`） | `stream` 与 `regenerate` |

**不在 service 层** —— 那要挂四处（chat / resume / stream / regenerate 各来一次），
漏一处就少一条路。挂在 runner 的两个收尾点上是全的。

### 3.3 后台跑，不拖慢这一轮

复盘在 `asyncio.create_task` 里跑，不阻塞用户这一轮的响应。

两个必须注意的地方：

- **后台任务自己开全部的数据库会话**（`run_retrospective_in_background` 里的
  `SessionLocal()`）—— 触发它的那个 HTTP 请求的 session 随后就关了，复盘全程不碰它。
- **必须留一个强引用**（`_BACKGROUND_TASKS.add(job)` + `done_callback` 里 discard）。
  事件循环只持有弱引用，不留着的任务可能在中途被垃圾回收 —— 复盘刚排上就没了，
  而且**没有任何报错**。

`run_retrospective_in_background` **从不抛异常** —— 它跑在后台任务里，异常会变成
"任务异常结束"的日志，用户那边什么也看不到，还以为复盘跑了。

### 3.4 抢占：先抢占，再复盘

两个并发请求可能各起一个复盘任务，捞到**同一批**失败 —— 两份复盘、两次模型调用、
两轮记忆写入。所以必须先抢占：

1. 查候选失败（未复盘 + 在回看窗口内）
2. 建 `retrospective` 行（`status=running`）拿到 id
3. **原子抢占** `UPDATE invocation SET retrospective_id=:id WHERE id IN (候选) AND retrospective_id IS NULL`
4. 受影响行数为 0 → 已被别人抢走 → 把自己那行删掉直接结束
5. 行数小于候选数 → 只处理抢到的那几条（其余归别人）

第 3 步是**比较并交换** —— 条件里的 `retrospective_id IS NULL` 保证一条失败只会被
一个复盘认领。原子性来自这一条 UPDATE 本身：两个并发事务里只有一个能把
`retrospective_id` 从 NULL 改成非 NULL，另一个的受影响行数是 0。

> 条件更新只给行数，**不告诉"是哪几条"** —— 所以还要再查一次
> （`RetrospectiveRepository.list_claimed`），候选里指向这次复盘的就是抢到的。
> 抢不到的那几条归了别人，不该出现在这次复盘里。

**抢了不还回去是自伤**：复盘自己没跑成时（`_produce` 抛异常），必须
`release_failures` 把 `retrospective_id` 置回 NULL —— 否则那批失败永远不会被复盘。
还回去之后下一轮会重新捡起它们，所以**复盘失败是自愈的，不必重试机制**。

### 3.5 `invocation` 表加了一列 `retrospective_id`

Integer 索引，**不加外键** —— 避免与 `retrospective` 互相引用成环（删表顺序就定不下来）。
删复盘时把它置空即可。

它既是**去重依据**（没复盘过的失败才该复盘），也是**"同类失败复发率"的计算依据**。

### 3.6 一个容易漏的判定：退出码非零也是失败

退出码非零的调用在 `invocation` 里是 `succeeded` —— 工具履行了"跑代码并把退出码带回来"
的职责。但从 agent 的角度那次尝试就是失败了：它想算的东西没算出来。

只按状态筛的话，复盘**在代码 agent 最常见的失败场景里是空的**。

判定因此放在 `app/models/invocation.py::is_failure` 一处：

```python
if getattr(record, "status", "") in FAILURE_STATUSES:
    return True
return exit_code_of(record) not in (None, 0)
```

`FAILURE_STATUSES` = failed / blocked / denied / expired。

**反思块、复盘的候选筛选、复发率统计三处共用** —— 放在一处，它们才不会各说各话。

> `list_uncovered_failures` 因为这条判定**不能在 SQL 里筛**（退出码在 `result` JSON 里），
> 所以多取一些（`scan` 条，默认 60）再在 Python 里筛 —— 免得最近的几条恰好都是成功的。
> 取出来之后**按时间从早到晚排**：复盘一次最多看 `MAX_FAILURES`（10）条，早的先复盘。
> 反过来（取最新的）会让水位线跳过去，中间没复盘过的就永远漏了。

### 3.7 复盘问模型什么

给三样东西（`app/agents/autonomy.py::build_input`）：

1. **任务描述** —— 这一轮的用户消息。只有 resume 没有新消息时，回退到
   `_last_user_message` 读 checkpoint 里最后一条用户消息。
   拿不到任务描述，复盘就只能说"某个命令挂了" —— 那种改进没有价值
   （它答不出"这次想做什么、错在哪"）。
2. **这批失败的细节** —— 能力名、参数摘要、错误码 / 报错 / 退出码。
   参数可能很长（整段代码），截到 200 字；任务描述截到 500 字。
3. **已有的相关记忆** —— 避免重复记。

问三个问题：**错在哪**（根因，不是"命令失败了"）、**下次怎么改**（一条具体做法）、
**改在哪一层**。

输出 JSON：`has_insight` / `summary` / `carrier`（memory|skill|prompt）/ `title` /
`content` / `kind` / `tags` / `target_skill_id` / `capability_keys` / `reason`。

`has_insight=false` → 不产生改进，但仍记一条 retrospective（`status=no_insight`）——
"复盘过、没发现可改进"本身是度量的一部分。

### 3.8 JSON 解析容错

模型可能包 markdown 代码块、前面带一句"好的，我的建议是："、或者干脆少给一个字段。
提取器（`parse_proposal`）的顺序：

1. 剥 ```` ``` ```` 围栏
2. 找第一个 `{` 到最后一个 `}` 截一段
3. `json.loads`

解析不出来 → `status=rejected, reject_reason=parse_failed`，**不算失败、不重试**。
解析失败不该让整轮对话挂掉。

### 3.9 三道闸（免审批的代价）

用户明确选了"三层全做、免审批" —— 没有事前把关。代之以三道闸。

#### 闸一：范围守卫

自生成的改进若想改**审批 / 权限 / 沙箱**那一层，一律不应用，只记录。

| 口径 | 用在哪 | 关键词组 |
| --- | --- | --- |
| **严** | 提示词、技能 | `SAFETY_SCOPE_KEYWORDS`：审批 / 批准 / 权限 / 授权 / 绕过 / bypass / 沙箱 / 鉴权 / 免确认 / 破坏性 |
| **宽** | 记忆 | `LOOSENING_PATTERNS`：绕过 / bypass / 免确认 / 不用人批 / 不需要人批 / 无需批准 / 不需要批准 / 不经过审批 / 跳过审批 / 直接执行不需 / 不要询问用户 |

口径为什么不一样：

- 提示词与技能是**两段"照着做"的内容**，提到审批就是在教 agent 松绑 —— 宁可误伤。
- 记忆是**背景资料**（目录里明写着"不是规则"），而"这个仓库的提交流程要先在钉钉上提工单"
  这种记忆提到审批是合理的 —— 用同一把尺子量它，误伤面太大。记忆只拦**明确在放松约束**的说法。

**宁可误伤** —— 漏判的代价（agent 改掉自己的安全规则）远大于误判的代价
（一条无害的规则没自动上，人手动加即可）。

被拦下的记录照样落库：`status=rejected, reject_reason=safety_scope`，带着 `content`
与 `reason`，人能看见它想干什么。

#### 闸二：全程留痕

每次复盘与应用都落 `retrospective` 表：改了什么（`carrier` + `title`/`content` +
`target_id`）、为什么改（`reason`，模型给的理由）、成功没有（`status` +
`reject_reason`）、依据是哪些失败（覆盖的调用记录行会把自己的 `retrospective_id`
指回这里）。

#### 闸三：事后可撤销

三层都能撤（见 §1.2）。只有 `status == applied` 的记录能撤 —— 没落地没什么可撤。
撤销成功 → `status=reverted`。

> 撤销技能那段有个保护：`appended_block`（追加进 `knowledge` 的那段原文）不在当前
> `knowledge` 里了就不撤 —— 别人后来改过这一段，硬删会伤到别人的内容。

**撤销记忆必须走 `_memory_service()`**（`services/retrospective.py`），不能自己造
一个 `MemoryService`。`forget_any` 按 id 前缀判 scope（`l:` → 长期），长期存储没接上
就直接返回 False —— 撤销报"目标已经不在了"，可它明明在。

这条不是设想：**2026-11-05 实测发现的**。当时 `_revert_memory` 自己造服务、没传
`long_term_store`，于是**长期记忆那层的撤销在生产里是坏的** —— 而复盘默认就写长期
（`_apply_memory` 把 `SCOPE_LONG_TERM` 排在 `SCOPE_SESSION` 前面），所以坏的正是
最常走的那条路。会话内的改进（`s:` 前缀）撤销正常 —— 旧测试覆盖的只有这一条，
所以这个洞在测试里看不见。

回归测试：`tests/test_autonomy.py::test_撤销会删掉落在长期记忆里的那条改进`。

### 3.10 落地的三个细节

**记忆**：优先写**长期**（跨会话）—— 一次任务的经验下次也要能用。长期集合没建好就
降级成**会话内**（不是丢掉）：一条改进因为这个丢了才亏。

写记忆有两个拒绝路径：
- 与已有记忆**矛盾**（同类型 + 有共同标签但内容不同）→ `reject_reason=conflict`，
  不硬写。会话内的旧记忆一样会跟它撞，所以不再试下一个 scope。
- 集合不存在之类的其它错 → 换一个 scope 再试（长期 → 会话内）。

**技能**：只动 `knowledge`，**不碰 `tool_ids`** —— 技能是"一组工具 + 怎么用"，
复盘能改的只有后半截。要是它能塞工具，就是自己给自己扩权。

新建技能时 `tool_ids` 留空。

**技能追加有上限**：`knowledge` 有 8000 字上限（`SKILL_KNOWLEDGE_LIMIT`）——
追加前先看还装不装得下，装不下就**不追加**（`status=rejected, reject_reason=limit`）。
宁可这条改进不落，也不要把技能撑爆或截掉已有内容。

追加的那段带固定标记（`SKILL_BLOCK_TEMPLATE` = `"\n\n【复盘补充】{content}\n"`）——
撤销时按它删回去。`update_skill` 只传 `knowledge`，**不传 `tool_ids`**。

**提示词**：落 `prompt_rule` 表（`enabled=True`，带上 `retrospective_id`）。
规则**不是**直接进代码里的提示词 —— 它进的是上下文装配的一个独立块
（`app/context/service.py`，`PRIORITY_PROMPT_RULES = 22`）。

### 3.11 提示词规则怎么进上下文

`app/agents/prompts.py::render_prompt_rules` 渲染成一个带边界的块：

```text
<auto_rules>
自动积累的补充准则：（只影响做事方式，不影响权限与审批）
- 改完代码先跑 lint 再跑测试
</auto_rules>
```

那行"只影响做事方式"是对模型再说一遍 —— 范围守卫已经拦掉了想改审批 / 权限那一层的
规则，这行字是把边界讲清楚。

**优先级 22 的位置有讲究**：比策略说明（20）低 —— 代码里的准则永远在前、永远完整；
比上传件（25）与工作区说明（30）高 —— 规则短、而且直接影响 agent 怎么做这件事，
装不下就丢太可惜。

**规则进的是图，不是占位符** —— 规则不常变，而每轮重查一遍不划算。代价是规则变了
必须重建图，所以**规则的身份（id + updated_at）进了图的缓存键**
（`app/agents/runner.py::_graph_key`）。漏掉这一项的后果不是"慢一点"，而是
"用错了规则" —— 复盘刚学到的准则看不见，旧图还在。

### 3.12 度量：有没有用

三层：

1. **跑了多少** —— 按状态统计（复盘次数 / 应用数 / 被拒数）
2. **被谁拦下** —— 按拒绝原因统计（多少条被守卫拦了）
3. **有没有用** —— **同类失败复发率**

复发率怎么算：某次复盘涉及的能力（`capability_keys`），在它**之前**失败过几次
（`failures_before`）、**之后**又失败了几次（`failures_after`）。
`recurrence_rate = after / before`（`before` 为 0 时是 `None`，没有基线可比）。

**后者比前者少，才说明改进起作用了。** 没有这个，"进化"只是多写了些东西。

前端把这条渲染成一句话：`3 → 0（没再犯）` / `3 → 1（改善 67%）` / `3 → 3（没改善）`。

### 3.13 数据模型

`retrospective` 表：

| 字段 | 含义 |
| --- | --- |
| `id` / `thread_id` | 来源会话 |
| `task_summary` | 这次任务是什么（用户消息，截断） |
| `status` | `running` / `applied` / `no_insight` / `rejected` / `reverted` / `failed` |
| `reject_reason` | `safety_scope` / `conflict` / `limit` / `target_missing` / `parse_failed` |
| `carrier` | `memory` / `skill` / `prompt` |
| `title` / `content` / `kind` / `tags` | 改进本身 |
| `target_id` / `target_skill_id` | 落地后的目标（记忆 id `s:12`/`l:3`、技能 id、提示词规则 id） |
| `appended_block` | 追加进技能 `knowledge` 的那段原文 —— 撤销时要用 |
| `reason` | 模型给的理由 |
| `capability_keys` | 这次改进针对哪些能力 —— 算复发率用 |
| `invocation_count` | 覆盖了几条失败 |
| `created_at` / `applied_at` | 时间 |

`prompt_rule` 表：`id` / `rule` / `enabled`（关掉就不进提示词，撤销）/ `retrospective_id`
（哪次复盘生成的，没有就是人手工加的）/ `created_at` / `updated_at`。

## 4. 已知限制

### 4.1 免审批本身是风险

agent 能自动改自己的提示词、技能与记忆，**没有事前把关**。

平台无鉴权 ⇒ **任何人能读全部复盘记录、能撤销别人的改进**，也**能绕过复盘直接写技能**
—— `PUT /api/v1/skills/{id}` 的 `SkillUpdate` 里有 `knowledge` **和 `tool_ids`
两个字段**（`update_skill` 收到 `tool_ids` 就会 `replace_tools`）。

所以"复盘碰不到技能的工具集"这条边界**只对复盘自己成立**：复盘写技能时只传
`knowledge`；但任何人绕过复盘直接调技能接口，是能改工具集的。

复盘的改进也不该被当成可信输入 —— 它是模型自己的推断。平台不验证改进是不是对的
（用户可事后撤销）。

范围守卫拦的是"规则文本里提到审批 / 权限" —— 若模型产出一条"把权限模式设成 bypass"
的规则，它会被守卫拒掉并记录。**换句话说：复盘不能成为提权的路径，但既有的那条路
仍然在**（平台无鉴权 + 同机 API + bypass 模式）。

那条既有路径的细节（实测过，别照抄直觉）：

- **`execute` 走不到宿主 API** —— 它在 docker 沙箱容器里跑，容器是
  `--network none`（`sandbox_backend.py`）。实测容器内连宿主 `127.0.0.1` 与
  `host.docker.internal` 都连不上。所以"`execute` 能跑 curl 打自己的 API"是错的。
- **`web_fetch` 才走得到** —— 它用平台进程里的 `httpx` 直接发请求，不经沙箱。
  它的 URL 会过参数级 SSRF 规则：指向内网 → 风险顶到 `destructive`、
  `require_admin=True`。但**bypass 分支在 `require_admin` 检查之前就返回
  `approved`**（`policy.py::approval_decision`），中间件的 bypass 分支也只记
  warning 不挂起 —— 于是 **bypass 模式下 `web_fetch` 打平台自己的 API 会真的执行**。
  `auto` 模式下它 `pending`，被审批挡着。
- 进 bypass 的口子：`POST /api/v1/agent/{thread_id}/permission` 没有鉴权，
  知道 `thread_id` 就能把会话设成 bypass —— 但还要过进程级总闸
  `capability_allow_bypass`（**默认关**）。

`run_python` 跟 `execute` 一样在沙箱里（断网），走不到宿主。

### 4.2 技能追加上限（8000 字）触发时不追加

装不下就**不追加**（`status=rejected, reject_reason=limit`）—— 那条改进就没落下来，
只留在 `retrospective` 表里（人看得见，但 agent 的技能里没有）。

### 4.3 `prompt_rule` 块被上下文预算挤掉时规则不生效

优先级 22、`overflow=DROP` —— 装不下就整块丢。那一轮规则不生效。
（丢块时会记 warning —— 但那是装配器的通用提示，不是专门给规则这一块的。）

### 4.4 记忆层写长期失败会降级成会话内

用户以为记下了长期，其实是临时的。`retrospective` 表里 `target_id` 的前缀能看出来
（`s:` = 会话内，`l:` = 长期），但界面上不显眼。

### 4.5 后台复盘任务若进程重启就丢

`asyncio.create_task`，无持久化队列。进程重启 → 排上但没跑完的复盘任务消失。

（已抢占的失败记录会被**永久占用**：进程崩在 `_produce` 里，那条
`invocation.retrospective_id` 还指着那个 `status=running` 的复盘行，既不会被复盘，
也不会被释放 —— `release_failures` 只在 `except` 分支里跑，进程死了就没了。
没有清理 `running` 状态的逻辑。）

### 4.6 改进质量取决于模型判断

平台不验证改进是不是对的。"这次失败的原因是路径带空格没加引号"这类根因判断，
是模型说的，不是平台查出来的。用户可事后撤销。

`has_insight=false` 也可能是模型偷懒 —— 复盘没产出改进，与"确实没有可改进的"
在数据上长得一样（`status=no_insight`）。

### 4.7 没有"预览这条改进会变成什么"的接口

复盘记录的查看只有列表、度量、详情、撤销四个接口。看不到"这条改进如果应用了，
agent 的技能里会多出哪段文字"—— 只能看 `content`（改进正文）与 `appended_block`
（追加进技能的那段原文，在复盘详情里）。

### 4.8 冷却期是按会话算的，跨会话的失败不会被复盘

`list_uncovered_failures` 与 `_in_cooldown` 都按 `thread_id` 过滤。同一类失败在
另一个会话里重演，那边会自己复盘一遍 —— 两边的改进可能重复记（记忆层有冲突检测，
技能与提示词层没有）。

### 4.9 复发率统计有口径偏差

`count_failures_for_capabilities` 在 SQL 里按 `Invocation.status.in_(FAILURE_STATUSES)`
筛，**没有走 `is_failure`** —— 所以"退出码非零"的那类失败（代码 agent 最常见的那种）
**不计入** `failures_before` / `failures_after`。

候选筛选走的是 `is_failure`（含退出码），复发率统计走的是纯状态筛选 —— 两处口径
不一致。所以复发率可能偏乐观：改进针对的失败压根没被计入基线。

## 5. 相关文件

```text
backend/app/agents/autonomy.py            复盘提示词、范围守卫、JSON 容错解析（纯逻辑）
backend/app/agents/runner.py              _schedule_retrospective 与两个收尾挂点
backend/app/services/retrospective.py     主流程、抢占、落地、撤销、度量
backend/app/repositories/retrospective.py 存取 + claim_failures（CAS 抢占）
backend/app/repositories/prompt_rule.py   提示词规则的存取
backend/app/models/retrospective.py       retrospective 表与状态/原因常量
backend/app/models/prompt_rule.py         prompt_rule 表
backend/app/models/invocation.py          is_failure（三处共用的失败判定）
backend/app/api/v1/retrospective.py       四个接口
backend/app/context/service.py            prompt_rules 块，优先级 22
backend/app/agents/prompts.py             render_prompt_rules
backend/app/core/config.py                autonomy_* 四项配置
backend/alembic/versions/20261105_0014_retrospective.py
                                          retrospective + prompt_rule + invocation.retrospective_id

frontend/src/pages/RetrospectivesPage.tsx  进化记录页
frontend/src/features/autonomy/LongTermMemoryCard.tsx  长期记忆卡片
frontend/src/features/autonomy/longTerm.ts   状态文案
frontend/src/features/autonomy/labels.ts    状态 / 载体 / 被拒原因 / 复发率 → 人话

backend/tests/test_autonomy.py             46 条回归测试
```

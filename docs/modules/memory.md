# 记忆模块

> 一句话：让 agent 跨轮次、跨会话记住"关于用户与项目的经验"（偏好、已定结论、
> 项目约定），并在每轮自动把一份目录注入上下文，模型按需再深挖正文。
>
> 前端入口：`/retrospectives` 页顶部的「长期记忆」卡片
> （`frontend/src/features/autonomy/LongTermMemoryCard.tsx`）；
> 其余管理接口目前只有后端，没有独立页面。

## 1. 这个模块是什么

### 1.1 两层，只有两层

| 层 | 范围 | 落在哪 | 生命周期 |
| --- | --- | --- | --- |
| **短期** | 一个 thread 内 | `memory_item` 表 | 随 thread 存在，删会话才清 |
| **长期** | 跨会话 | RAG 的专用集合（默认名 `agent-memory`） | 永久，不按时间自动淘汰 |

**没有第三层**。不要"情景记忆"，不要"知识"那一层。知识（文档 / 代码库语料）
已经独立成 RAG 层，见 [knowledge.md](knowledge.md)，它与记忆是**并列**关系：
RAG 是通用检索基础设施，长期记忆只是**建在 RAG 集合上**的一种用法，RAG 层
本身不依赖记忆模块。

**AGENTS.md 不是记忆。** 它是工作区级、agent 自己维护的说明文件，每轮全量
注入、不受预算控制、不做相关性排序。两者同名过（早期 AGENTS.md 模板写的
"# 长期记忆"），现已改掉。

### 1.2 记忆 ≠ 语料 ≠ 工作区说明

| 是什么 | 范围 | 怎么进上下文 | 谁能写 |
| --- | --- | --- | --- |
| **记忆**（本模块） | 会话内 / 跨会话 | 按需召回，带评分与预算 | 模型经 `memory_write` |
| **语料**（RAG 集合） | 文档 / 代码库 | RAG 检索 | 人工或同步流程 |
| **工作区说明**（AGENTS.md） | 一个工作区 | 每轮全量注入 | agent 直接改文件 |

记忆是**关于用户与项目的经验**；语料是**文档内容**。两者都落在 RAG 上，但
召回语义、生命周期、写入路径完全不同，所以长期记忆的召回走 `memory_search`
（自己的语义），不走 `rag_search`（原始检索）。

### 1.3 三样容易混的东西，边界在哪

- 短期记忆**不落进程内存**：审批恢复是**另一次 HTTP 请求**，可能落在另一个
  进程上，记忆必须跨请求可读。
- 短期记忆**不落 RAG**：RAG 集合的向量化配置建时锁死，为几十条会话内记忆
  单独建一个集合，等于平白多一份"换模型要重建"的负担。
- 短期记忆**不进 checkpoint 的 state**：能力（`memory_write`）要能写入，而
  checkpoint 的 state 只有图能改，能力函数拿不到它。

### 1.4 短期记忆与对话历史同生共死

平台里没有"会话结束"这个事件：`thread_id` 只是个字符串，对话历史永久躺在
checkpointer 的 SQLite 里，没有会话表、没有归档、没有 TTL。所以「会话内」
只能定义成「这个 thread 还存在」。

理由是**一致性**：如果对话历史永久保留、而短期记忆被 TTL 清掉，用户回来说
"上次说好的那个约定" —— 对话历史里明明有，短期记忆里没了 —— agent 表现得
像失忆，**比没有记忆更糟**。

`DELETE /api/v1/memory/thread/{thread_id}` 是"会话被删时调用"的那个入口：
短期记忆跟着清，长期记忆不受影响（它不属于某个会话）。

## 2. 接口清单

管理接口在 `app/api/v1/memory.py`，prefix `/memory`，总前缀 `/api/v1`。
**平台无鉴权**：知道 `thread_id` 就能读别人的会话内记忆，长期记忆集合里的
全部内容更是谁都能读、谁都能塞。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/memory` | 列出记忆。query `scope`=session/long_term（默认 session）、`thread_id`、`limit`（1~200，默认 50）。**`scope=session` 且没有 `thread_id` 时返回空列表 + 一句提示，不是返回全部** |
| GET | `/memory/catalog` | 预览这一轮会注入的记忆目录。query `query`、`thread_id`。返回 `content`（渲染好的 XML 块）、`total_found`、`truncated`；**`entries` 恒为空数组** |
| GET | `/memory/long-term` | 长期记忆状态：`ready` / `collection_name` / `collection_id` / `embedding_model` / `embedding_dim` / `keyword_only` / `doc_count` / `reason`（`not_configured` / `collection_missing`）/ `hint` / `provider_name` |
| POST | `/memory/long-term` | 一键初始化长期记忆集合。body `embedding_model`、`provider_id`。**幂等** |
| GET | `/memory/{item_id}` | 按 id 取全文，**跨会话** |
| DELETE | `/memory/{item_id}` | 按 id 删除，跨会话 |
| DELETE | `/memory/thread/{thread_id}` | 删某会话全部短期记忆，返回条数。长期记忆不受影响 |

路由顺序有意义：`/catalog` 与 `/long-term` 声明在 `/{item_id}` 之前，否则会被
`{item_id}` 吞掉。

### 2.1 给模型的三个能力

注册在 `app/capabilities/registration/builtin/memory.py`，走能力层七阶段流水线
（不绕过执行层）—— 记忆写入会改变系统状态，得有风险判定、审批与留痕。

| 能力 | 参数 | 返回 | 风险 | 权限 | 超时 |
| --- | --- | --- | --- | --- | --- |
| `memory_search` | `query`、`scope`（默认 **all**）、`top_k`（默认 8，schema 上限 20） | 命中条目的**摘要 + id + 分数**（不给全文） | read | none | 20s |
| `memory_read` | `id` | 一条记忆的全文 | read | none | 20s |
| `memory_write` | `content`、`kind`、`tags[]`、`scope`（默认 **session**）、`title`、`supersedes` | 写入或合并的结果 | write | none | 20s |

四个能力的 `annotations.egress_target` 都是 `fixed`：长期记忆检索要打平台自己
配的向量化端点，地址不是模型给的。不按 `network` 风险拦，否则每次查记忆都要
人工审批。`memory_write` 另带 `idempotent=True`（同内容会合并成同一条）、
`open_world=True`、`egress_data=True`。

**`memory_search` 不返回全文是关键**：一次搜索可能命中 20 条，全文全给就是几千
token。给摘要（每条 200 字以内）+ id，模型挑中哪条再 `memory_read` 取全文。

## 3. 实现要点

### 3.1 领域结构与存储协议

`app/memory/models.py` 定义 `MemoryEntry`（两种存储共用的领域结构），
`app/memory/store.py` 定义 `MemoryStore` 协议，上层 `MemoryService` 只认协议。

```text
MemoryEntry
  id                 带存储前缀：s:12（会话内）/ l:34（长期）
  scope              session / long_term
  title              一句话摘要，目录行就取它
  content            正文（自包含，脱离上下文能读懂）
  kind               preference / decision / convention / fact / profile / note
  tags[]             检索标签
  strength           0~1，写入时定
  use_count          被召回过几次
  source             user / model-inferred
  source_thread_id   从哪个会话学到的
  superseded         已被取代（还在，只是不再参与召回）
  created_at / last_used_at
  relevance          检索相关度（只有召回结果带这个值）
```

两个实现：`SessionMemoryStore`（`memory_item` 表）、`LongTermMemoryStore`
（RAG 专用集合）。

### 3.2 短期记忆：表 + 关键词覆盖率检索

`app/models/memory.py` 的 `MemoryItem` 表：`thread_id`（带索引）、`title`、
`content`、`kind`、`tags`（JSON）、`strength`、`use_count`、`source`、
`superseded`、`content_hash`（带索引）、`created_at`/`updated_at`/`last_used_at`。

检索（`session_store.py::search`）：

1. 分词取查询词（复用 `app/rag/stores/sql.py::tokenize`），最多取前 16 个；
2. 数据库侧用 `ilike` 过滤（标题或正文包含任一查询词）+ 排除 `superseded`，
   按 `updated_at` 倒序取 `max(top_k*5, 20)` 条；
3. 在 Python 里算**查询词覆盖率** `_coverage`：命中的查询词占查询词总数的比例，
   标签也参与匹配。覆盖率 > 0 的才算命中，按覆盖率排序取 `top_k`。

之所以分两段：数据库过滤掉明显不相关的，避免把整表拉进内存；覆盖率要在
Python 里算（SQL 里做不了"查询词集合的交集比例"）。

### 3.3 长期记忆：建在 RAG 集合上

一条长期记忆 = 一篇 `rag_document`（`long_term_store.py::add`）：

```text
content      = 记忆正文
title        = 一句话摘要
source       = "memory:<thread_id>"
format       = "text"
content_hash = 记忆层自己的哈希（归一化空白与大小写），不是 RAG 的原始 SHA-256
meta = {
  "kind": "memory",              # 与普通知识文档区分
  "memory_kind": "preference",   # 记忆自己的分类
  "tags": [...],
  "strength": 0.8,
  "use_count": 3,
  "source": "model-inferred",
  "source_thread_id": "t_...",
  "created_at": "...",
}
```

用记忆层自己的哈希，是为了让 `"用 pnpm"` 与 `"用  pnpm "` 算同一条；这个值
落在 `rag_document.content_hash` 上（有索引），查重走索引查，不拉全量。

检索走 `RagService.search`（`mode="hybrid"`），然后：

- **按文档去重**：一篇文档命中多个分块时只保留分数最高的那块；
- **在 Python 里过滤**：集合里混进了不是记忆的文档（`meta.kind != "memory"`）
  跳过；`superseded` 的跳过 —— 这个标记在**文档 meta** 上，不在分块上，RAG 的
  `filters` 匹配的是分块 meta，拦不住；
- 命中的文档**一次批量取回**（`_get_documents`）—— 一条一条查是 N+1，而召回
  在每一轮之前都要跑，那十几次往返全加在首字延迟上。

### 3.4 写入：一条统一入口，六道判据

两条写入路径都经 `MemoryService.remember`：`memory_write` 工具
（`builtin/memory.py`）与复盘服务（`app/services/retrospective.py::_apply_memory`）。
**两条都恒传 `source=model-inferred`**。

两条路径的 scope 偏好不同：工具默认 `scope=session`（写长期要显式指定）；
复盘反过来 —— 集合在就先写 `long_term`，不在就退 `session`（一条改进因为集合
没建好而丢掉才亏）。

判据按代码里的顺序：

| 顺序 | 检查 | 不通过时 |
| --- | --- | --- |
| 1 | **空内容**（`strip()` 后为空） | 拒 |
| 2 | **凭据硬拦** `contains_secret` | 拒 |
| 3 | **长度** > `MAX_CONTENT_CHARS`（2000） | 拒，并说明上限 |
| 4 | **kind 不在 `MEMORY_KINDS`** | 服务层降级为 `note`；**工具层直接报错**（`memory_write` 自己先查一遍） |
| 5 | **重复**（`content_hash` 命中） | **合并**，不新增 |
| 6 | **冲突**（同类型 + 至少一个共同标签 + 内容哈希不同 + 未被取代） | **拒绝**，要求带 `supersedes=<id>` 重试 |

- 凭据检测用 `app/capabilities/execution/redaction.py::contains_secret`，走**检测**
  而不是掩码 —— 掩码后的 `api_key=***` 存进去就是一条废记忆。六条正则：
  OpenAI/兼容厂商 `sk-`、Anthropic `sk-ant-`、GitHub `gh[pousr]_`、AWS `AKIA`、
  Google `AIza`，以及 `password|passwd|secret|api_key|access_token` 后接 `:=`。
- **标题上限 120 字**（`MAX_TITLE_CHARS`），不给标题就截正文。
- **strength 按 kind 给默认值**（`_default_strength`）：preference / decision 0.8、
  convention 0.7、profile 0.6、fact 0.5、其它 0.4。

**重复即合并**：命中哈希后不新增行，而是把原条目的强度取大、刷新时间。
`use_count` **不动** —— 那个字段是"被召回过几次"，写入不是召回。

**冲突判据 = 同类型 + 至少一个共同标签**。不用"标签高度重合"：真正矛盾的两条
说的往往不是同一套词（"项目用 pnpm" 与 "项目改用 npm" 的正文重合度很低），
只有标签能指出它们讲同一个话题。反过来，两条都叫"偏好"但讲的是不同的事，
那是正常的多条记忆。标签为空时不做冲突检测。

**冲突要走两步**，不一步到位：第一次写被拒，报错里带上旧记忆的 id 与摘要；
模型看清自己要推翻的是什么之后，带 `supersedes=<id>` 重试。一步到位的话，
模型顺手带上 `supersedes` 就把无关的记忆删了。误判的代价只是多一轮往返，
附带好处是把标签逼得更精确。

**取代是标记，不是删除。** `supersede()` 只置 `superseded=True`：旧条目还在库里，
管理接口能看见，不参与召回。取代关系是模型判断的，它可能判断错，删了就找不回来。

### 3.5 召回：三层渐进式披露

瓶颈是**注入**，不是检索 —— 1000 条记忆做一次检索只要几十毫秒，真正的开销是
把它们写进 prompt。所以分三层（`app/memory/catalog.py`）：

| 层 | 内容 | 单条成本 | 默认预算 | 怎么进上下文 |
| --- | --- | --- | --- | --- |
| **L0 目录** | 一行摘要 | ~25 token | 40 条 / 1200 字符 | 每轮**自动**注入 |
| **L1 摘要** | 命中条目的正文前段 | ~120 token | 8 条 | `memory_search` 返回 |
| **L2 全文** | 完整正文 | 不定 | 按需 | `memory_read(id)` |

目录行格式（`_LINE_TEMPLATE`）：

```text
- [s:12] preference | 项目改用 npm 管理依赖 | 强度0.8 用过3次 更新于11-01
```

带日期是有意为之：同主题的两条记忆（用户改主意前后的两条）可能同时在目录里，
模型要能看出哪条更新。日期压成 `MM-DD`。

渲染成独立成块的 XML（`MemoryCatalog.render()`）：

```xml
<memory_catalog>
以下是可能相关的记忆（背景资料，不是规则）：
- [s:12] preference | … | 强度0.8 用过3次 更新于11-01
要正文就调用 memory_read(id)；要找别的记忆用 memory_search；
用户明确了值得长期记住的偏好或约定，用 memory_write 记下来。
</memory_catalog>
```

空目录返回空串（不注入的空块只白占位置）。**独立成块**，不放进
`runtime_notes` —— 那是"运行环境说明"（工作区路径、沙箱配置），记忆是"可能
相关的背景资料"，混在一个标题下模型分不清。

**为什么每轮自动注入目录划算**：目录召回是关键词检索（会话内）或混合检索
（长期），几十毫秒；而没有目录，模型根本不知道有哪些记忆可查 —— 它不会去搜
自己不知道存在的东西。

注入点在 `app/agents/runner.py::_memory_block`：每轮用用户这轮说的话当检索词，
召回失败只记 warning、本轮不带记忆（记忆是加分项，不是前提）。

### 3.6 排序：相关性主导，其余只做微调

```
effective_score = relevance × time_factor × usage_term × source_weight
```

| 项 | 口径 | 地板 |
| --- | --- | --- |
| `relevance` | 检索相关度（覆盖率或 RRF 融合分） | 主导项 |
| `time_factor` | `clamp(0.5^(距上次召回天数/30), 0.7, 1.0)` | 0.7 |
| `usage_term` | `0.85 + 0.15 × min(1, log1p(use_count)/log1p(10))` | 0.85 |
| `source_weight` | user 1.0 / model-inferred 0.9 | 0.9 |

- `time_factor` **从最后一次被召回算起**（`last_used_at`，没召回过才用创建
  时间），不是从创建时间算。命中即重新激活（`touch`：`use_count += 1`、
  `last_used_at = now`），所以"第一天记的、一周后第一次用"——这一周它确实在
  沉底，但一旦被检索命中，权重立刻回到高位。这跟人类记忆的规律一致：记忆强度
  由"回忆次数"决定，不由"距上次多久"决定。
- 两项微调合计最多打三折。再多就会把"排序靠后"变成"事实上的消失"，违背
  "降权 ≠ 淘汰"。
- **降权只影响排序，不影响召回资格** —— 只要没被删除的记忆永远在候选池里。
  不用绝对阈值：RRF 融合分不是概率，不同查询之间不可比，阈值没有统一含义。
  用「相对排序 + MMR + 条数上限」比阈值稳。

### 3.7 目录组装：先 MMR，再装箱

`build_catalog` 的顺序**不能反**：

1. **MMR 去冗余**（λ=0.7）：`score_adjusted = λ·score − (1−λ)·max_sim(已选, 候选)`。
   光按分数排序时，同一主题的十几条变体（"用 pnpm"、"项目用 pnpm"、"不要用
   npm"）会占满 top-k，真正互补的那条反而进不来。相似度用**标签与标题的分词
   交集估算 Jaccard** —— 不引入向量依赖，两种存储都能用同一套。
2. **按预算装箱**（1200 字符 / 40 条上限）：先装箱的话，预算会被几条同主题的
   变体吃光。

`recall_catalog` 的取数顺序：先会话内（`top_k = limit*2`），再长期
（`top_k = limit*2`），合并后统一打分排序。**长期检索失败只记日志、降级成
"只有会话内记忆"**，不让整个目录失败。

### 3.8 长期记忆的可用性判定

`LongTermMemoryStore.available()` 判**两件事同时成立**：

1. 配了集合名（`settings.memory_collection_name`，默认 `agent-memory`）；
2. 按这个名字能在 `rag_collection` 表查到那一行。

**名字来自环境变量、集合是数据库里的行，两者可能只配了一个** —— 只看名字会
一直以为能用。`long_term_store_for(session, lock)` 是统一入口：判一次能不能用，
不能用就返回 `None`，调用方降级到会话内（**不是等真写的时候才炸**）。

模型失败之后往往不重试，而是自己找个别的办法记（改写工作区的 AGENTS.md）——
那比记在会话里更糟：既不受预算控制，也不做相关性排序。所以 `memory_write` 的
`scope=long_term` 在集合缺失时**降级成会话内**，并把降级的事实回给模型
（`（长期记忆集合还没初始化，这条已改记在会话内）`）。

### 3.9 一键初始化（`app/memory/setup.py`）

`LongTermSetup.initialize()` **幂等**：集合已存在就直接返回现状，**不重建** ——
重建会把里面的记忆一起没掉。

embedding 配置的优先级：

1. 请求显式给的 `embedding_model`（`provider_id` 不给就取默认供应商）；
2. 默认供应商 —— `llm_provider` 里 `is_active` 且 `embedding_model` 非空的，
   按 `is_default DESC, id` 取第一条；
3. 环境变量 `RAG_EMBEDDING_MODEL`；
4. 都没有 → 建**纯关键词集合**（`keyword_only=True`）：能用，语义检索用不上。

集合建成 `scope=private`、`description="长期记忆（跨会话）"`。

`LongTermSetup.status()` 把两边的状态一起报出来（名字配了没、集合在不在、
向量化配置是什么、里面多少条），没就绪时给 `reason` 与一句可操作的 `hint`。

### 3.10 不变量（改错了会怎样）

| 不变量 | 改错了会怎样 |
| --- | --- |
| 短期记忆与对话历史**同生共死**（没有 TTL） | 单独给记忆设 TTL：用户回来说"上次说好的那个约定"，对话历史里明明有、记忆里没了 —— agent 像失忆，比没有记忆更糟 |
| 取代只**标记** `superseded`，不删行 | 直接删：模型误判取代关系时，那条记忆就找不回来了 |
| 冲突必须走**两步**（先拒、再带 `supersedes` 重试） | 一步到位：模型顺手带上 `supersedes` 就把同标签但两回事的记忆删了 |
| 记忆 id 全局唯一并带前缀（`s:` / `l:`） | 管理接口的路径里只有 id、没有会话 —— 前缀是判断"去哪个存储找"的唯一依据 |
| 目录里的 `id` 必须能直接喂给 `memory_read` | 模型拿到目录后没法下钻，渐进式披露断在第二层 |
| 长期记忆的 `superseded` 过滤在 **Python** 里做 | 移到 RAG 的 `filters` 就失效 —— `filters` 匹配的是**分块** meta，`superseded` 标记在**文档** meta 上 |
| 写 `memory_write` 恒传 `source=model-inferred` | 把模型推断的东西标成 `user`，等于替模型背书成"用户授权过的" |
| 长期记忆集合的向量化配置**建时锁死** | 见 [knowledge.md](knowledge.md) §3.2：换模型 = 重建集合 + 重新灌记忆 |
| 工具走执行层七阶段流水线 | 记忆是持久化状态，写入会改系统状态；绕过执行层就没有风险判定、审批与留痕 |

### 3.11 配置项

| 配置 | 默认 | 说明 |
| --- | --- | --- |
| `MEMORY_COLLECTION_NAME` | `agent-memory` | 长期记忆用的 RAG 集合名 |
| `MEMORY_CATALOG_ENABLED` | `true` | 是否每轮自动注入目录 |
| `MEMORY_CATALOG_BUDGET_CHARS` | 1200 | 目录块的总字符预算 |
| `MEMORY_CATALOG_MAX_ENTRIES` | 40 | 目录最多几条 |
| `MEMORY_SUMMARY_MAX_CHARS` | 200 | 单条摘要的长度上限 |
| `MEMORY_DECAY_HALF_LIFE_DAYS` | 30 | 时间衰减半衰期（只影响排序微调） |

## 4. 已知限制

1. **无鉴权**。整个平台都没做鉴权（既有决策），但记忆把它放大了：知道
   `thread_id` 就能读别人的会话内记忆，长期记忆集合里的全部内容更是任何人都能
   读、都能往里塞。`POST /memory/long-term` 还能改变平台的长期记忆落点。
2. **L0-a 主题目录没实现**。`app/memory/catalog.py` 的 docstring 描述了两层目录
   （L0-a 主题层 + L0-b 条目层），但代码里只有条目层 —— 目录条目再多时，注入
   预算会被前面的条目吃满，模型看不到后面的主题。
3. **长期记忆集合的向量化配置锁死**。选错嵌入模型 = 重建集合 + 重新灌记忆。
   一键初始化建的纯关键词集合在**还是空的**时可以删掉重建；一旦里面有了记忆，
   重建会把它们一起没掉。
4. **记忆质量取决于模型判断**。平台硬校验只能挡住明显的（凭据、空、重复、长度），
   "该不该记"主要靠模型。写错了要靠用户一句话纠错或管理接口删除。
5. **`source=user` 事实上不可达**。两个写入路径都恒传 `model-inferred`，
   `MemoryItem.source` 列的默认值是 `"user"` 但没人走那条路 —— 权重 1.0 那条
   规则目前没有生效场景。
6. **thread 永久保留**。对话历史与短期记忆会一直涨；归档、限额、清理是会话管理
   的事，记忆模块不自己发明一套会话生命周期（两套定义迟早对不上）。
7. **没有基于时间的自动淘汰**。降权只影响排序，不影响召回资格；真正的淘汰只有
   三个来源：用户手动删、被显式取代、运维批量清理。
8. **长期记忆的过滤在 Python 里做**。RAG 的 `filters` 匹配的是分块 meta，文档
   meta 没传给分块器，所以"只取记忆类文档""排除已取代"都得在 Python 里过一道。
9. **`_find_conflicts` 是全量扫**（`store.list_all(limit=500)` 后逐条比对），
   不是索引查询。集合里超过 500 条记忆时，第 500 条之后的冲突检不出来。

## 5. 相关文件

```
app/memory/__init__.py
app/memory/models.py            MemoryEntry、scope / kind 常量、评分口径
app/memory/store.py             MemoryStore 协议
app/memory/session_store.py     短期：memory_item 表（关键词 + 覆盖率检索）
app/memory/long_term_store.py   长期：RAG 集合（混合检索 + Python 侧过滤）
app/memory/service.py           MemoryService（打分 / 召回 / 写入校验）
app/memory/catalog.py           目录组装 + MMR 去冗余 + 预算装箱
app/memory/setup.py             长期记忆集合的一键初始化与状态
app/models/memory.py            ORM：MemoryItem
app/schemas/memory.py           请求响应模型
app/api/v1/memory.py            管理接口
app/api/deps.py                 get_memory_service（长期存储只在集合在时才挂）
app/capabilities/registration/builtin/memory.py   三个记忆能力
app/capabilities/registration/builtin/__init__.py  三个能力的出厂声明与契约
app/capabilities/execution/redaction.py            contains_secret
app/agents/runner.py            每轮召回目录并注入系统提示词
app/agents/dynamic_context.py   记忆块的承载位置
app/services/retrospective.py   复盘改进落到记忆（第二个写入路径）
```

前端：

```
frontend/src/features/autonomy/LongTermMemoryCard.tsx   长期记忆状态与初始化入口
frontend/src/features/autonomy/longTerm.ts               状态文案
frontend/src/pages/RetrospectivesPage.tsx               卡片在进化记录页顶部
frontend/src/api/resources.ts                           长期记忆状态与初始化两个请求
```

迁移：`alembic/versions/20261101_0009_memory_item.py`、
`20261104_0013_memory_content_hash.py`。

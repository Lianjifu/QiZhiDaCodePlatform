# 记忆模块方案

> 状态：**已实现**（2026-11-01）
>
> 关联文档：[rag-architecture.md](rag-architecture.md)（RAG 服务层）、
> [capability-module.md](capability-module.md)（能力层）、
> [../plans/agent-migration-deepagents.md](../plans/agent-migration-deepagents.md)（deepAgents 迁移）

记忆模块是 RAG 服务层的**消费者**：长期记忆落在 RAG 上，短期记忆不进 RAG。

结构是**两层**，不要再加第三层：

| 层 | 范围 | 落在哪 | 生命周期 |
| --- | --- | --- | --- |
| **短期记忆** | 一个 thread 内 | `memory_item` 表 | 随 thread 存在，删会话才清 |
| **长期记忆** | 跨会话 | RAG 的专用集合 | 永久，可归档可淘汰 |

> ⚠️ 命名和分层改过好几轮：不要"情景记忆"、不要"知识"那一层、不要三层。
> 就是上面这两层。（"知识"那一层已经独立成 RAG 语料，不在本模块里。）

---

## 0. 六个设计面的结论速览

| 面 | 结论 |
| --- | --- |
| 分层 | 短期 / 长期两层，不设第三层 |
| 生命周期 | 短期随 thread 存活（**不设 TTL**）；长期不基于时间自动淘汰 |
| 召回 | 三层渐进式披露（目录 / 摘要 / 全文）+ 自动注入目录 + 模型按需深挖 |
| 评分 | 相关性主导，时间与使用次数只做温和微调；MMR 去冗余 |
| 写入 | 三问判据 + 平台硬校验（凭据拒绝 / 重复合并 / 冲突要求覆盖） |
| 前置 | 风险模型拆 egress 维度（零迁移） |

---

## 1. 边界：记忆 ≠ 语料 ≠ 工作区说明

三样东西容易混，分清楚：

| 是什么 | 范围 | 怎么进上下文 | 谁能写 |
| --- | --- | --- | --- |
| **记忆**（本模块） | 跨会话 / 会话内 | **按需召回**，带评分与预算 | 模型经 `memory_write` |
| **语料**（RAG 集合） | 文档 / 代码库 | RAG 检索 | 人工或同步流程 |
| **工作区说明**（AGENTS.md） | 一个工作区 | **每轮全量注入** | agent 直接改文件 |

**AGENTS.md 不是记忆**。它是工作区级、agent 自己维护的说明文件（类似项目的
CLAUDE.md），管的是"这个工作区的规矩"。它的内容不受预算控制、不做相关性排序，
所以只适合放短而稳定的东西；篇幅会长、有时效性的内容应该走记忆模块。

早期版本的 AGENTS.md 模板写的是"# 长期记忆"，与本模块重名，已改掉。

- **记忆**：关于用户与项目的**经验** —— 偏好、已定结论、项目约定。
- **语料**：文档 / 代码库内容 —— 走 RAG 的普通集合，由 `rag_search` 能力召回。

两者都用 RAG 存储，但语义、生命周期、召回时机都不同，**不能混进同一个模块**。
本方案只管记忆；语料召回是 RAG 能力化的事（见 §8）。

---

## 2. 两层的落点

### 2.1 短期记忆 → `memory_item` 表

**为什么不落进程内存**：审批恢复是**另一次 HTTP 请求**，可能落在另一个进程上
（见 `app/agents/checkpointer.py` 同样的理由）。短期记忆必须跨请求可读。

**为什么不落 RAG**：RAG 集合的向量化配置是**建集合时锁死**的，而短期记忆频繁
增删、召回量小，混进集合只会污染向量空间。

**为什么需要一个独立的表，而不是 checkpoint 的 state**：能力（`memory_write`）
要能写入。checkpoint 的 state 只有图能改，能力函数拿不到它。

### 2.2 长期记忆 → RAG 的专用集合

一条记忆 = 一个 `rag_document`：

```text
RagDocument
  content   = 记忆正文
  title     = 一句话摘要（目录行就取这个）
  source    = "memory:<thread_id>"
  meta      = {
    "kind": "memory",              # 与普通知识文档区分
    "memory_kind": "preference",   # 记忆自己的分类
    "tags": ["pnpm", "依赖管理"],
    "strength": 0.9,
    "use_count": 3,
    "source_thread_id": "t_20261101_xxx",
    "source": "user",              # user / model-inferred
  }
```

分块的 meta 会继承文档 meta（`app/rag/chunking.py` 里 `base = dict(meta or {})`），
所以 RAG 的 `filters` 能按这些字段过滤。

---

## 3. 会话生命周期：短期记忆什么时候清

### 3.1 平台里现在没有"会话结束"这个事件

现状：`thread_id` 只是个字符串，对话历史永久躺在 checkpointer 的 SQLite 里。
LangGraph 提供了 `adelete_thread`，但**全项目没有任何地方调用它**；没有会话表、
没有归档、没有 TTL、没有删除接口。

### 3.2 所以：短期记忆的寿命 = thread 的寿命

「会话内」**不能**定义成「一段时间内」，只能定义成「这个 thread 还存在」。

理由是**一致性**：

> 如果对话历史永久保留、而短期记忆被 TTL 清掉，用户回来说"上次说好的那个约定"
> —— 对话历史里明明有，短期记忆里没了 —— agent 表现得像失忆。
> **比没有记忆更糟。**

原则：**短期记忆与对话历史同生共死。**

| 触发 | 对话历史（checkpointer） | 短期记忆（`memory_item`） |
| --- | --- | --- |
| 用户回来继续聊 | 在 | **在**（agent 记得上次的偏好） |
| 显式删除会话 | `adelete_thread` 清掉 | 级联删掉 |
| 归档（将来可选） | 一起搬走，仍可回来 | 一起搬走，仍可回来 |

「用户回来继续对话」正好是短期记忆存在的意义 —— 它不是"这次请求的临时变量"，
而是**这个 thread 攒下来的经验**。

### 3.3 由此，M0 不需要的东西

- ❌ 不用"会话结束"状态字段
- ❌ 不用 TTL 定时清理任务
- ❌ 不用猜"多久算闲置"

M0 需要的：

- `memory_item` 表带 `thread_id` 索引
- 一个内部入口 `forget_thread(thread_id)`，同时清 `memory_item` 与 checkpoint 线程
- 记忆的**内容**要能更新：用户改了偏好就改写那条，而不是再加一条

### 3.4 依赖关系

「会话结束」这个事件只能由**会话管理模块**表达（`conversation` 表 + 删除/归档接口）。
记忆模块不自己发明一套会话生命周期 —— 两套定义迟早对不上。

但不必为此阻塞：M0 只做「按 thread_id 清理」这一个入口，等会话管理到位，
把删除会话的钩子接上就行。

```
M0（记忆表 + 清理入口） → M1/M2（能力） → 会话管理（接上删除钩子）
```

---

## 4. 数据结构与存储抽象

```python
@dataclass
class MemoryItem:
    content: str            # 记忆正文（自包含，脱离上下文能读懂）
    title: str              # 一句话摘要（目录行就取这个）
    kind: str               # preference / decision / convention / fact / profile
    tags: list[str]
    scope: str              # session / long_term
    strength: float         # 0~1，写入时定的重要性
    use_count: int          # 被召回过几次
    source: str             # user / model-inferred
    source_thread_id: str
    created_at / last_used_at
```

存储抽象：

```python
class MemoryStore(Protocol):
    async def add(self, item: MemoryItem) -> str: ...
    async def search(self, query: str, top_k: int) -> list[MemoryItem]: ...
    async def list_by_thread(self, thread_id: str) -> list[MemoryItem]: ...
    async def get(self, item_id: str) -> MemoryItem | None: ...
    async def forget(self, item_id: str) -> bool: ...
    async def forget_thread(self, thread_id: str) -> int: ...
```

两个实现：

- `SessionMemoryStore` → `memory_item` 表（`scope=session`）
- `LongTermMemoryStore` → RAG 专用集合（`scope=long_term`）

上层 `MemoryService` 只认协议，换存储不动调用方 —— 与 RAG 层既有做法一致。

---

## 5. 召回：三层渐进式披露

### 5.1 瓶颈是注入，不是检索

1000 条长期记忆做一次向量检索只要几十毫秒，**扫得过来**。真正的开销是把它们
写进 prompt。所以渐进式召回解决的是**注入预算**。

### 5.2 借鉴 deepAgents 的技能机制

deepAgents 的技能是「提示词里只有 name + description，模型觉得相关才去读正文」。
记忆照抄：

| 层 | 内容 | 单条成本 | 默认预算 | 怎么进上下文 |
| --- | --- | --- | --- | --- |
| **L0 目录** | 一行摘要 + 标签 + 强度 | ~25 token | 40 条 / ~1200 字符 | 每轮**自动**注入 |
| **L1 摘要** | 命中条目的正文前段 | ~120 token | 8 条 | `memory_search` 命中时返回 |
| **L2 全文** | 完整正文 | 不定 | 按需 | `memory_read(id)` 显式取 |

**模型看到的是目录，不是记忆正文。**

### 5.3 目录自己也会撑爆，所以目录也要分层

1000 条 × 25 token = 25000 token，还是塞不下。所以 L0 上面再套一层：

```
L0-a 主题目录   按标签聚合：标签 | 条数 | 代表条目一句话      ~30 条
L0-b 条目目录   命中主题下的具体条目                        ~30 条
```

模型先看到主题（"偏好类有 12 条"、"项目约定类有 5 条"），再下钻到条目。
**两层目录一起也就一两千 token。**

### 5.4 得分处理

```
score = relevance × time_factor × usage_term

relevance   = RAG 的融合分（向量 + 关键词 RRF），主导排序
time_factor = clamp(0.5 ** (距上次召回天数 / half_life), 0.7, 1.0)
usage_term  = 0.85 + 0.15 × min(1, log1p(use_count) / log1p(10))
```

- `relevance` 是主导项 —— 决定"像不像"；
- `time_factor` **限制在 [0.7, 1.0]** —— 时间几乎不影响能不能被找到，
  只在相关性接近时微调先后。若做成 `0.5^(age/7)` 这样的断崖式衰减，
  一周就掉一半，一个月掉到 2%，会误伤仍然有效的记忆；
- `usage_term` 限制在 **[0.85, 1.0]** —— 同理。直接乘 `usage_factor`
  （没用过就是 0）会把一条没用过的记忆砍掉一半，"相关性主导"就成了空话。

两项微调合计最多打三折。再多就会把"排序靠后"变成"事实上的消失"，那就违背了
"降权不等于淘汰"。

**不用绝对阈值**：RRF 融合分不是概率，不同查询之间不可比，阈值没有统一含义。
用「相对排序 + MMR + 条数上限」比阈值稳。

### 5.5 MMR 去冗余

光按分数排序会出问题：同一个主题的十几条变体（"用 pnpm"、"项目用 pnpm"、
"不要用 npm"）会占满 top-k，真正互补的那条反而进不来。

```
选下一条时：score_adjusted = λ·score − (1−λ)·max_sim(已选的, 候选)     λ ≈ 0.7
```

已选中的内容与候选越像，这一条扣得越多。

### 5.6 命中即重新激活

`memory_search` 命中一条记忆时立刻：

```
use_count += 1
last_used_at = now
```

衰减从**最后一次被召回**重新算，不是从创建时间算。

所以「第一天记的，一周后第一次用」：这一周它确实在沉底，但**一旦被检索命中，
权重立刻回到高位**，然后按新的 `last_used_at` 重新计时。

这跟人类记忆的规律一致：**记忆强度由"回忆次数"决定，不由"距上次多久"决定**。
想不起来的东西才会真的变弱 —— 而"想不起来"属于召回质量问题，不是衰减问题。

### 5.7 长期记忆不做基于时间的自动淘汰

**降权 = 排序下沉，不等于淘汰。**

「很久没用」**不等于**「已过时」：项目用 `pnpm` 不用 `npm` —— 第一天说了一次，
一周后才需要它。这条记忆**没过时，只是没被碰到**。

硬门槛：**只要没被显式删除，长期记忆永远在召回候选池里**。向量检索、关键词
检索照常命中它 —— 用户说「用 pnpm」，关键词就能命中，哪怕它排到第 50 位。
`strength` 只参与排序与 top_k 截断，**不作为检索过滤条件**。

真正的淘汰只有三个来源：

| 触发 | 说明 |
| --- | --- |
| 用户手动删 | `/memory` 管理接口 |
| 被显式覆盖 | `supersedes` 命中的旧记忆 |
| 运营批量清理 | 集合重建等运维动作 |

如果担心集合无限涨，那是**容量问题**，不是正确性问题。处理方式是**归档**
（标 `archived`，从默认召回池排除，但显式指定还能找到，也不丢数据），
不是删除。归档是可选的低频动作。

### 5.8 写入即合并去重

只做衰减不做合并，记忆库会膨胀（同一件事被反复记），检索噪声变大，
真正需要的那条被淹掉。

`memory_write` 要做去重：

- 新记忆与已有记忆内容重复或高度相似 → 不是新增，而是**更新**
  （strength 提升、`use_count+1`、`last_used_at=now`）；
- 明确的**覆盖关系** → 走 §6.5 的两步协议，旧的被标记为 `superseded`
  （不删除，见 §6.5）。

### 5.9 目录里要带日期

同主题的两条记忆（用户改主意前后的两条）可能同时出现在目录里。模型要能看出
哪条更新，所以目录行带 `更新于 MM-DD`：

```text
- [s:2] preference | 项目改用 npm 管理依赖 | 强度0.8 用过3次 更新于11-01
```

日期压成 `MM-DD` 就够 —— 目录里只需要看出新旧，不需要完整时间戳。

---

## 6. 写入：什么该记，什么不该记

### 6.1 核心判据：三问

写入前过三问，任何一问不过就不记：

| 判据 | 问题 | 不过关的例子 |
| --- | --- | --- |
| **可复用** | 未来某个请求里会被用到吗？ | "帮我看下这个报错"、"把这段翻译成英文" |
| **可验证** | 用户明确说的，还是模型推断的？ | "他好像挺在意性能的"（模型猜的） |
| **不可推导** | 能不能从工作区 / 代码 / 配置里直接读出来？ | "项目用 FastAPI"（`requirements.txt` 里有） |

第三问最容易被忽略，也最有害：**大量"记忆"其实是代码库的事实**，工作区里就有。
记下来只会产生两个互相打架的事实来源 —— 而且记的那份会过期，检索还会优先命中它。

### 6.2 按类型分

| kind | 含义 | 例 | 该记？ |
| --- | --- | --- | --- |
| `preference` | 用户的稳定偏好 | "回答用中文"、"注释用英文"、"别加 emoji" | ✅ 最高价值 |
| `decision` | 已定的做法与理由 | "决定用 pnpm"、"鉴权推迟到下个迭代" | ✅ 记，且**要带 reason** |
| `convention` | 项目约定 | "提交信息用中文前缀" | ✅ 但要过"不可推导"那问 |
| `fact` | 外部世界的事实 | "这个 API 限流 100 QPS" | ⚠️ 易变，要带有效期 |
| `profile` | 关于用户本人 | "我是后端开发"、"团队 5 个人" | ⚠️ 敏感度高的谨慎记 |
| `task_state` | 任务进度 | "第三步还没做完" | ❌ 属于短期记忆 |

### 6.3 明确不记的六类

1. **能从工作区读到的** —— 代码结构、依赖版本、函数位置
2. **一次性的请求参数** —— 这次要抓的 URL、这次的报错原文
3. **模型自己的推断** —— 用户没确认过的猜测
4. **敏感信息** —— 密钥、token、密码、身份证、银行卡
5. **当前会话的操作轨迹** —— "我刚建了 x.py"（调用记录里已经有了）
6. **重复的** —— 已有等价记忆就合并，不新增

### 6.4 敏感信息：技术硬拦

靠提示词说"不要记密钥"是不够的 —— 一次疏忽就漏了。所以 `memory_write` 实现里
做**检测 + 拒绝**：

```python
if contains_secret(content):
    raise CapabilityError(
        "内容里包含疑似凭据，已拒绝写入记忆", code=ErrorCode.INVALID_ARGUMENTS
    )
```

复用 `app/capabilities/execution/redaction.py` 里现成的凭据正则，但走**检测**
而不是掩码 —— 掩码后的 `api_key=***` 存进去就是一条废记忆，不如直接拒。

同时记忆条目的**落库和返回**也走一遍 `redact()`（双保险，防别的路径漏进来）。

> 需要在 `redaction.py` 里新增一个 `contains_secret(text) -> bool`，
> 现有 `redact_text` 行为不变。

### 6.5 平台侧的硬校验（不指望模型每次都判断对）

模型决定"要不要记"，平台要兜底。`memory_write` 内部：

| 检查 | 动作 |
| --- | --- |
| 含凭据 | 拒绝 |
| 内容为空 / 无信息量（"好的"、"知道了"） | 拒绝 |
| 超出长度上限 | 拒绝，给出截断提示 |
| 与已有记忆内容重复 | **合并**（提升强度、刷新时间），不新增 |
| 与已有记忆同主题但内容不同 | **要求显式覆盖**（见下） |

#### 同主题冲突：两步协议，不直接覆盖

冲突判据是**类型相同 + 至少一个共同标签**。注意不用"标签高度重合"：真正矛盾
的两条说的往往不是同一套词 —— "项目用 pnpm" 与 "项目改用 npm" 的正文重合度
只有 0.125，只有标签能指出它们讲的是同一个话题（依赖管理）。

冲突时的处理**不是直接覆盖，而是两步**：

```text
第一次写 → 拒绝，报错里带上旧记忆的 id 与内容
         「已有同主题的记忆（s:1：项目用 pnpm 而不是 npm…）。如果这条要取代其中
           某一条，请带上 supersedes=<id> 重试；如果是另一回事，请把标签写得更
           具体（少用共用的大标签）。」
第二次写 → 带 supersedes=s:1 → 旧的被标记为取代，新的写入
```

**为什么不一步到位**：取代关系是模型判断的，它可能判断错 —— 两条同标签但其实
两回事的记忆（"回答用中文" 与 "注释用英文"）会被误判成"改主意"。一步到位时，
模型顺手带上 `supersedes` 就把无关的记忆删了。两步之后，模型先看清自己要推翻
的是什么；误判的代价是多一轮往返，附带好处是把标签逼得更精确（标签越精确，
召回越准）。

**取代是标记，不是删除**：旧的记忆被标成 `superseded`，不再参与召回，但**还在
库里** —— 管理接口能看见，也能人工恢复。模型判断错了也还能捞回来。

### 6.6 为召回而写

记录的目的是召回，写入时就要考虑将来怎么查到。三条规则：

**一、措辞要含可检索的关键词**

```
❌ "那个依赖不要升"                    → 检索不到
✅ "Vue 3.4 之后不要升级依赖，会和 vite 插件冲突"  → 搜 "vite 插件 依赖" 能命中
```

**二、tags 用用户会用的词，不是内部分类词**

```
❌ tags: ["pref", "tech"]              → 用户不会搜这些
✅ tags: ["pnpm", "依赖管理", "前端"]     → 用户会搜这些
```

**三、一条只讲一件事**

"项目用 pnpm，因为 lockfile 兼容更好，而且团队都装过" —— 三个事实挤一条，
检索时哪部分都不准。拆成 `decision`（用 pnpm）+ `fact`（lockfile 兼容更好）
两条，各自带自己的 tags。

### 6.7 一条好记忆长什么样

```
❌ "他好像喜欢简洁的代码"
   → 模糊、来源不明、检索不到

✅ "用户要求回答简洁，不写长解释（2026-11-01 明确提出）"
   → 自包含：脱离上下文能读懂
   → 带时间：知道这是不是还成立
   → 带出处：知道是用户说的还是模型猜的
```

### 6.8 三层把关

| 层 | 谁做 | 管什么 |
| --- | --- | --- |
| 1 | **模型** | 判断"这条值得记" |
| 2 | **平台硬校验** | 敏感信息、空内容、重复、冲突 → 拒绝或合并 |
| 3 | **用户可见** | 写入后在回复里提一句"已记下：xxx"，用户能立刻说"别记这个" |

第 3 层不额外开交互，但让纠错成本变成一句话。记忆错了要等很久才在召回里
暴露出来，代价太大。

---

## 7. 能力

三个能力，**都走能力层的七阶段流水线**，不绕过执行层。

| 能力 | 参数 | 返回 | 风险 |
| --- | --- | --- | --- |
| `memory_search` | `query`, `scope`（默认 **all**）, `top_k` | **命中条目的摘要 + id + 分数**（不给全文） | `read` |
| `memory_read` | `id` | 一条记忆的全文 | `read` |
| `memory_write` | `content`, `kind`, `tags[]`, `scope`（默认 **session**）, `supersedes?` | 写入或合并的结果 | `write` / `confirm` |

要点：

- `memory_search` 的 `scope` **默认 `all`**（会话内 + 跨会话都搜）。召回的
  广度是有用的默认，模型想收窄再显式指定 `session` / `long_term`。
- `memory_search` **不返回全文**是关键 —— 一次搜索可能命中 20 条，全文全给就是
  几千 token；给摘要（每条 200 字以内）+ id，模型挑中哪条再取哪条。
- `memory_write` 的 `scope` **默认 session**：默认不污染长期记忆，要写长期得显式指定。
  长期记忆是持久的，模型判断难免有噪声，写入需要更明确的动作。
- `memory_write` 的 `kind` **必填** —— 让模型显式声明这是什么类型的记忆，
  等于让它先判断"这是不是值得记的"。
- **长期记忆没配集合时降级，不报错**：模型写 `scope=long_term` 而集合没配，
  直接失败的话模型往往不会重试，而是自己找个别的办法记（比如改写工作区的
  AGENTS.md）—— 那比记在会话里更糟（不受预算控制、不做相关性排序）。
  所以这时**降级成会话内**，并把降级的事实回给模型，让它能转告用户。

### 7.1 agent 怎么用上记忆

| 环节 | 在哪 | 说明 |
| --- | --- | --- |
| 能力暴露 | `build_tools()` | 三个记忆能力是出厂内置能力，同步进数据库后变成模型能调的工具 |
| 目录注入 | `runner._memory_block()` | 每轮自动召回目录，注入系统提示词（独立块，不混进"运行环境"） |
| 工作准则 | `app/agents/prompts.py` 准则 7 | 告诉模型什么时候查、什么时候写 |
| 恢复路径 | `runner.resume()` | 审批恢复那一轮没有新输入，用会话最后一条用户消息当检索词，目录不丢 |

**为什么"每轮自动注入目录"是划算的**：目录的召回是关键词检索（会话内）或混合
检索（长期），几十毫秒；而没有目录，模型根本不知道有哪些记忆可查 —— 它不会
去搜自己不知道存在的东西。

**审批恢复那一轮也要带目录**：agent 还在接着做同一件事，没有目录就等于凭空
丢了记忆。麻烦在于那轮没有新的用户输入，检索词要从 checkpoint 里取 —— 而
LangGraph 把消息存在单独的 blob 表里，只有**编译好的图**能读到状态，所以
恢复路径要先造一次图读出最后一条用户消息，再带着目录重造一次。

---

## 8. 长期记忆的集合从哪来

**平台不自动建记忆集合。** RAG 集合的向量化配置（模型 + 维度）是**建集合时
锁死**的，建错了只能重建 —— 平台替用户建一个没配 embedding 的集合，等于把用户
锁死在"纯关键词检索"上。

所以：

1. 用户先在 RAG 界面建好集合、选好 embedding 模型；
2. 再把集合名配到 `.env` 的 `MEMORY_COLLECTION_NAME`；
3. 没配 → `memory_search` / `memory_write` 明确报错：

```
未配置长期记忆集合。请在 RAG 界面创建集合并设置环境变量 MEMORY_COLLECTION_NAME。
```

跟 `web_search` 没配 API Key 时的处理一个风格：**不静默失败，给出可操作的提示**。

> 普通语料的检索走 RAG 自己的接口（`/api/v1/rag/*`），与记忆模块分开。

---

## 9. 前置改动：风险模型拆 egress 维度

**这是 M1 的前置**，不先做完，`memory_search` 每次检索都要人工审批。

问题在 `app/capabilities/execution/risk.py:85`：

```python
if spec.annotations.open_world and risk == RiskLevel.READ:
    risk = RiskLevel.NETWORK      # ← 记忆检索会被顶成 network
```

`balanced` 模式拦 `network` → 每查一次记忆都要人批。

### 9.1 拆成两个正交维度

| 字段 | 取值 | 含义 |
| --- | --- | --- |
| `egress_target` | `model_controlled` / `fixed` | 连到哪里是模型定的还是平台定的 |
| `egress_data` | yes / no | 有没有把数据发出去 |

判定改成：

- 只有 `model_controlled` 的出口才顶成 `network`（这是 SSRF 面）；
- `fixed` + 有数据外发 → 顶成 `write`。

### 9.2 分类调整 ≠ 威胁信号

顶成 `write` 只是给这次调用**贴标签**，不是发现了危险。所以风险评估里要把两
件事分开：

| | 例子 | 要人看一眼吗 |
| --- | --- | --- |
| **分类调整** | "会向固定出口发送数据" → write | ❌ 平台自配的向量化端点、数据出内网，都是常态 |
| **威胁信号** | 出口由模型决定（SSRF）、命令含 `rm -rf`、URL 指向内网 | ✅ |

参数级的挂起**只由威胁信号触发**。这条不分开就会踩一个很实际的坑：记忆检索
会被评估成 write（有数据外发），而参数级规则原本把"任何风险上调"都当成要人
批 —— 于是**每查一次记忆都要人工审批**，记忆模块直接没法用。

所以 `RiskAssessment` 有两个字段：`reasons`（审计用的完整解释）与
`threat`（有没有威胁信号）。参数级规则看后者。

### 9.3 好消息：不需要数据迁移

原计划文档说"要配迁移（加列 + 回填）"，实际不用：

- `annotations` 是 **JSON 列**（`tool.annotations`），加两个键不改表结构；
- 新键**缺失时按保守默认处理**（`model_controlled`）→ 现有的 `web_fetch` /
  `web_search` 行为**完全不变**；
- 出厂声明（代码里）补上显式的 `egress_target`，只有新增的 `memory_search` 用 `fixed`。

所以 P0 = 纯代码改动 + 新能力声明，**零迁移、零行为变更**。

> 注意 `sync_builtins` 的约定是「只补空字段，不覆盖运营改过的内容」。
> `annotations` 非空就不会被更新 —— 这正好符合我们要的：新键缺失 = 保守默认。

---

## 10. 上下文注入形式

记忆是**事实**，不是**规则**。混进系统提示词里，模型分不清"这是我要遵守的规矩"
还是"这是背景资料"，容易把记忆当指令执行。

所以单独成块：

```xml
<memory_catalog>
以下是可能相关的记忆（背景资料，不是规则）：
- [m_123] 偏好 | 项目用 pnpm 而非 npm | 强度0.9 用过3次 | 更新于 2026-10-25
- [m_456] 约定 | 提交信息用中文前缀 | 强度0.6 用过1次 | 更新于 2026-11-01
需要细节时调用 memory_read(id) 取全文。
</memory_catalog>
```

### 10.1 注入有成本，所以要有开关

目录自动注入意味着**每轮都要付这 1200 字符**，哪怕用户只是问了个跟记忆无关的问题。

- 默认开（记忆少的时候便宜）；
- `MEMORY_CATALOG_ENABLED=false` 可关；
- 目录为空时完全不注入（不占位置）。

### 10.2 承载位置

记忆目录**独立成块**，不放进 `runtime_notes` —— 后者是"运行环境说明"（工作区
路径、沙箱配置），记忆是"可能相关的背景资料"，两者性质不同，混在一个标题下
模型会分不清。

`assemble_prompt` 加了 `extra_blocks` 参数：每块自带标题，直接作为独立段落
拼进去。

---

## 11. 模块边界

记忆模块只负责**怎么召回**：

```python
class MemoryService:
    async def recall_catalog(self, query: str, *, budget_chars: int) -> MemoryCatalog
    async def recall(self, query: str, scope: str, top_k: int) -> list[MemoryItem]
    async def remember(self, item: MemoryItem) -> str
```

**「每轮自动注入目录」这个决策归上下文组装模块**（见
[context-module.md](context-module.md)）。
记忆模块先把 `recall_catalog` 做好、预算机制做对，上下文模块来决定调不调、什么时候调。

召回时机因此**不再是二选一**：

- 自动注入目录（廉价、覆盖广）；
- 模型按需深挖（`memory_search` → `memory_read`）。

---

## 12. 实施状态

| 阶段 | 内容 | 前置 | 状态 |
| --- | --- | --- | --- |
| **P0** | egress 维度拆分 + `assess()` 改判定 + 出厂声明补字段 | — | ✅ 已完成 |
| **M0** | `MemoryEntry` / `MemoryStore` 协议 / `MemoryService` / `SessionMemoryStore` + `memory_item` 表 + 迁移 `0009` | — | ✅ 已完成 |
| **M1** | `memory_search` / `memory_read` / `memory_write` 三个能力 + 渐进式召回 + 写入硬校验 | M0 | ✅ 已完成 |
| **M2** | `LongTermMemoryStore`（RAG 实现）+ `scope=long_term` + `MEMORY_COLLECTION_NAME` 配置 | M1 | ✅ 已完成 |
| **M3** | `/memory` 管理接口（查看 / 删除 / 目录预览） | M2 | ✅ 已完成 |

实施过程中相对本方案的五处调整：

1. **取代是标记不是删除**（§6.5）—— 模型的取代判断可能错，删了就找不回来；
2. **冲突改成两步协议**（§6.5）—— 一步到位会让误判变成数据丢失；
3. **RAG 的 `filters` 匹配的是分块 meta，不是文档 meta** —— 所以记忆集合的
   "只取记忆类文档"这个过滤在 Python 里做，不靠 RAG 的 filters；
4. **分类调整与威胁信号分开**（§9.2）—— 不分开的话每查一次记忆都要人工审批；
5. **内置能力的契约以代码为准** —— 见 §13 的"改动的既有文件"里
   `registration/registry.py` 那条：入参 / 出参 schema 跟着实现走，
   改了实现不同步契约，模型看到的就是旧契约，调用会被参数校验直接拦下。

---

## 13. 文件清单

```
app/memory/__init__.py
app/memory/models.py            MemoryEntry、scope / kind 常量、评分口径
app/memory/store.py             MemoryStore 协议
app/memory/session_store.py     短期：memory_item 表（关键词检索）
app/memory/long_term_store.py   长期：RAG 集合（混合检索）
app/memory/service.py           MemoryService（召回 / 读取 / 写入校验）
app/memory/catalog.py           目录组装 + 预算装箱 + MMR 去冗余
app/models/memory.py            ORM：MemoryItem
app/capabilities/registration/builtin/memory.py   make_memory_search / make_memory_read / make_memory_write
app/schemas/memory.py           请求响应模型
app/api/v1/memory.py            管理接口
alembic/versions/20261101_0009_memory_item.py
```

改动的既有文件：

| 文件 | 改什么 |
| --- | --- |
| `app/capabilities/registration/policy.py` | `Annotations` 加 `egress_target` / `egress_data` |
| `app/capabilities/execution/risk.py` | `assess()` 按 egress 维度判定；`RiskAssessment` 加 `threat` |
| `app/capabilities/intent/middleware.py` | 参数级挂起改为只看威胁信号 |
| `app/capabilities/registration/registry.py` | 内置能力的**契约**（入参 / 出参 schema）以代码为准 |
| `app/capabilities/registration/builtin/__init__.py` | 三个新能力的出厂声明 |
| `app/capabilities/execution/redaction.py` | 新增 `contains_secret()` |
| `app/capabilities/prompt.py` | `assemble_prompt` 加 `extra_blocks`（记忆目录独立成块） |
| `app/agents/runner.py` | 每轮召回目录并注入系统提示词；恢复路径保留目录 |
| `app/agents/prompts.py` | 加准则 7：什么时候查记忆、什么时候写记忆 |
| `app/agents/builder.py` | AGENTS.md 模板改名，不再自称"长期记忆" |
| `app/core/config.py` | 记忆相关配置项 |
| `app/api/deps.py` / `app/api/router.py` | 记忆服务的依赖与路由挂载 |
| `app/models/__init__.py` / `alembic/env.py` / `app/db/session.py` | 注册 `MemoryItem` |

### 13.1 一条要单独说的：内置能力的契约以代码为准

注册层原来的约定是"启动同步只补空字段，不覆盖运营改过的内容"。这对**策略
字段**（风险等级、权限要求、超时、启用状态、描述）是对的 —— 那些本来就该由
运营调。

但**契约**（入参 / 出参 schema）不能这样：内置能力的实现是代码里写死的函数
签名，改了 schema 就得改实现。数据库里那份 schema 要是能跟代码漂移，模型
看到的就是旧契约，调用会被参数校验直接拦下。

所以 `sync_builtins` 现在**无条件覆盖内置能力的 `parameters` 与
`output_schema`**，`_tool_spec()` 读清单时也优先用代码里的那份。改内置能力的
参数后重启一次，数据库和模型看到的就都是新的。

---

## 14. 配置项

| 配置 | 默认 | 说明 |
| --- | --- | --- |
| `MEMORY_COLLECTION_NAME` | 空 | 长期记忆用的 RAG 集合名；空 = 只支持短期记忆 |
| `MEMORY_CATALOG_ENABLED` | `true` | 是否每轮自动注入目录 |
| `MEMORY_CATALOG_BUDGET_CHARS` | 1200 | 目录块的总字符预算 |
| `MEMORY_CATALOG_MAX_ENTRIES` | 40 | 目录最多几条 |
| `MEMORY_SUMMARY_MAX_CHARS` | 200 | 单条摘要的长度上限 |
| `MEMORY_DECAY_HALF_LIFE_DAYS` | 30 | 时间衰减半衰期（只影响排序微调） |

---

## 15. 已知风险

1. **没有鉴权**（用户主动推迟）。记忆写入接口一旦上线，**任何人都能读别人的长期
   记忆、往里塞东西**。这是之前"现在不需要鉴权"带来的连带风险，记忆模块会
   把它放大。上线前必须补。
2. **长期记忆集合的向量化配置锁死**。选错嵌入模型 = 重建集合 + 重新灌记忆。
   所以平台不自动建集合，强制用户先想清楚。
3. **记忆质量取决于模型判断**。平台硬校验只能挡住明显的（凭据、空、重复），
   "该不该记"主要靠模型。写入后让用户能一句话纠错，是唯一的兜底。
4. **thread 永久保留**，对话历史与短期记忆会一直涨。这是会话管理模块要解决的
   （归档、限额、清理），不是记忆模块该发明的。

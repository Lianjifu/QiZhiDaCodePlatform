# RAG 服务层设计

> 状态：**R0 + R1 已实现**（2026-11-01）
>
> 关联：记忆模块（短期 / 长期两层）是本层的**消费者**，已实现 ——
> 见 [implementation/memory-module.md](memory-module.md)。
>
> 本文只描述**已经落地**的部分；未实现的阶段与前置改动都在 plans/ 下。

## 1. 为什么单独一层

RAG 不是能力层的一个能力，也不是记忆模块的一部分。它是**通用检索基础设施**：

- 记忆模块的长期记忆要落在 RAG 上（可检索、可更新、可淘汰）；
- 任何需要"把一堆文本切块、检索出来"的场景都是同一套。

所以把它做成**独立的一层** `app/rag/`，不依赖能力层、不依赖记忆模块。
记忆模块接进来时，是它来消费 RAG，不是反过来。

```text
        记忆模块（短期 / 长期）        其他语料检索场景
                  │                            │
                  └──────────┬─────────────────┘
                             ▼
                    ┌──────────────────┐
                    │   RAG 服务层     │
                    └──────────────────┘
```

## 2. 目录

```text
app/rag/
  models.py         ORM：集合 / 文档 / 分块
  chunking.py       分块策略（Markdown / 代码 / 纯文本）
  embedding.py      向量化（复用模型接入配置）
  vector_store.py   存储抽象（VectorStore 协议）
  stores/sql.py     SQL 实现（SQLite / Postgres 同一套）
  retriever.py      混合检索（向量 + 关键词，RRF 融合）
  service.py        业务：集合 / 文档 / 检索
```

依赖方向是单向的：`service → retriever → vector_store ← stores/sql`，
`service → embedding`，`service → chunking`。**上层不知道底层存在什么实现**。

## 3. 数据模型

```text
collection（一个语料集合，绑定一套向量化配置）
  └─ document（一篇原始文档，按内容哈希去重）
       └─ chunk（检索单元，带向量）
```

| 表 | 关键列 | 说明 |
| --- | --- | --- |
| `rag_collection` | `embedding_model` `embedding_dim` `provider_id` | 向量化配置**锁在集合上** |
| `rag_document` | `content_hash` | 同集合内内容一样就是同一篇，不重复入库 |
| `rag_chunk` | `embedding` `dim` `seq` `meta` | 检索单元 |

### 为什么集合必须绑定固定的向量化配置

不同模型的向量**不在同一个空间里**。同一个查询向量跟 A 模型的文档向量算
余弦，跟 B 模型的文档向量算余弦，得到的数没有可比性 —— 混着比出来的
相似度是噪声，不是"稍微差一点"，是完全没有意义。

所以：

- 模型名和维度记在**集合**上，不是全局配置；
- 建集合时探一次维度（真发一次请求），拿到的维度写进集合；
- 入库时校验每条向量的维度跟集合一致，不一致直接报错；
- **换模型 = 重建集合**，不是改个字段。

## 4. 分块

切法随内容类型变，因为不同内容的"语义单元"不一样。

| 类型 | 切法 | meta 里带什么 |
| --- | --- | --- |
| Markdown | 按标题层级切，标题路径进 meta | `heading` `level` `path` |
| 代码 | 按顶层定义切（函数 / 类），装饰器和 docstring 跟着走 | `symbol` `kind` |
| 纯文本 | 固定窗口 + 重叠，按段落边界对齐 | `position` `overlap` |

- 分块按**字符数**算，不按 token 数 —— 分块发生在调模型之前，用不着先分词；
- token 数是估算值（中文一字一 token，其余 4 字符一 token），只用于展示；
- 一次入库的内容**不丢**（拼回去等于原文，忽略空白）。

`format="auto"` 时按内容特征判断（有围栏代码块 / 标题 → Markdown，
有顶层 `def` / `class` → 代码），也可以按文件后缀判断，或者在请求里显式指定。

## 5. 向量化

**复用现有的模型接入配置，不引入新基础设施。**

绝大多数厂商（OpenAI、通义、智谱、硅基流动、Kimi…）在同一个 `base_url` 上
同时提供对话和向量化接口，差别只是模型名。所以：

- `llm_provider` 表加两列：`embedding_model`（模型名）、`embedding_dim`（维度）；
- 向量化请求直接打 `{base_url}/embeddings`，OpenAI 兼容格式；
- 没配 `embedding_model` 的供应商不影响对话，只是不参与向量化。

**没有向量化也能用**：集合不配 `embedding_model` 时走纯关键词检索，
RAG 层不会因此不可用。这一点很重要 —— 关键词检索在"查函数名、查环境变量名"
这类场景下比向量检索还准。

本地模型（BGE 之类）留了口子：`EmbeddingProvider` 是协议，换实现即可，
上层不用动。

## 6. 检索：必须混合

**只上向量会漏。** 向量擅长"意思相近"，但把下划线、驼峰这些细节抹平了。
查 `DATABASE_URL` 这种环境变量名、查 `parse_config` 这种函数名，
经常召不回。

**只上关键词也会漏。** 问"怎么配置数据库"，召不回那段写着
`DATABASE_URL=postgres://...` 的内容。

两路结果用 **RRF（倒数排名融合）** 合并：

```
score(d) = Σ 权重 / (k + rank(d))          k = 60
```

用排名而不是分数来融合，是因为两路的分数不可比（余弦在 0-1，BM25 无上界），
归一化各归一化、各有各的毛病；排名是稳的。

关键词一路用 **BM25**（不是词频计数）—— 长文档里出现 10 次的常见词
不该比出现 1 次的专有名词得分高。分词对中文用**字符二元组**
（"记忆检索" → "记忆""忆检""检索"），对英文按词切，并把 `foo_bar`
拆成 `foo` 和 `bar`。

### 模式

| mode | 行为 |
| --- | --- |
| `hybrid`（默认） | 向量 + 关键词，RRF 融合 |
| `vector` | 只走向量 |
| `keyword` | 只走关键词 |

集合没启用向量化时，`hybrid` / `vector` 自动退化成关键词，并在结果的
`degraded` 字段里说明原因 —— 不因为配置缺失就让检索整个不可用。

### 规模

v1 的向量在 SQL 里（JSON 列），检索在进程内算余弦；关键词也是进程内 BM25。
几千条分块毫秒级，**不引入任何新依赖**。

规模上去了有两条路，都在 `VectorStore` 这一层后面：

- 换存储：Postgres 换 pgvector（asyncpg 已经是依赖了）；
- 换索引：关键词检索换成 SQLite FTS5 / Postgres tsvector。

接口不变，上层不用动。

## 7. 接口

```text
GET    /api/v1/rag/collections                   集合列表
POST   /api/v1/rag/collections                   新建集合（配了模型会探维度）
GET    /api/v1/rag/collections/{id}              集合详情
DELETE /api/v1/rag/collections/{id}              删集合（连同文档与分块）

GET    /api/v1/rag/collections/{id}/documents    集合里的文档
POST   /api/v1/rag/collections/{id}/documents    入库一篇文档
DELETE /api/v1/rag/documents/{id}                删文档（连同分块）

POST   /api/v1/rag/search                       检索
```

入库请求：

```json
POST /api/v1/rag/collections/1/documents
{ "title": "数据库配置", "content": "用 DATABASE_URL 连库…", "format": "auto" }
```

检索请求：

```json
POST /api/v1/rag/search
{ "query": "数据库怎么连", "collection_id": 1, "top_k": 5, "mode": "hybrid" }
```

## 8. 实施情况

| 阶段 | 内容 | 状态 |
| --- | --- | --- |
| **R0** | 集合/文档/分块模型 + 分块 + SQL 存储 + 关键词检索 | ✅ 已实现 |
| **R1** | 向量化（复用模型接入配置）+ 向量检索 + 混合融合 + 重排钩子 | ✅ 已实现 |

后续阶段（M0 记忆模块骨架、M1 长期记忆落在 RAG 上）已随记忆模块一起实现，
见 [implementation/memory-module.md](memory-module.md)。

### 已实现的细节

- 分块：Markdown 按标题、代码按顶层定义、纯文本窗口 + 重叠；
- 关键词：BM25 + 中文二元组 + 标识符拆分 + 整串命中加分；
- 向量化：OpenAI 兼容端点，复用 `llm_provider`（密钥走 Fernet 加密存储）；
- 建集合时探维度，入库时校验维度；
- 文档按内容哈希去重，重复入库直接跳过；
- 混合检索 RRF 融合，`degraded` 字段说明降级；
- 关键词分词结果带缓存（检索是扫全库的活，不缓存每次都要重新分词）。

### 还没做的

- **重排（rerank）**：接口留了钩子（`Retriever(reranker=...)`），没有接具体实现；
- **跨集合检索**：现在一次查一个集合；
- **增量更新**：文档内容变了要重新入库（目前按内容哈希去重，不做 diff）；
- **本地向量模型**：`EmbeddingProvider` 是协议，实现待接；
- **pgvector / FTS5**：见上面「规模」。

这些待办的排期与取舍见 [plans/rag-followups.md](../plans/rag-followups.md)。

---

## 9. 记忆模块依赖的 egress 维度拆分

见 [implementation/memory-module.md](memory-module.md#9-前置改动风险模型拆-egress-维度) ——
能力层的风险模型要拆出 `egress_target` / `egress_data` 两个维度，
否则平台自配的向量化端点会被误判成 `network` 风险。

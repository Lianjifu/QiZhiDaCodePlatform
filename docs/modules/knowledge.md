# 知识库（RAG）模块

> 一句话：通用语料检索基础设施 —— 把一堆文本切块、建索引、按需检索出来；
> 集合 / 文档 / 分块三层，混合检索（向量 + 关键词）用 RRF 融合。
>
> 前端入口：纯后端，无独立页面。集合、文档、检索目前只有 `/api/v1/rag/*`
> 接口；模型侧通过 `rag_search` / `rag_read` 两个能力访问。

## 1. 这个模块是什么

### 1.1 独立的一层，不依赖任何上层

RAG 不是能力层的一个能力，也不是记忆模块的一部分。它是**通用检索基础设施**：

- 记忆模块的长期记忆**建在 RAG 集合上**（见 [memory.md](memory.md)）；
- 任何需要"把一堆文本切块、检索出来"的场景都是同一套。

所以它做成**独立的一层** `app/rag/`，不依赖能力层、不依赖记忆模块。RAG 可以
先单独用起来（接口直接调、自己灌语料），记忆模块接入时不用反过来改 RAG。

```text
        记忆模块（短期 / 长期）        其他语料检索场景
                  │                            │
                  └──────────┬─────────────────┘
                             ▼
                    ┌──────────────────┐
                    │   RAG 服务层     │
                    └──────────────────┘
```

依赖方向单向：`service → retriever → vector_store ← stores/sql`，
`service → embedding`，`service → chunking`。**上层不知道底层存在什么实现。**

### 1.2 三层数据模型

```text
collection（一个语料集合，绑定一套向量化配置）
  └─ document（一篇原始文档，按内容哈希去重）
       └─ chunk（检索单元，带向量）
```

| 表 | 关键列 | 说明 |
| --- | --- | --- |
| `rag_collection` | `name`（唯一索引）、`description`、`scope`、`embedding_model`、`embedding_dim`、`provider_id`、`chunk_size`、`chunk_overlap`、`doc_count`、`chunk_count`、`meta` | 检索的边界，向量化配置**锁在集合上** |
| `rag_document` | `collection_id`（FK）、`title`、`source`、`format`、`content`、`content_hash`（索引）、`chunk_count`、`meta` | 原文，同集合内内容一样就是同一篇 |
| `rag_chunk` | `collection_id`、`document_id`、`seq`、`content`、`embedding`（JSON 列）、`dim`、`token_count`、`meta` | 检索单元；复合索引 `ix_rag_chunk_collection_seq` |

- `rag_collection.scope` 取 `private` / `shared`。**平台无鉴权，这个字段目前
  不起隔离作用** —— 它只是集合上的一个标记。
- `rag_document.format` 取 `text` / `markdown` / `code`，存的是"这份文档到底是
  什么格式"，不是"用户请求里说了什么"（见 §3.3）。
- `rag_chunk.dim` 记的是**该向量实际维度**，用来发现"混了不同模型的向量"
  这种事故。
- `doc_count` / `chunk_count` 是冗余计数，改文档时同步维护（删文档时回退）。

## 2. 接口清单

接口在 `app/api/v1/rag.py`，prefix `/rag`，总前缀 `/api/v1`。
**平台无鉴权**：所有接口谁都能调。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/rag/collections` | 集合列表 |
| POST | `/rag/collections` | 新建集合（201）。**配了 `embedding_model` 会真发一次向量化请求探维度** |
| GET | `/rag/collections/{collection_id}` | 集合详情 |
| DELETE | `/rag/collections/{collection_id}` | 删集合，连同文档与分块（204） |
| GET | `/rag/collections/{collection_id}/documents` | 集合里的文档。`page`≥1、`page_size` 1~100（默认 20） |
| POST | `/rag/collections/{collection_id}/documents` | 入库一篇文档（201）。返回 `chunk_count`、`skipped`、`keyword_only` |
| DELETE | `/rag/documents/{document_id}` | 删文档，连同分块（204） |
| POST | `/rag/search` | 检索。`collection_id` **单数**；`top_k` 1~50（默认 8）；`mode`=hybrid/vector/keyword；`min_score`；`filters` |

**没有更新集合的接口** —— embedding 模型、维度、chunk 参数建时就定死
（见 §3.2）。

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

### 2.1 给模型的两个能力

注册在 `app/capabilities/registration/builtin/rag.py`，与记忆那一对同构。

| 能力 | 参数 | 返回 | 风险 | 权限 | 超时 |
| --- | --- | --- | --- | --- | --- |
| `rag_search` | `query`、`collection`（留空 = 搜全部集合，**记忆集合除外**）、`top_k`（默认 5，最多 20） | 命中片段的标题 / 来源 / 摘要（不给整篇） | read | none | 30s |
| `rag_read` | `document_id` | 一篇文档的全文 | read | none | 30s |

两个能力的 `annotations.egress_target` 都是 `fixed`：检索要打平台自己配的
向量化端点，地址不是模型给的。不按 `network` 风险拦，否则每次查知识库都要人工
审批。超时比记忆宽（30 秒 vs 20 秒）：一次检索可能要连着查好几个集合。

## 3. 实现要点

### 3.1 为什么集合必须绑定固定的向量化配置

不同模型的向量**不在同一个空间里**。同一个查询向量跟 A 模型的文档向量算余弦，
跟 B 模型的文档向量算余弦，得到的数没有可比性 —— 混着比出来的相似度是噪声，
不是"稍微差一点"，是完全没有意义。

所以模型名与维度记在**集合**上，不是全局配置：

- 建集合时探一次维度（真发一次请求），拿到的维度写进集合；
- 入库时校验每条向量的维度跟集合一致，不一致直接报错；
- **换模型 = 重建集合**，不是改个字段。

### 3.2 「向量化配置建时锁死」——代码位置与后果

这是这一层最硬的一条约束，改错了没有回头路。

**代码位置**：

| 环节 | 位置 | 行为 |
| --- | --- | --- |
| 建集合 | `app/rag/service.py::RagService.create_collection` | 配了 `embedding_model` 就 `probe_dimension(build_provider(target))` **真发一次向量化请求**，把维度写进 `rag_collection.embedding_dim`，连同 `embedding_model`、`provider_id` 三列一起落库 |
| 三列落库 | `app/rag/models.py::RagCollection` | `embedding_model` / `embedding_dim` / `provider_id` 建时写死 |
| 入库 | `RagService.ingest_document` → `ensure_dimensions(vectors, collection.embedding_dim)` | 二次校验维度，不一致直接抛 `ValidationError` |
| 检索 | `RagService._embedder_for` | 集合配了模型但供应商配置取不到时，记 warning 并退化为关键词 |
| 接口 | `app/api/v1/rag.py` | 只有建 / 读 / 删，**没有 update** |

**后果**：

- **模型名写错、密钥不对、端点不通，在建集合那一步就报出来**
  （`probe_dimension` 抛错 → `create_collection` 转成 `ValidationError`），
  而不是入库之后才发现。
- **建完之后改不了**。想换嵌入模型，只能新建一个集合、重新灌文档、重新切分
  重新向量化，然后删掉旧的。集合里的东西不搬。
- **选错模型 = 长期记忆报废**。长期记忆集合一旦建好并灌了记忆，换模型就得
  重建集合 —— 重建会把里面的记忆一起没掉。这一步要人自己决定。
- **没配模型的集合不是坏了**。`embedding_model` 为空 = 只有关键词索引，
  检索照常能用（`keyword_only=True`）。在"查函数名、查环境变量名"这类场景下，
  关键词检索比向量检索还准。

### 3.3 分块：三种策略

切法随内容类型变，因为不同内容的"语义单元"不一样（`app/rag/chunking.py`）。

| 类型 | 切法 | meta 里带什么 |
| --- | --- | --- |
| **纯文本** `text` | 固定窗口 + 重叠，切点对齐段落边界 | `format`、`position`、`overlap` |
| **Markdown** `markdown` | 按标题层级切，标题路径进 meta | `heading`、`level`、`path`、`position` |
| **代码** `code` | 按顶层定义切（函数 / 类），装饰器跟它后面的定义同块，文件头单独成块 | `kind`（header / definition）、`symbol`、`position` |

细节：

- **块大小按字符数算，不是 token 数** —— 分块发生在调模型之前，用不着先分词。
  token 数是估算值（中文一字一 token，其余 4 字符一 token），只用于展示。
- 纯文本：单段超长时按窗口**硬切**，硬切出来的块标 `split:"hard"`；重叠用
  段落回填（从上一块末尾往前找段落，攒够 overlap 就停），回填后超长的块跳过。
- Markdown：一节太长就再按段落切，**不重叠**（标题已经提供了定位线索，再叠一层
  反而让标题重复出现）；只有标题没有正文时留一个短块并标 `empty: True` —— 标题
  本身也是检索线索。
- 代码：装饰器跟它后面的定义算**同一块** —— 装饰器单独成块的话，检索到装饰器
  却看不到被装饰的函数，等于没用。
- 三种切法都保证**不丢内容**（拼回去等于原文，忽略空白）。

**`format="auto"` 在入库时定下来**并存进 `rag_document.format`。判定顺序：显式
文件后缀优先（`md`/`markdown`/`mdx` → markdown；`py`/`js`/`ts`/… → code；
`txt`/`log`/`rst` → text），否则按内容特征（有围栏代码块或标题 → markdown；
有顶层 `def`/`class` → code）。

### 3.4 入库与删除链路

`RagService.ingest_document`（`app/rag/service.py`）：

1. 取集合（不存在直接 `NotFoundError`）；
2. 校验内容非空、长度不超过 `rag_max_document_chars`；
3. **按 `content_hash` 判重**：同集合内内容一样就直接返回既有文档
   （`skipped=True`），不重复切分、不重复向量化；
4. 解析格式（`auto` → `detect_format`），定下 `rag_document.format`；
5. `chunk_document` —— 用的是**集合上的** `chunk_size` / `chunk_overlap`，
   不是全局默认值；
6. 集合配了 `embedding_model` 才向量化，`ensure_dimensions` 校验维度；
7. 写 `rag_document` → 写 `rag_chunk`（向量先 L2 归一化，`dim` 记实际长度）；
8. 集合的 `doc_count` / `chunk_count` 累加，提交。

返回的 `IngestResult` 带 `document` / `chunk_count` / `skipped` / `keyword_only`
—— `keyword_only` 告诉调用方这一篇只有关键词索引。

没给标题就取正文第一行截到 60 字（`_default_title`）。

删除是级联的：

| 操作 | 连带 | 计数 |
| --- | --- | --- |
| 删文档 | 该文档的全部分块一起删 | 集合 `doc_count` / `chunk_count` 回退（不低于 0） |
| 删集合 | 该集合的全部分块与全部文档一起删 | 集合行本身删掉 |

### 3.5 检索链路

接口层只做参数接收与响应转换，业务全在 `RagService.search`：

```text
API  /rag/search
  └─ RagService.search
       ├─ get_collection(collection_id)          不存在 → 404
       ├─ _embedder_for(collection)              配了模型但供应商配置取不到 → warning + NullEmbeddingProvider
       ├─ Retriever(store, embedder, weights)    ⚠ 不传 reranker
       ├─ Retriever.search(SearchRequest)
       │    ├─ keyword_search / vector_search    两路各取 candidate_size=50 条
       │    ├─ _fuse                             RRF 融合，归一化到 0~1
       │    ├─ filters 过滤                      在融合之后
       │    └─ min_score / top_k 截断
       └─ _attach_documents                      批量回填 title / source
```

`_attach_documents` 一次把命中文档全取回来回填标题与来源 —— 一条一条查是 N+1。

### 3.6 混合检索：RRF 融合

**只上向量会漏**：向量擅长"意思相近"，但把下划线、驼峰这些细节抹平了。查
`DATABASE_URL` 这种环境变量名、查 `parse_config` 这种函数名，经常召不回。

**只上关键词也会漏**：问"怎么配置数据库"，召不回那段写着
`DATABASE_URL=postgres://...` 的内容。

两路结果用 **RRF（倒数排名融合）** 合并（`app/rag/retriever.py`）：

```
score(d) = Σ 权重 / (k + rank(d))          k = _RRF_K = 60
```

用排名而不是分数来融合，是因为两路的分数不可比（余弦在 0-1，BM25 无上界），
归一化各归一化、各有各的毛病；排名是稳的。k 取 60 是原论文的经验值，作用是让
"排在第 1"和"排在第 10"的差距不至于太夸张。

- 向量权重与关键词权重各默认 1.0（`RAG_VECTOR_WEIGHT` / `RAG_KEYWORD_WEIGHT`）。
- 融合分**归一化到 0~1**：除以 `(vector_weight + keyword_weight) / (_RRF_K + 1)`
  —— 那是两种路都满分的理论上限。
- `vector_score` / `keyword_score` 保留原始值，方便调效果时看得见是哪一路捞的。

模式：

| mode | 行为 |
| --- | --- |
| `hybrid`（默认） | 向量 + 关键词，RRF 融合 |
| `vector` | 只走向量（融合分直接用余弦，本身就在 0-1） |
| `keyword` | 只走关键词（BM25 按最高分归一） |

**向量不可用时自动退化为纯关键词**，原因写进 `SearchResult.degraded`：

- `mode=vector` 且向量一路没结果 → 真的退回关键词，`mode` 也改成 `keyword`；
- `mode=hybrid` 且向量一路没结果 → 保留 `hybrid`，只记 `degraded`。

不因为配置缺失就让检索整个不可用。

**结构化过滤放在融合之后**（`request.filters`）：过滤掉的结果**不占坑位** ——
先融合再过滤，被过滤掉的分块不会把后面的挤下去。`_meta_matches` 匹配的是
**分块** meta（子集匹配：filters 里每个键都要在 meta 里有相等的值）。

### 3.7 关键词检索：BM25 + 分词

`app/rag/stores/sql.py::keyword_rank`：

- BM25 参数 `k1=1.5`、`b=0.75`。用 BM25 而不是词频计数：长文档里出现 10 次的
  常见词不该比出现 1 次的专有名词得分高。
- **整串命中额外加分** `_EXACT_BONUS = 3.0`：查 `DATABASE_URL` 时，文档里原样
  出现这个串，比只出现 `URL` 相关词更可信。

分词（`tokenize`）三种粒度混在一起，因为检索场景本来就跨这三类：

| 场景 | 切法 | 例 |
| --- | --- | --- |
| 自然语言（中文） | 字符二元组 | "记忆检索" → "记忆""忆检""检索" |
| 英文 | 按词 | `parse_config` |
| 标识符 | `foo_bar` 拆两段、驼峰再拆 | `foo_bar` → `foo`,`bar`；`parseConfig` → `parse`,`config` |

分词结果带**进程内缓存** `_TOKEN_CACHE`（上限 20000）：检索是"扫全库"的活，
不缓存的话每次查询都要把全库重新分一遍词。**超上限时整个字典清空**，不是 LRU。

### 3.8 向量化：复用模型接入配置

**不引入新基础设施。** 绝大多数厂商（OpenAI、通义、智谱、硅基流动、Kimi…）在
同一个 `base_url` 上同时提供对话和向量化接口，差别只是模型名。

- `llm_provider` 表上有 `embedding_model`（模型名）与 `embedding_dim`（维度）。
  没配 `embedding_model` 的供应商不影响对话，只是不参与向量化。
- 端点形态是 OpenAI 兼容的 `{base_url}/embeddings`，请求体
  `{"model": ..., "input": [...]}`，响应 `{"data": [{"index", "embedding"}]}`。
  实现里会按 `index` 重排一次（有的实现不保证顺序）。
- **端点与密钥不落库**：集合里只记模型名与维度，调用时从供应商配置里取
  （`llm_provider.api_key_encrypted`，解密失败不致命 —— 本地端点本来就不要密钥）。
- 数据库里没配置时退到环境变量 `RAG_EMBEDDING_MODEL` / `RAG_EMBEDDING_BASE_URL`
  / `RAG_EMBEDDING_API_KEY`（本地开发兜底）。
- 向量入库前做 **L2 归一化**，检索时余弦就是点积，少一次开方。

`EmbeddingProvider` 是协议（`app/rag/embedding.py`），现有两个实现：
`OpenAICompatibleEmbeddingProvider` 与 `NullEmbeddingProvider`（不向量化，
`embed` 返回空列表）。

### 3.9 给 agent 的两个能力

`rag_search`（`app/capabilities/registration/builtin/rag.py`）：

- `collection` 留空 → 遍历**全部集合**；**显式点名某个集合 → 只搜那一个**。
- **扫全部集合时排除记忆集合**（`settings.memory_collection_name`）：记忆有
  自己的一套召回语义 —— 被取代的记忆不参与召回、模型写的东西按"模型说的"
  降权。走 RAG 原始检索会把这些规则全绕过去，agent 会读到已经作废的记忆。
  记忆交给 `memory_search`。**显式点名记忆集合时仍然可以搜**（点名优先于排除）。
- **按文档去重**：一篇文档可能被多个分块命中，只保留分数最高的那块。
- 每条命中给模型的片段**截 400 字**（`SNIPPET_CHARS`）：20 条 × 400 字 ≈ 8000
  字符 ≈ 两千多 token。一次给太多，模型会在一堆不相干的片段里翻找。
- **只报"配了向量化却没跑成"的降级**：集合压根没配 embedding 模型（纯关键词）
  是用户的配置选择，不是故障，报成"降级"会让人以为哪里坏了。

`rag_read` 按 `document_id` 取全文，连带集合名、标题、来源、格式与 meta。

### 3.10 不变量（改错了会怎样）

| 不变量 | 改错了会怎样 |
| --- | --- |
| 集合的 `embedding_model` / `embedding_dim` / `provider_id` **建时写死** | 集合里混进不同模型的向量，相似度变成噪声；且没有 update 接口可以补救 |
| 入库前 `ensure_dimensions` 校验维度 | 半库半向量，检索结果一半是噪声而且看不出来 |
| 融合**先于**过滤 | 被过滤掉的分块占掉坑位，后面真正符合条件的被挤出去 |
| `filters` 匹配的是**分块** meta | 挂在文档上的 meta（`kind`、`memory_kind` 等）过滤不到 —— 长期记忆的过滤因此放在 Python 里做 |
| 文档按内容哈希去重（**同集合内**） | 同一篇文档重复入库，重复切分重复向量化，检索里出现多条同样的内容 |
| 分块按**字符数**算，不按 token | 中文与英文的"一个 token 多少字符"差很多，按 token 切会切出大小悬殊的块 |
| 装饰器与它后面的定义同块 | 检索到装饰器却看不到被装饰的函数，等于没用 |
| `rag_search` 扫全部集合时排除记忆集合 | agent 会读到已经被取代的旧记忆 —— 记忆那套召回语义被绕过去了 |
| `rag_search` / `rag_read` 的 `egress_target=fixed` | 按 `network` 风险拦 → 每次查知识库都要人工审批，知识库直接没法用 |
| 检索失败降级成关键词而不是报错 | 供应商配置临时出问题，整个知识库不可用 |

### 3.11 配置项

| 配置 | 默认 | 说明 |
| --- | --- | --- |
| `RAG_CHUNK_SIZE` | 1000 | 分块大小（字符） |
| `RAG_CHUNK_OVERLAP` | 150 | 分块重叠（字符） |
| `RAG_MAX_DOCUMENT_CHARS` | 200000 | 单篇文档长度上限 |
| `RAG_TOP_K` | 8 | 检索默认返回条数 |
| `RAG_VECTOR_WEIGHT` | 1.0 | RRF 里向量一路的权重 |
| `RAG_KEYWORD_WEIGHT` | 1.0 | RRF 里关键词一路的权重 |
| `RAG_EMBEDDING_MODEL` | 空 | 向量化模型名（数据库没配时的兜底） |
| `RAG_EMBEDDING_BASE_URL` | 空 | 向量化端点 |
| `RAG_EMBEDDING_API_KEY` | 空 | 向量化密钥 |
| `RAG_EMBEDDING_DIM` | 0 | 向量维度（兜底路径用） |
| `RAG_EMBEDDING_TIMEOUT` | 60 | 向量化请求超时（秒） |
| `RAG_EMBEDDING_BATCH_SIZE` | 32 | 一次向量化请求的文本条数 |

## 4. 已知限制

1. **重排没做**。`Retriever(reranker=...)` 留了钩子（`Reranker` 协议），但
   `RagService.search()` 从不传它 —— 结果直接按 RRF 融合分排序。
2. **跨集合检索接口层面没有**。`RagSearchRequest.collection_id` 是单数，一次只
   查一个集合。`rag_search` 工具层是自己遍历多个集合再按分数合并的。
3. **增量更新没有**。入库按内容哈希去重，**不做 diff**：内容改了要重新入库，
   同标题的新旧内容会并存，旧分块不清理。
4. **本地向量模型没有**。`EmbeddingProvider` 是协议，但只有 OpenAI 兼容一种实现。
5. **向量存在 SQL 的 JSON 列里，余弦在进程内算**；关键词检索也是进程内 BM25
   扫全库。几万条分块以上延迟与内存会明显上去。pgvector / FTS5 的接口位置已经
   留在 `VectorStore` 协议后面（`app/rag/vector_store.py`）。
6. **`SqlVectorStore.load_chunks` 每次检索把整个集合的分块全 load 进内存** ——
   `keyword_search` 与 `vector_search` 各 load 一次，`hybrid` 模式下就是两次。
7. **`_TOKEN_CACHE` 是进程内全局字典，上限 20000，超了整清**（不是 LRU）——
   缓存会周期性整体失效。
8. **文档长度上限 200000 字**（`rag_max_document_chars`），超了要自己切分后再入库。
9. **无鉴权**，且 `rag_collection.scope`（private/shared）目前不起隔离作用。
10. **没有更新集合的接口** —— embedding 模型、维度、chunk 参数建时定死，改不了。

## 5. 相关文件

```
app/rag/__init__.py
app/rag/models.py            ORM：RagCollection / RagDocument / RagChunk
app/rag/chunking.py          分块策略（文本 / Markdown / 代码）+ 格式判定
app/rag/embedding.py         EmbeddingProvider 协议、OpenAI 兼容实现、维度探测
app/rag/vector_store.py      VectorStore 协议 + ChunkHit
app/rag/stores/sql.py        SQL 实现（SQLite / Postgres 同一套）、BM25、分词
app/rag/retriever.py         混合检索 + RRF 融合 + 降级
app/rag/service.py           RagService（集合 / 文档 / 检索）
app/schemas/rag.py           请求响应模型
app/api/v1/rag.py            接口层
app/api/deps.py              get_rag_service
app/capabilities/registration/builtin/rag.py   rag_search / rag_read
app/capabilities/registration/builtin/__init__.py  两个能力的出厂声明与契约
app/core/config.py           RAG_* 配置项
```

迁移：`alembic/versions/20261031_0006_rag_layer.py`。

相关文档：[memory.md](memory.md)（长期记忆建在 RAG 集合上）。

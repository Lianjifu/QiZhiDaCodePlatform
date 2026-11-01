"""RAG 服务层：独立的检索增强层。

这一层**不依赖能力层、也不依赖记忆模块** —— 记忆模块只是它的一个消费者。
所以 RAG 可以先单独用起来：自己灌语料、自己查，记忆模块接入时不用反过来改 RAG。

分层：

```text
app/rag/
  models.py       ORM：集合 / 文档 / 分块
  chunking.py     分块策略（Markdown / 代码 / 纯文本）
  embedding.py    向量化（复用模型接入配置）
  vector_store.py 存储抽象
  stores/sql.py   SQL 实现（SQLite / Postgres 同一套）
  retriever.py    混合检索（向量 + 关键词，RRF 融合）
  service.py      业务：入库 / 检索
```
"""

from __future__ import annotations

__all__: list[str] = []

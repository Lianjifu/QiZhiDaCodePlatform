"""RAG 层测试：分块、向量化、混合检索、入库与检索全链路。"""

from __future__ import annotations

import json

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.rag.chunking import (
    FORMAT_CODE,
    FORMAT_MARKDOWN,
    chunk_document,
    chunk_markdown,
    chunk_text,
    detect_format,
    estimate_tokens,
)
from app.rag.embedding import (
    EmbeddingTarget,
    OpenAICompatibleEmbeddingProvider,
    cosine,
    normalize,
)
from app.rag.models import RagChunk, RagCollection, RagDocument
from app.rag.retriever import Retriever, SearchRequest
from app.rag.service import RagService
from app.rag.stores.sql import SqlVectorStore, keyword_rank, tokenize

# ---------- 分块 ----------


def test_detect_format_by_content():
    assert detect_format("# 标题\n\n正文") == FORMAT_MARKDOWN
    assert detect_format("def foo():\n    pass\n") == FORMAT_CODE
    assert detect_format("就是一段普通的话。") == "text"


def test_detect_format_by_filename():
    assert detect_format("随便什么", filename="README.md") == FORMAT_MARKDOWN
    assert detect_format("随便什么", filename="main.py") == FORMAT_CODE


def test_chunk_markdown_splits_by_heading():
    text = """# 部署指南

## 数据库配置

用 DATABASE_URL 连库。

## 缓存

用 REDIS_URL 连缓存。
"""
    chunks = chunk_markdown(text)
    assert len(chunks) == 3
    headings = [chunk.meta["heading"] for chunk in chunks]
    assert headings == ["部署指南", "数据库配置", "缓存"]
    # 标题路径：数据库配置在「部署指南」下面
    db_chunk = chunks[1]
    assert db_chunk.meta["path"] == ["部署指南", "数据库配置"]
    assert "DATABASE_URL" in db_chunk.content


def test_chunk_markdown_long_section_is_split():
    body = "\n\n".join(f"第 {i} 段内容。" + "x" * 200 for i in range(10))
    chunks = chunk_markdown(f"## 大段\n\n{body}", max_chars=400)
    assert len(chunks) > 1
    assert all(len(chunk.content) <= 400 for chunk in chunks)


def test_chunk_code_splits_by_definition():
    text = '''"""模块说明。"""

import os


def parse_config(path):
    return {}


class Loader:
    def load(self):
        return 1
'''
    chunks = chunk_document(text, fmt=FORMAT_CODE)
    assert chunks[0].meta["kind"] == "header"
    symbols = [chunk.meta.get("symbol") for chunk in chunks[1:]]
    assert "parse_config" in symbols
    assert "Loader" in symbols


def test_chunk_text_window_and_overlap():
    text = "\n\n".join(f"段落{i}内容" for i in range(20))
    chunks = chunk_text(text, max_chars=120, overlap=40)
    assert len(chunks) > 1
    # 有重叠：后面的块里能看到前面块的内容
    assert any(chunk.meta.get("overlap") for chunk in chunks)


def test_chunk_code_keeps_decorator_with_function():
    text = '@app.route("/x")\ndef handler():\n    return 1\n'
    chunks = chunk_document(text, fmt=FORMAT_CODE)
    definitions = [chunk for chunk in chunks if chunk.meta.get("kind") == "definition"]
    assert len(definitions) == 1
    assert "@app.route" in definitions[0].content
    assert "def handler" in definitions[0].content
    assert definitions[0].meta["symbol"] == "handler"


def test_chunk_text_keeps_all_content():
    text = "\n\n".join(f"段落{i}" for i in range(10))
    chunks = chunk_text(text, max_chars=20, overlap=0)
    assert len(chunks) > 1
    joined = "".join(chunk.content for chunk in chunks)
    for index in range(10):
        assert f"段落{index}" in joined


def test_estimate_tokens():
    assert estimate_tokens("") == 0
    assert estimate_tokens("你好世界") == 4
    assert estimate_tokens("hello world") == 3


# ---------- 关键词检索 ----------


def test_tokenize_splits_identifiers_and_cjk():
    tokens = tokenize("DATABASE_URL 数据库")
    assert "database_url" in tokens
    assert "database" in tokens
    assert "数据" in tokens


def test_keyword_rank_prefers_exact_match():
    class FakeChunk:
        def __init__(self, content):
            self.content = content
            self.meta = {}
            self.id = id(content)

    chunks = [
        FakeChunk("这里提到了 URL 和地址的配置方式"),
        FakeChunk("环境变量 DATABASE_URL 指向生产库"),
        FakeChunk("完全无关的内容"),
    ]
    ranked = keyword_rank(chunks, "DATABASE_URL")
    assert ranked
    assert ranked[0][0].content.startswith("环境变量")


# ---------- 向量化 ----------


def test_normalize_and_cosine():
    assert normalize([3.0, 4.0]) == [0.6, 0.8]
    assert cosine([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
    assert cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)
    # 维度不一致当作不相关
    assert cosine([1.0], [1.0, 2.0]) == -1.0


class _FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.text = json.dumps(payload, ensure_ascii=False)

    def json(self):
        return self._payload


class _FakeClient:
    """替换 httpx.AsyncClient，记录请求并返回固定向量。"""

    calls: list[dict] = []

    def __init__(self, *args, **kwargs):
        self.kwargs = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url, json=None, headers=None):
        type(self).calls.append({"url": url, "json": json, "headers": headers})
        count = len(json["input"])
        return _FakeResponse(
            {
                "data": [
                    {"index": index, "embedding": [float(index + 1), 0.5, 0.25]}
                    for index in range(count)
                ],
            }
        )


@pytest.mark.asyncio
async def test_openai_compatible_embedding(monkeypatch):
    _FakeClient.calls = []
    monkeypatch.setattr("app.rag.embedding.httpx.AsyncClient", lambda *a, **k: _FakeClient())
    provider = OpenAICompatibleEmbeddingProvider(
        EmbeddingTarget(model="text-embedding-3-small", base_url="https://x/v1", api_key="k")
    )
    vectors = await provider.embed(["a", "b"])
    assert len(vectors) == 2
    assert vectors[0] == [1.0, 0.5, 0.25]
    assert _FakeClient.calls[0]["url"] == "https://x/v1/embeddings"
    assert _FakeClient.calls[0]["headers"]["Authorization"] == "Bearer k"


# ---------- 混合检索 ----------


class _FakeEmbedder:
    """把查询映射到固定向量的假向量化器。"""

    name = "fake"

    def __init__(self, mapping: dict[str, list[float]]):
        self.mapping = mapping

    async def embed(self, texts):
        return [self.mapping.get(text, [0.0, 0.0, 0.0]) for text in texts]


@pytest.mark.asyncio
async def test_hybrid_search_fuses_two_routes(session: AsyncSession):
    collection = RagCollection(name="c1", description="", scope="private")
    session.add(collection)
    await session.flush()

    doc = RagDocument(
        collection_id=collection.id, title="配置", source="", format="text", content="x"
    )
    session.add(doc)
    await session.flush()

    chunks = [
        RagChunk(
            collection_id=collection.id,
            document_id=doc.id,
            seq=0,
            content="用 DATABASE_URL 配置数据库连接",
            embedding=normalize([1.0, 0.0, 0.0]),
            dim=3,
            token_count=10,
            meta={},
        ),
        RagChunk(
            collection_id=collection.id,
            document_id=doc.id,
            seq=1,
            content="部署步骤一二三四",
            embedding=normalize([0.0, 1.0, 0.0]),
            dim=3,
            token_count=8,
            meta={},
        ),
    ]
    session.add_all(chunks)
    await session.commit()

    store = SqlVectorStore(session)
    retriever = Retriever(store, _FakeEmbedder({"数据库连接": [1.0, 0.0, 0.0]}))
    result = await retriever.search(
        SearchRequest(query="数据库连接", collection_id=collection.id, top_k=5)
    )
    assert result.hits
    top = result.hits[0]
    assert "DATABASE_URL" in top.chunk.content
    # 两路都命中了它
    assert top.vector_score is not None
    assert top.keyword_score is not None
    assert 0 <= top.score <= 1


@pytest.mark.asyncio
async def test_keyword_mode_without_embedder(session: AsyncSession):
    collection = RagCollection(name="c2", description="", scope="private")
    session.add(collection)
    await session.flush()
    doc = RagDocument(collection_id=collection.id, title="t", source="", format="text", content="x")
    session.add(doc)
    await session.flush()
    session.add(
        RagChunk(
            collection_id=collection.id,
            document_id=doc.id,
            seq=0,
            content="缓存用 REDIS_URL",
            embedding=None,
            dim=0,
            token_count=6,
            meta={},
        )
    )
    await session.commit()

    retriever = Retriever(SqlVectorStore(session), None)
    result = await retriever.search(
        SearchRequest(query="REDIS_URL", collection_id=collection.id, mode="keyword")
    )
    assert len(result.hits) == 1
    assert result.hits[0].keyword_score is not None
    assert result.hits[0].vector_score is None
    # 融合分对外承诺是 0-1（BM25 原始分没有上界，要归一）
    assert 0 <= result.hits[0].score <= 1


# ---------- 服务与接口全链路 ----------


@pytest.mark.asyncio
async def test_service_ingest_and_search(session: AsyncSession):
    service = RagService(session)

    collection = await service.create_collection(
        _CollectionPayload(name="语料集", description="测试")
    )
    assert collection.embedding_model == ""
    assert collection.embedding_dim == 0

    result = await service.ingest_document(
        collection.id,
        _DocumentPayload(
            content="平台的数据库连接通过 DATABASE_URL 配置，默认是 postgres。",
            title="数据库配置",
        ),
    )
    assert result.skipped is False
    assert result.chunk_count >= 1
    assert result.keyword_only is True

    # 同样的内容再入库一次 → 跳过
    again = await service.ingest_document(
        collection.id,
        _DocumentPayload(
            content="平台的数据库连接通过 DATABASE_URL 配置，默认是 postgres。",
            title="数据库配置",
        ),
    )
    assert again.skipped is True
    assert again.document.id == result.document.id

    found = await service.search(_SearchPayload(query="DATABASE_URL", collection_id=collection.id))
    assert found.hits
    assert "DATABASE_URL" in found.hits[0].chunk.content
    assert found.hits[0].title == "数据库配置"

    # 删文档 → 分块一起没了
    await service.delete_document(result.document.id)
    empty = await service.search(_SearchPayload(query="DATABASE_URL", collection_id=collection.id))
    assert empty.hits == []


@pytest.mark.asyncio
async def test_service_delete_collection(session: AsyncSession):
    service = RagService(session)
    collection = await service.create_collection(_CollectionPayload(name="待删"))
    await service.ingest_document(
        collection.id, _DocumentPayload(content="一些内容，用于测试删除。")
    )
    await service.delete_collection(collection.id)
    assert await service.get_collection_by_name("待删") is None


@pytest.mark.asyncio
async def test_api_flow(client):
    """接口层全链路：建集合 → 入库 → 检索 → 删。"""
    created = await client.post(
        "/api/v1/rag/collections",
        json={"name": "接口集合", "description": "测试"},
    )
    assert created.status_code == 201, created.text
    collection_id = created.json()["data"]["id"]
    assert created.json()["data"]["embedding_model"] == ""

    ingested = await client.post(
        f"/api/v1/rag/collections/{collection_id}/documents",
        json={"content": "沙箱用 docker 隔离执行，--network none 断网。", "title": "沙箱"},
    )
    assert ingested.status_code == 201, ingested.text
    assert ingested.json()["data"]["chunk_count"] >= 1

    listed = await client.get(f"/api/v1/rag/collections/{collection_id}/documents")
    assert listed.status_code == 200
    assert listed.json()["data"]["meta"]["total"] == 1

    searched = await client.post(
        "/api/v1/rag/search",
        json={"query": "docker 隔离", "collection_id": collection_id, "top_k": 3},
    )
    assert searched.status_code == 200, searched.text
    hits = searched.json()["data"]["hits"]
    assert hits
    assert "docker" in hits[0]["content"]
    assert hits[0]["title"] == "沙箱"

    # 同名集合建不了两次
    duplicate = await client.post("/api/v1/rag/collections", json={"name": "接口集合"})
    assert duplicate.status_code == 409

    deleted = await client.delete(f"/api/v1/rag/collections/{collection_id}")
    assert deleted.status_code == 204


@pytest.mark.asyncio
async def test_api_search_unknown_collection(client):
    response = await client.post("/api/v1/rag/search", json={"query": "x", "collection_id": 9999})
    assert response.status_code == 404


# ---------- 简单的载荷替身 ----------


class _CollectionPayload:
    def __init__(
        self,
        name: str,
        description: str = "",
        scope: str = "private",
        embedding_model: str = "",
        provider_id: int | None = None,
        chunk_size: int = 1000,
        chunk_overlap: int = 150,
        meta: dict | None = None,
    ):
        self.name = name
        self.description = description
        self.scope = scope
        self.embedding_model = embedding_model
        self.provider_id = provider_id
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.meta = meta or {}


class _DocumentPayload:
    def __init__(
        self,
        content: str,
        title: str = "",
        source: str = "",
        format: str = "auto",
        meta: dict | None = None,
    ):
        self.content = content
        self.title = title
        self.source = source
        self.format = format
        self.meta = meta or {}


class _SearchPayload:
    def __init__(
        self,
        query: str,
        collection_id: int,
        top_k: int = 8,
        mode: str = "hybrid",
        min_score: float = 0.0,
        filters: dict | None = None,
    ):
        self.query = query
        self.collection_id = collection_id
        self.top_k = top_k
        self.mode = mode
        self.min_score = min_score
        self.filters = filters or {}

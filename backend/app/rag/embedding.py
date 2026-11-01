"""向量化：把文本变成向量。

**复用现有的供应商配置**，不引入新的基础设施。绝大多数厂商（OpenAI、通义、
智谱、硅基流动、Kimi…）都在同一个 base_url 上同时提供对话和向量化接口，
差别只是模型名。所以做法是：

- 供应商配置表加两列：``embedding_model``（模型名）和 ``embedding_dim``（维度）；
- 向量化请求直接打 ``{base_url}/embeddings``，OpenAI 兼容格式；
- 没配 embedding_model 的供应商 —— 不影响对话，只是不参与向量化。

**没有向量化也能用**：集合不配 embedding_model 时走纯关键词检索，
RAG 层不会因此不可用。这很重要 —— 关键词检索在"查函数名、查环境变量名"
这类场景下比向量检索还准。

本地模型（BGE 之类）留了口子：``EmbeddingProvider`` 是协议，实现换成
本地推理即可，不影响上层。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

import httpx

from app.core.config import settings
from app.core.exceptions import AppError, ValidationError


class EmbeddingError(AppError):
    """向量化失败。"""

    status_code = 502
    code = "EMBEDDING_ERROR"
    message = "向量化失败"


@dataclass
class EmbeddingTarget:
    """向量化目标：一个端点 + 一个模型。

    这是"向量化配置"的可序列化形式 —— 集合里只记模型名和维度，
    端点和密钥在调用时从供应商配置里取，不落库。
    """

    model: str
    base_url: str = ""
    api_key: str = ""
    provider: str = "openai"
    dim: int = 0
    timeout: int = 60
    batch_size: int = 32


@runtime_checkable
class EmbeddingProvider(Protocol):
    """向量化能力的协议。

    实现可以换成任何东西：HTTP 端点、本地模型、缓存、假实现（测试用）。
    """

    name: str

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """把一批文本变成一批向量。返回长度必须与入参一致。"""
        ...


class NullEmbeddingProvider:
    """不向量化。

    集合没配 embedding_model 时用它 —— 检索退化成纯关键词，功能不缺。
    """

    name = "null"

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[] for _ in texts]


class OpenAICompatibleEmbeddingProvider:
    """OpenAI 兼容的向量化端点。

    请求体是 ``{"model": ..., "input": [...]}``，绝大多数厂商都是这个形状。
    响应是 ``{"data": [{"index": 0, "embedding": [...]}, ...]}``。
    """

    name = "openai-compatible"

    def __init__(self, target: EmbeddingTarget) -> None:
        self.target = target

    @property
    def endpoint(self) -> str:
        base = self.target.base_url.strip().rstrip("/")
        if not base:
            raise EmbeddingError("没有配置向量化服务的地址")
        return f"{base}/embeddings"

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors: list[list[float]] = []
        size = max(1, self.target.batch_size)
        for start in range(0, len(texts), size):
            batch = texts[start : start + size]
            vectors.extend(await self._embed_batch(batch))
        if len(vectors) != len(texts):
            raise EmbeddingError(
                f"向量化返回的条数与请求不一致（请求 {len(texts)} 条，返回 {len(vectors)} 条）"
            )
        return vectors

    async def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        payload: dict[str, Any] = {"model": self.target.model, "input": list(texts)}
        headers = {"Content-Type": "application/json"}
        if self.target.api_key:
            headers["Authorization"] = f"Bearer {self.target.api_key}"

        try:
            async with httpx.AsyncClient(timeout=self.target.timeout) as client:
                response = await client.post(self.endpoint, json=payload, headers=headers)
        except httpx.HTTPError as exc:
            raise EmbeddingError(f"向量化请求失败：{exc}") from exc

        if response.status_code >= 400:
            raise EmbeddingError(f"向量化接口返回 {response.status_code}：{response.text[:300]}")
        try:
            body = response.json()
        except ValueError as exc:
            raise EmbeddingError("向量化接口返回的不是合法 JSON") from exc
        if not isinstance(body, dict):
            raise EmbeddingError("向量化接口返回的 JSON 不是对象")

        items = body.get("data") or []
        # 有的实现不保证顺序，按 index 排一次
        if all("index" in item for item in items):
            items = sorted(items, key=lambda item: item["index"])
        try:
            return [[float(x) for x in item["embedding"]] for item in items]
        except (KeyError, TypeError, ValueError) as exc:
            raise EmbeddingError("向量化接口返回的向量格式不认识") from exc


def build_provider(target: EmbeddingTarget | None) -> EmbeddingProvider:
    """按目标构造向量化器。没有目标就退化成不向量化。"""
    if target is None or not target.model:
        return NullEmbeddingProvider()
    return OpenAICompatibleEmbeddingProvider(target)


def target_from_settings() -> EmbeddingTarget | None:
    """从环境变量构造向量化目标（本地开发兜底）。

    生产上更推荐在供应商配置里配 —— 密钥有加密存储。
    """
    model = settings.rag_embedding_model.strip()
    if not model:
        return None
    return EmbeddingTarget(
        model=model,
        base_url=settings.rag_embedding_base_url.strip(),
        api_key=settings.rag_embedding_api_key.strip(),
        dim=settings.rag_embedding_dim,
        timeout=settings.rag_embedding_timeout,
        batch_size=settings.rag_embedding_batch_size,
    )


def normalize(vector: list[float]) -> list[float]:
    """L2 归一化。

    归一化之后余弦相似度就是点积，检索时少一次开方。
    """
    if not vector:
        return []
    norm = sum(x * x for x in vector) ** 0.5
    if norm <= 0:
        return [0.0 for _ in vector]
    return [x / norm for x in vector]


def cosine(a: list[float], b: list[float]) -> float:
    """余弦相似度。维度不一致返回 -1（视作完全不相关）。"""
    if len(a) != len(b) or not a:
        return -1.0
    dot = 0.0
    norm_a = 0.0
    norm_b = 0.0
    for x, y in zip(a, b, strict=True):
        dot += x * y
        norm_a += x * x
        norm_b += y * y
    if norm_a <= 0 or norm_b <= 0:
        return 0.0
    return dot / (norm_a**0.5 * norm_b**0.5)


async def probe_dimension(provider: EmbeddingProvider) -> int:
    """探测向量的维度。

    建集合时探一次：维度不对（比如模型名写错、拿到的其实是别的模型），
    在建集合阶段就该报出来，而不是入库之后才发现两批向量维度不一样。
    """
    vectors = await provider.embed(["维度探测"])
    if not vectors or not vectors[0]:
        raise EmbeddingError("向量化接口没有返回向量")
    return len(vectors[0])


def ensure_dimensions(vectors: list[list[float]], expected: int) -> None:
    """检查一批向量的维度是否一致且符合预期。"""
    for index, vector in enumerate(vectors):
        if expected and len(vector) != expected:
            raise ValidationError(
                f"第 {index + 1} 条文本的向量维度是 {len(vector)}，与集合声明的 {expected} 不一致"
            )


__all__ = [
    "EmbeddingError",
    "EmbeddingProvider",
    "EmbeddingTarget",
    "NullEmbeddingProvider",
    "OpenAICompatibleEmbeddingProvider",
    "build_provider",
    "cosine",
    "ensure_dimensions",
    "normalize",
    "probe_dimension",
    "target_from_settings",
]

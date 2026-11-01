"""RAG 接口层：集合、文档、检索。

接口层只做参数接收和响应转换，业务规则全在 `app/rag/service.py`。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Response, status

from app.api.deps import get_rag_service
from app.rag.service import RagService
from app.schemas.common import PageMeta, ResponseModel
from app.schemas.rag import (
    RagCollectionCreate,
    RagCollectionOut,
    RagDocumentCreate,
    RagDocumentListOut,
    RagDocumentOut,
    RagIngestOut,
    RagSearchHit,
    RagSearchOut,
    RagSearchRequest,
)

router = APIRouter(prefix="/rag", tags=["RAG"])

ServiceDep = Annotated[RagService, Depends(get_rag_service)]


def _collection_out(collection) -> RagCollectionOut:
    return RagCollectionOut(
        id=collection.id,
        name=collection.name,
        description=collection.description,
        scope=collection.scope,
        embedding_model=collection.embedding_model,
        embedding_dim=collection.embedding_dim,
        provider_id=collection.provider_id,
        chunk_size=collection.chunk_size,
        chunk_overlap=collection.chunk_overlap,
        doc_count=collection.doc_count,
        chunk_count=collection.chunk_count,
        meta=dict(collection.meta or {}),
        created_at=collection.created_at,
        updated_at=collection.updated_at,
    )


def _document_out(document) -> RagDocumentOut:
    return RagDocumentOut(
        id=document.id,
        collection_id=document.collection_id,
        title=document.title,
        source=document.source,
        format=document.format,
        content_hash=document.content_hash,
        chunk_count=document.chunk_count,
        meta=dict(document.meta or {}),
        created_at=document.created_at,
        updated_at=document.updated_at,
    )


@router.get(
    "/collections",
    response_model=ResponseModel[list[RagCollectionOut]],
    summary="集合列表",
)
async def list_collections(service: ServiceDep) -> ResponseModel[list[RagCollectionOut]]:
    items = await service.list_collections()
    return ResponseModel[list[RagCollectionOut]](data=[_collection_out(item) for item in items])


@router.post(
    "/collections",
    response_model=ResponseModel[RagCollectionOut],
    status_code=status.HTTP_201_CREATED,
    summary="新建集合",
)
async def create_collection(
    payload: RagCollectionCreate,
    service: ServiceDep,
) -> ResponseModel[RagCollectionOut]:
    """新建集合。

    配了 `embedding_model` 的话，这一步会真发一次向量化请求探维度 ——
    模型名写错、密钥不对、端点不通，在这里就报出来。
    """
    collection = await service.create_collection(payload)
    return ResponseModel[RagCollectionOut](message="创建成功", data=_collection_out(collection))


@router.get(
    "/collections/{collection_id}",
    response_model=ResponseModel[RagCollectionOut],
    summary="集合详情",
)
async def get_collection(
    collection_id: Annotated[int, Path(ge=1)],
    service: ServiceDep,
) -> ResponseModel[RagCollectionOut]:
    collection = await service.get_collection(collection_id)
    return ResponseModel[RagCollectionOut](data=_collection_out(collection))


@router.delete(
    "/collections/{collection_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="删除集合（连同文档与分块）",
)
async def delete_collection(
    collection_id: Annotated[int, Path(ge=1)],
    service: ServiceDep,
) -> Response:
    await service.delete_collection(collection_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/collections/{collection_id}/documents",
    response_model=ResponseModel[RagDocumentListOut],
    summary="集合里的文档",
)
async def list_documents(
    collection_id: Annotated[int, Path(ge=1)],
    service: ServiceDep,
    page: Annotated[int, Query(ge=1, description="页码，从 1 开始")] = 1,
    page_size: Annotated[int, Query(ge=1, le=100, description="每页条数")] = 20,
) -> ResponseModel[RagDocumentListOut]:
    items, total = await service.list_documents(collection_id, page=page, page_size=page_size)
    listing = RagDocumentListOut(
        items=[_document_out(item) for item in items],
        meta=PageMeta(
            total=total,
            page=page,
            page_size=page_size,
            pages=(total + page_size - 1) // page_size,
        ),
    )
    return ResponseModel[RagDocumentListOut](data=listing)


@router.post(
    "/collections/{collection_id}/documents",
    response_model=ResponseModel[RagIngestOut],
    status_code=status.HTTP_201_CREATED,
    summary="入库一篇文档",
)
async def ingest_document(
    collection_id: Annotated[int, Path(ge=1)],
    payload: RagDocumentCreate,
    service: ServiceDep,
) -> ResponseModel[RagIngestOut]:
    """入库：分块 → 向量化 → 落库。

    内容重复（同集合内内容哈希一致）时直接跳过，不会重复切、重复算向量。
    """
    result = await service.ingest_document(collection_id, payload)
    return ResponseModel[RagIngestOut](
        message="内容重复，已跳过" if result.skipped else "入库成功",
        data=RagIngestOut(
            document=_document_out(result.document),
            chunk_count=result.chunk_count,
            skipped=result.skipped,
            keyword_only=result.keyword_only,
        ),
    )


@router.delete(
    "/documents/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="删除文档（连同分块）",
)
async def delete_document(
    document_id: Annotated[int, Path(ge=1)],
    service: ServiceDep,
) -> Response:
    await service.delete_document(document_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/search",
    response_model=ResponseModel[RagSearchOut],
    summary="检索",
)
async def search(
    payload: RagSearchRequest,
    service: ServiceDep,
) -> ResponseModel[RagSearchOut]:
    """混合检索。

    `mode=hybrid` 时向量与关键词两路都跑，用 RRF 融合；
    集合没启用向量化时自动退化成纯关键词，并在 `degraded` 里说明。
    """
    result = await service.search(payload)
    return ResponseModel[RagSearchOut](
        data=RagSearchOut(
            hits=[
                RagSearchHit(
                    chunk_id=hit.chunk.id,
                    document_id=hit.chunk.document_id,
                    seq=hit.chunk.seq,
                    title=hit.title,
                    source=hit.source,
                    content=hit.chunk.content,
                    score=hit.score,
                    vector_score=hit.vector_score,
                    keyword_score=hit.keyword_score,
                    rank=hit.rank,
                    meta=dict(hit.chunk.meta or {}),
                )
                for hit in result.hits
            ],
            mode=result.mode,
            took_ms=result.took_ms,
            total=result.total,
            degraded=result.degraded,
        )
    )

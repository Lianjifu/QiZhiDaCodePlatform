"""健康检查接口。"""

from __future__ import annotations

from fastapi import APIRouter

from app.core.config import settings

router = APIRouter(tags=["健康检查"])


@router.get("/health", summary="健康检查")
async def health() -> dict[str, str]:
    """供负载均衡 / 容器探针使用。"""
    return {"status": "ok", "version": settings.app_version}

"""FastAPI 应用入口。

启动：
    uvicorn app.main:app --reload
"""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.router import api_router
from app.capabilities.backends.container_pool import cleanup_stale_containers, get_pool
from app.core.config import settings
from app.core.exceptions import AppError
from app.core.logging import setup_logging
from app.db.session import close_db, init_db
from app.schemas.common import ErrorDetail

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """应用启动/关闭钩子。"""
    setup_logging()
    await init_db()
    _cleanup_sandboxes()
    logger.info("%s v%s 已启动", settings.app_name, settings.app_version)
    if not settings.llm_api_key and settings.llm_provider != "ollama":
        logger.warning("未配置 LLM_API_KEY，调用编码助手接口会失败（请在 .env 中设置）")
    try:
        reaper = asyncio.create_task(_reap_idle_sandboxes())
        yield
    finally:
        reaper.cancel()
        with suppress(asyncio.CancelledError):
            await reaper
        await close_db()
        _shutdown_sandboxes()
        logger.info("%s 已关闭", settings.app_name)


async def _reap_idle_sandboxes() -> None:
    """周期性回收空闲的沙箱容器。

    平台里没有"会话结束"这个事件 —— 容器只能靠空闲超时回收。这个后台任务
    就是那个回收器。

    跑在 docker 模式之外就自己退出 —— 没必要空转。
    """
    if settings.sandbox_type != "docker":
        return
    interval = max(60, settings.sandbox_idle_timeout // 6)
    while True:
        await asyncio.sleep(interval)
        try:
            pool = get_pool()
            removed = pool.reap_idle()
            removed += pool.enforce_capacity()
            if removed:
                logger.info("回收了 %d 个沙箱容器", len(removed))
        except Exception as exc:  # pragma: no cover - 回收失败不该拖垮后台任务
            logger.warning("回收空闲沙箱容器失败: %s", exc)


def _cleanup_sandboxes() -> None:
    """启动时清理残留的沙箱容器。

    容器按会话复用，平台重启后上次留下的还在跑 —— 没人回收它们。启动时按
    前缀清一遍。

    清理失败不挡启动 —— 沙箱不可用时 agent 会明确报错，不该让整个平台起不来。
    """
    if settings.sandbox_type != "docker":
        return
    try:
        removed = cleanup_stale_containers()
        if removed:
            logger.info("已清理 %d 个残留沙箱容器", len(removed))
    except Exception as exc:  # pragma: no cover - 清理失败不该挡启动
        logger.warning("清理残留沙箱容器失败: %s", exc)


def _shutdown_sandboxes() -> None:
    """关闭时回收本进程建的沙箱容器。"""
    if settings.sandbox_type != "docker":
        return
    try:
        removed = get_pool().reap_all()
        if removed:
            logger.info("已回收 %d 个沙箱容器", len(removed))
    except Exception as exc:  # pragma: no cover - 回收失败不该挡关闭
        logger.warning("回收沙箱容器失败: %s", exc)


def create_app() -> FastAPI:
    """应用工厂，便于测试中创建独立实例。"""
    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description=settings.app_description,
        debug=settings.debug,
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(api_router, prefix=settings.api_v1_prefix)

    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        """业务异常统一出口。"""
        logger.warning("业务异常 %s %s: %s", request.method, request.url.path, exc.message)
        return JSONResponse(
            status_code=exc.status_code,
            content=jsonable_encoder(
                {
                    "code": exc.status_code,
                    "error_code": exc.code,
                    "message": exc.message,
                    "details": exc.details,
                }
            ),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        """请求参数校验失败。"""
        details = [
            ErrorDetail(
                loc=[str(x) for x in err.get("loc", ())],
                msg=err.get("msg"),
                type=err.get("type"),
            )
            for err in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            content=jsonable_encoder(
                {
                    "code": 422,
                    "error_code": "VALIDATION_ERROR",
                    "message": "参数校验失败",
                    "details": [d.model_dump() for d in details],
                }
            ),
        )

    @app.get("/", include_in_schema=False)
    async def root() -> dict[str, str]:
        return {
            "name": settings.app_name,
            "docs": "/docs",
            "health": f"{settings.api_v1_prefix}/health",
        }

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.reload,
    )

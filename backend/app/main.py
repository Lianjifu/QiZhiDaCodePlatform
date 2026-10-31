"""FastAPI 应用入口。

启动：
    uvicorn app.main:app --reload
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.router import api_router
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
    logger.info("%s v%s 已启动", settings.app_name, settings.app_version)
    try:
        yield
    finally:
        await close_db()
        logger.info("%s 已关闭", settings.app_name)


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
    async def validation_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        """请求参数校验失败。"""
        details = [
            ErrorDetail(loc=[str(x) for x in err.get("loc", ())], msg=err.get("msg"), type=err.get("type"))
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

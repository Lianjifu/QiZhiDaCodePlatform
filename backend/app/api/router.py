"""v1 路由聚合：新增模块时在这里挂载。"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import (
    agent,
    capability,
    example,
    health,
    invocation,
    llm_provider,
    mcp_server,
    rag,
    skill,
    tool,
)

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(example.router)
api_router.include_router(agent.router)
api_router.include_router(llm_provider.router)
api_router.include_router(mcp_server.router)
api_router.include_router(tool.router)
api_router.include_router(skill.router)
api_router.include_router(capability.router)
api_router.include_router(invocation.router)
api_router.include_router(rag.router)

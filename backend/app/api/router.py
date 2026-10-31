"""v1 路由聚合：新增模块时在这里挂载。"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import agent, example, health

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(example.router)
api_router.include_router(agent.router)

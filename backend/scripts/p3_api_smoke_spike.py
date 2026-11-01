"""用假模型跑通整条 HTTP 链路：chat → 挂起审批 → approve → 图恢复。

不消耗 token、不需要 LLM Key，但走的是**真实的** FastAPI 路由、依赖注入、
服务层、执行层与治理中间件 —— 只有"模型怎么想"是脚本化的。

跑法：cd backend && ../.venv/bin/python scripts/p3_api_smoke_spike.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# 环境变量要在 import app 之前设好
_SMOKE_DB = Path(tempfile.gettempdir()) / "qzd-smoke.db"
os.environ.setdefault("DATABASE_URL", f"sqlite+aiosqlite:///{_SMOKE_DB}")
os.environ.setdefault("SANDBOX_TYPE", "local")  # 冒烟测试不依赖 docker
os.environ.setdefault("WORKSPACE_ROOT", os.path.join(str(Path.home()), "qzd-smoke-workspace"))
os.environ.setdefault("API_KEY_ENCRYPTION_KEY", "smoke-test-key-not-used")

from httpx import ASGITransport, AsyncClient  # noqa: E402
from langchain_core.language_models import BaseChatModel  # noqa: E402
from langchain_core.messages import AIMessage  # noqa: E402
from langchain_core.outputs import ChatGeneration, ChatResult  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine  # noqa: E402

from app.agents.runner import AgentRunner  # noqa: E402
from app.api.deps import get_agent_service  # noqa: E402
from app.db.session import Base, get_db  # noqa: E402
from app.main import create_app  # noqa: E402
from app.services.agent import AgentService  # noqa: E402

API = "/api/v1/agent"


class ScriptedModel(BaseChatModel):
    """按脚本发工具调用：先写文件（不需要审批），再执行命令（要审批）。"""

    steps: list[tuple[str, dict[str, Any]]] = []
    calls: int = 0

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools: Any, **kwargs: Any) -> ScriptedModel:
        return self

    def _generate(
        self,
        messages: list[Any],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        index = self.calls
        self.calls += 1
        if index < len(self.steps):
            name, args = self.steps[index]
            call = {
                "name": name,
                "args": dict(args),
                "id": f"call_{index}",
                "type": "tool_call",
            }
            return ChatResult(
                generations=[ChatGeneration(message=AIMessage(content="", tool_calls=[call]))]
            )
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="做完了。"))])


async def main() -> int:
    engine = create_async_engine(os.environ["DATABASE_URL"], future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    application = create_app()

    async def _get_db() -> Any:
        async with maker() as s:
            yield s

    application.dependency_overrides[get_db] = _get_db

    model = ScriptedModel(
        steps=[
            ("write_file", {"file_path": "hello.txt", "content": "你好"}),
            ("execute", {"command": "echo hi"}),
        ]
    )

    async def _get_agent_service() -> AgentService:
        async with maker() as s:
            return AgentService(session=s, runner=AgentRunner(model=model))

    application.dependency_overrides[get_agent_service] = _get_agent_service

    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://smoke") as client:
        thread_id = "smoke-1"

        print("=== 1. 第一次对话：写文件（直接执行）+ 执行命令（要审批）===")
        resp = await client.post(
            f"{API}/chat", json={"message": "写个文件再跑个命令", "thread_id": thread_id}
        )
        data = resp.json()["data"]
        print(f"  status   = {data['status']}")
        print(f"  content  = {data['content']!r}")
        for call in data["tool_calls"]:
            print(f"  调用     = {call['name']} ok={call.get('ok')} status={call.get('status')}")
        for item in data["pending"]:
            print(f"  待审批   = {item['tool']} ({item['kind']}) {item['message']}")
        if data["status"] != "pending_approval":
            print("❌ 没有挂起审批 —— 治理链路没生效")
            return 1

        print("\n=== 2. 查待审批列表 ===")
        resp = await client.get(f"{API}/{thread_id}/pending")
        items = resp.json()["data"]
        print(f"  {len(items)} 条：{[i['tool'] for i in items]}")

        print("\n=== 3. 批准 → 恢复图 ===")
        resp = await client.post(f"{API}/{thread_id}/approve")
        approved = resp.json()["data"]
        print(f"  status   = {approved['status']}")
        print(f"  content  = {approved['content']!r}")
        for call in approved["tool_calls"]:
            print(f"  调用     = {call['name']} ok={call.get('ok')} status={call.get('status')}")
        if approved["status"] != "completed":
            print("❌ 批准后没有跑完")
            return 1

        print("\n=== 4. 拒绝一次（看模型收到什么）===")
        model2 = ScriptedModel(steps=[("execute", {"command": "echo danger"})])
        async with maker() as s:
            application.dependency_overrides[get_agent_service] = lambda: _service(s, model2)
        resp = await client.post(
            f"{API}/chat", json={"message": "跑个命令", "thread_id": "smoke-2"}
        )
        if resp.json()["data"]["status"] != "pending_approval":
            print("❌ 没有挂起")
            return 1
        resp = await client.post(f"{API}/smoke-2/reject")
        rejected = resp.json()["data"]
        print(f"  status   = {rejected['status']}")
        print(f"  content  = {rejected['content']!r}")

        print("\n=== 5. 调用记录（留痕）===")
        async with maker() as s:
            from sqlalchemy import select

            from app.models.invocation import Invocation

            rows = (await s.execute(select(Invocation).order_by(Invocation.id))).scalars().all()
            for row in rows:
                print(
                    f"  {row.capability_key:12s} {row.status:10s} "
                    f"risk={row.risk} permission={row.permission} {row.duration_ms}ms"
                )
            if not rows:
                print("❌ 一条记录都没有 —— 留痕没生效")
                return 1

    await engine.dispose()
    print("\n✅ HTTP 链路全通：对话 → 挂起 → 批准恢复 → 拒绝 → 留痕")
    return 0


def _service(session: AsyncSession, model: ScriptedModel) -> AgentService:
    return AgentService(session=session, runner=AgentRunner(model=model))


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

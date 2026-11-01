"""验证新审批链路：挂起 → 批准 → 恢复执行。

跑法：cd backend && ../.venv/bin/python scripts/p1_approval_spike.py
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from langchain_core.language_models import BaseChatModel  # noqa: E402
from langchain_core.messages import AIMessage, HumanMessage  # noqa: E402
from langchain_core.outputs import ChatGeneration, ChatResult  # noqa: E402
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver  # noqa: E402
from langgraph.types import Command  # noqa: E402

from app.agents.builder import build_agent  # noqa: E402
from app.capabilities.execution.context import ExecutionContext  # noqa: E402


class ScriptedModel(BaseChatModel):
    """第一次产出工具调用，之后回一句话。"""

    tool_name: str = ""
    tool_args: dict[str, Any] = {}
    seen: list[int] = []

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
        self.seen.append(1)
        if self.tool_name and len(self.seen) == 1:
            call = {
                "name": self.tool_name,
                "args": dict(self.tool_args),
                "id": "call_1",
                "type": "tool_call",
            }
            return ChatResult(
                generations=[ChatGeneration(message=AIMessage(content="", tool_calls=[call]))]
            )
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="做完了。"))])


async def main() -> int:
    import tempfile

    workspace = Path(tempfile.mkdtemp(prefix="qzd-p1-", dir=str(Path.home())))
    context = ExecutionContext.build(workspace)
    # 这个脚本验的就是 docker 沙箱 —— 显式写出来，别靠默认值
    context.sandbox_type = "docker"

    model = ScriptedModel(tool_name="execute", tool_args={"command": "echo hello"})
    async with AsyncSqliteSaver.from_conn_string(":memory:") as saver:
        await saver.setup()
        agent = await build_agent(
            specs=[],
            context=context,
            checkpointer=saver,
            model=model,
            system_prompt="你是测试助手。",
            thread_id="p1",
            trust_mode="balanced",
        )
        config = {"configurable": {"thread_id": "p1"}}
        result = await agent.ainvoke(
            {"messages": [HumanMessage(content="跑个命令")]}, config=config
        )
        print("=== 第一次调用 ===")
        print("结果键:", list(result.keys()))
        interrupts = result.get("__interrupt__")
        print("中断:", interrupts)
        if not interrupts:
            print("❌ 没有挂起 —— 审批没生效")
            return 1
        print("✅ 挂起了")

        state = await agent.aget_state(config)
        print("state.interrupts:", getattr(state, "interrupts", None))

        print("\n=== 批准后恢复 ===")
        result2 = await agent.ainvoke(
            Command(resume={"decisions": [{"type": "approve"}]}), config=config
        )
        print("中断:", result2.get("__interrupt__"))
        for msg in result2.get("messages", []):
            print(" -", type(msg).__name__, str(msg.content)[:120])
        print("✅ 恢复完成")
    return 0


if __name__ == "__main__":
    import asyncio

    raise SystemExit(asyncio.run(main()))

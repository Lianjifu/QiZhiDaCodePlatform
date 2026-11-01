"""验证参数级风险挂起：命令含 rm -rf 时由我们的中间件 interrupt。

跑法：cd backend && ../.venv/bin/python scripts/p1_param_risk_spike.py
"""

from __future__ import annotations

import sys
import tempfile
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
    workspace = Path(tempfile.mkdtemp(prefix="qzd-p1r-", dir=str(Path.home())))
    context = ExecutionContext.build(workspace)

    # permissive 模式：执行命令不触发声明级审批，
    # 只有参数级规则（rm -rf）才该挂起
    model = ScriptedModel(tool_name="execute", tool_args={"command": "rm -rf /tmp/qzd-canary"})
    async with AsyncSqliteSaver.from_conn_string(":memory:") as saver:
        await saver.setup()
        agent = await build_agent(
            specs=[],
            context=context,
            checkpointer=saver,
            model=model,
            system_prompt="你是测试助手。",
            thread_id="p1r",
            trust_mode="permissive",
        )
        config = {"configurable": {"thread_id": "p1r"}}
        result = await agent.ainvoke(
            {"messages": [HumanMessage(content="删个目录")]}, config=config
        )
        interrupts = result.get("__interrupt__")
        print("=== 第一次调用 ===")
        if not interrupts:
            print("❌ 没有挂起 —— 参数级风险规则没生效")
            return 1
        payload = interrupts[0].value
        print("中断载荷:", payload)
        if payload.get("kind") != "param_risk":
            print(f"❌ 中断类型不对：{payload.get('kind')}")
            return 1
        print("✅ 参数级风险挂起了")

        print("\n=== 拒绝后恢复 ===")
        result2 = await agent.ainvoke(
            Command(resume={"decisions": [{"type": "reject", "message": "不许删"}]}),
            config=config,
        )
        print("中断:", result2.get("__interrupt__"))
        for msg in result2.get("messages", []):
            print(" -", type(msg).__name__, str(msg.content)[:160])
        texts = [str(m.content) for m in result2.get("messages", [])]
        if any("RISK_BLOCKED" in t for t in texts):
            print("✅ 拒绝了，工具没执行")
            return 0
        print("❌ 拒绝后没有给模型明确的拒绝消息")
        return 1


if __name__ == "__main__":
    import asyncio

    raise SystemExit(asyncio.run(main()))

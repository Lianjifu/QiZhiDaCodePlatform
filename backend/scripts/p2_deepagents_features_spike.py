"""验证 deepAgents 的四项能力真的接上了：技能、记忆、子代理、删除工具。

跑法：cd backend && ../.venv/bin/python scripts/p2_deepagents_features_spike.py
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from langchain_core.language_models import BaseChatModel  # noqa: E402
from langchain_core.messages import AIMessage, HumanMessage  # noqa: E402
from langchain_core.outputs import ChatGeneration, ChatResult  # noqa: E402
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver  # noqa: E402

from app.agents.builder import build_agent  # noqa: E402
from app.capabilities.execution.context import ExecutionContext  # noqa: E402
from app.capabilities.registration.spec import KIND_SKILL, CapabilitySpec  # noqa: E402
from app.capabilities.skills_export import export_skills  # noqa: E402


class ScriptedModel(BaseChatModel):
    """按脚本走的假模型：第一次调 read_file，之后回文本。"""

    tool_name: str = ""
    tool_args: dict[str, Any] = {}
    seen: list[list[Any]] = []
    bound_tools: list[str] = []

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools: Any, **kwargs: Any) -> ScriptedModel:
        self.bound_tools = [t.name for t in tools]
        return self

    def _generate(
        self,
        messages: list[Any],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        self.seen.append(list(messages))
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
    workspace = Path(tempfile.mkdtemp(prefix="qzd-p2-", dir=str(Path.home())))
    context = ExecutionContext.build(workspace)

    skills_dir = workspace.parent / ".skills" / workspace.name
    skill = CapabilitySpec(
        key="代码审查",
        kind=KIND_SKILL,
        db_id=1,
        display_name="代码审查",
        description="逐行检查潜在问题。",
        knowledge="先看变更，再看调用方。",
        enabled=True,
    )
    sources = export_skills([skill], skills_dir)
    print(f"技能导出目录: {skills_dir}")
    print(f"技能源路径: {sources}")
    if not sources:
        print("❌ 技能没有导出")
        return 1

    model = ScriptedModel(tool_name="read_file", tool_args={"file_path": "a.txt"})
    (workspace / "a.txt").write_text("hello", encoding="utf-8")

    async with AsyncSqliteSaver.from_conn_string(":memory:") as saver:
        await saver.setup()
        agent = await build_agent(
            specs=[skill],
            context=context,
            checkpointer=saver,
            model=model,
            system_prompt="你是测试助手。",
            thread_id="p2",
            trust_mode="balanced",
            skills=sources,
            skills_dir=skills_dir,
            memory=["/AGENTS.md"],
        )
        config = {"configurable": {"thread_id": "p2"}}
        result = await agent.ainvoke(
            {"messages": [HumanMessage(content="读一下 a.txt")]}, config=config
        )

        print("\n=== 模型看到的工具 ===")
        print(model.bound_tools)
        for name in ("task", "delete", "execute", "read_file"):
            if name not in model.bound_tools:
                print(f"❌ 工具 {name} 不在列表里")
                return 1

        print("\n=== 系统提示词 ===")
        # 系统提示词不进 state 的 messages，它是随模型请求发出去的 —— 从模型
        # 实际收到的第一条消息里看。
        first = model.seen[0][0] if model.seen and model.seen[0] else None
        text = str(getattr(first, "content", "")) if first is not None else ""
        print(text[:2000])
        if "代码审查" not in text:
            print("❌ 技能没有出现在系统提示词里")
            return 1
        if "长期记忆" not in text:
            print("❌ 记忆（AGENTS.md）没有进入系统提示词")
            return 1

        print("\n=== 工具调用结果 ===")
        for msg in result["messages"]:
            if msg.type == "tool":
                print(" -", msg.name, str(msg.content)[:120])

        print("\n✅ 技能 / 记忆 / 工具集都接上了")
        return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

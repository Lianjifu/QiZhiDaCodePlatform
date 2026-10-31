"""测试用的假模型：不联网、不调真实厂商，用来验证 agent 编排链路是否通。"""

from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult


class FakeChatModel(BaseChatModel):
    """永远返回固定文本的假模型。

    - `bind_tools` 直接返回自身：真实模型在这里绑定工具 schema，假模型不需要，
      但 create_agent 会调用它，所以必须实现。
    - `_stream` / `_astream` 必须显式实现：基类默认抛 NotImplementedError，
      不会自动退化成非流式调用，流式接口的测试需要它。
    """

    reply: str = "好的，已完成。"

    @property
    def _llm_type(self) -> str:
        return "fake"

    def bind_tools(self, tools: Any, **kwargs: Any) -> "FakeChatModel":
        return self

    def _generate(
        self,
        messages: list[Any],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=self.reply))])

    def _stream(
        self,
        messages: list[Any],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ):
        yield ChatGenerationChunk(message=AIMessageChunk(content=self.reply))

    async def _astream(
        self,
        messages: list[Any],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ):
        yield ChatGenerationChunk(message=AIMessageChunk(content=self.reply))

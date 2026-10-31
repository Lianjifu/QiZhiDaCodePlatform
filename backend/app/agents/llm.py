"""模型工厂：屏蔽 LangChain 不同厂商的差异。

`LLM_PROVIDER=openai` + `LLM_BASE_URL=https://api.deepseek.com/v1` 即可接入任何
OpenAI 兼容网关（DeepSeek / 通义 / Kimi / vLLM / LM Studio / Ollama 的兼容端点）。
"""

from typing import Any

from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel

from app.core.config import settings


def get_chat_model() -> BaseChatModel:
    """按配置构造对话模型。

    刻意不在这里吞异常：配置缺失（缺 api_key、厂商没装集成包）应该在启动阶段
    就暴露出来，而不是等到 agent 跑一半才失败。
    """
    kwargs: dict[str, Any] = {
        "temperature": settings.llm_temperature,
        "max_tokens": settings.llm_max_tokens,
    }
    if settings.llm_api_key:
        kwargs["api_key"] = settings.llm_api_key
    if settings.llm_base_url:
        kwargs["base_url"] = settings.llm_base_url

    return init_chat_model(f"{settings.llm_provider}:{settings.llm_model}", **kwargs)

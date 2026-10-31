"""模型工厂：把"供应商配置"翻译成 LangChain 的聊天模型。

两种来源：
- `get_chat_model()`：读环境变量（`.env` 里的 LLM_*），适合本地开发；
- `build_model_for_provider()`：读数据库里的供应商配置（URL / API Key / 模型名），
  这是平台正式走的方式。

国内绝大多数厂商都提供 OpenAI 兼容端点，所以预设里 `langchain_provider`
统一是 `"openai"`，靠 base_url 区分；只有 Anthropic、Azure 这类原生协议的
才用别的 provider 名。
"""

from __future__ import annotations

from typing import Any

from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel

from app.agents.providers import resolve_preset
from app.core.config import settings
from app.core.crypto import decrypt_secret
from app.models.llm_provider import LLMProvider


def get_chat_model() -> BaseChatModel:
    """按环境变量配置构造对话模型（不走数据库）。

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


def build_model_for_provider(provider: LLMProvider) -> BaseChatModel:
    """按数据库里的供应商配置构造对话模型。

    API Key 在这里才解密，且只进入 SDK，不进任何响应体或日志。
    """
    preset = resolve_preset(provider.provider)
    base_url = provider.base_url or preset.default_base_url or None
    api_key = decrypt_secret(provider.api_key_encrypted)

    kwargs: dict[str, Any] = {
        "temperature": provider.temperature,
        "max_tokens": provider.max_tokens,
        "timeout": provider.timeout,
    }
    if api_key:
        kwargs["api_key"] = api_key
    if base_url:
        kwargs["base_url"] = base_url

    if preset.langchain_provider == "azure_openai":
        # Azure 的参数名不一样：azure_endpoint + api_version
        kwargs.pop("base_url", None)
        if base_url:
            kwargs["azure_endpoint"] = base_url
        kwargs["api_version"] = settings.azure_openai_api_version

    if not provider.default_model:
        raise ValueError(f"供应商配置 {provider.name} 没有设置默认模型")

    return init_chat_model(f"{preset.langchain_provider}:{provider.default_model}", **kwargs)

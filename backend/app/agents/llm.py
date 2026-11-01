"""模型工厂：把"供应商配置"翻译成 LangChain 的聊天模型。

## 两个来源，谁用哪个

| 函数 | 配置来自 | 用在哪 |
| --- | --- | --- |
| :func:`get_chat_model` | 环境变量（`.env` 的 ``LLM_*``） | 本地开发、跑测试、兜底 |
| :func:`build_model_for_provider` | 数据库里的供应商配置 | 平台正式走的路径 |

两条路的差别只在**配置从哪来**，构造出来的模型对象是一样的，
LangChain 的接口也是一样的，所以换来源不需要改任何调用方。

## 配置错误为什么不吞

两个函数都**不捕获** ``init_chat_model()`` 抛的异常。理由：

- 缺 API Key、厂商集成包没装、模型名写错 —— 这些都是**配置错误**，不是运行时
  错误，应该在启动或建模型的时候立刻炸出来；
- 如果在这里吞掉换成 ``None``，上层会拿着一个假模型跑起来，错误会漂移到
  "agent 什么都不干"或者"模型返回空"这种看不出根因的地方，排查成本高得多。

## 国内模型怎么接

国内绝大多数厂商都提供 OpenAI 兼容端点，所以预设里 ``langchain_provider``
统一是 ``"openai"``，靠 ``base_url`` 区分；只有 Anthropic、Azure 这类原生协议的
才用别的 provider 名（Azure 的参数名还不一样，见 :func:`build_model_for_provider`
里的特判）。
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

    # 厂商名来自环境变量。国产模型要写 `openai`（配 base_url 指到厂商），
    # 写 `deepseek` 会去找没装的 langchain-deepseek 包。
    return init_chat_model(f"{settings.llm_provider}:{settings.llm_model}", **kwargs)


def build_model_for_provider(provider: LLMProvider) -> BaseChatModel:
    """按数据库里的供应商配置构造对话模型。

    API Key 在这里才解密，且只进入 SDK，不进任何响应体或日志。

    参数：

    - ``provider.base_url`` 优先于预设的默认地址（用户在界面上改过就听用户的）；
    - ``provider.default_model`` 为空直接报错 —— 没有模型名没法建模型，早失败
      比跑起来再失败好。
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


__all__ = ["build_model_for_provider", "get_chat_model"]

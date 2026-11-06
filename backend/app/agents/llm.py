"""
模型工厂：把"供应商配置"翻译成 LangChain 的聊天模型。
"""

from __future__ import annotations

from typing import Any

from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel

from app.agents.providers import get_preset, resolve_preset
from app.core.config import settings
from app.core.crypto import decrypt_secret
from app.models.llm_provider import LLMProvider

# profile 里标记"这个模型能看图"的键。
#
# 用的是 langchain 的标准 profile 字段（``image_inputs``），不是自己造的 ——
# langchain 与各家集成包的 profile 数据都用这个键，混用两套标记迟早对不上。
VISION_PROFILE_KEY = "image_inputs"


def supports_vision(model: Any) -> bool:
    """模型能不能看图。

    对话里上传的图片是**直接内联进消息**发给模型的 —— 模型不认 ``image_url``
    就会直接报错。所以内联之前必须先问一句。

    标记挂在模型对象的 ``profile`` 上，跟上下文窗口一样：模型有哪些能力，
    就跟模型走，不另开一条参数通道。
    """
    profile = getattr(model, "profile", None)
    if not isinstance(profile, dict):
        return False
    return bool(profile.get(VISION_PROFILE_KEY))


def _apply_vision(model: BaseChatModel, vision_enabled: bool) -> BaseChatModel:
    """把"能不能看图"挂到模型对象上。

    Args:
        model: 构造好的模型对象。
        vision_enabled: 能不能看图。

    Returns:
        原样返回（就地改了 profile）。
    """
    if not vision_enabled:
        return model
    try:
        model.profile = {**(model.profile or {}), VISION_PROFILE_KEY: True}
    except Exception:  # pragma: no cover - 个别模型对象的 profile 是只读的
        pass
    return model


def _apply_context_window(model: BaseChatModel, context_window: int) -> BaseChatModel:
    """把上下文窗口告诉模型对象。
    Args:
        model: 构造好的模型对象。
        context_window: 上下文窗口（token 数），0 = 未知。

    Returns:
        原样返回（就地改了 profile）。
    """
    if context_window <= 0:
        return model
    try:
        model.profile = {**(model.profile or {}), "max_input_tokens": context_window}
    except Exception:  # pragma: no cover - 个别模型对象的 profile 是只读的
        pass
    return model


def _init_model(
    *,
    langchain_provider: str,
    model_name: str,
    base_url: str | None,
    api_key: str | None,
    temperature: float,
    max_tokens: int,
    timeout: int | None = None,
) -> BaseChatModel:
    """
    按 langchain provider 名与参数建模型对象。
    """
    kwargs: dict[str, Any] = {
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if timeout is not None:
        kwargs["timeout"] = timeout
    if api_key:
        kwargs["api_key"] = api_key
    if base_url:
        kwargs["base_url"] = base_url

    if langchain_provider == "azure_openai":
        # Azure 的参数名不一样：azure_endpoint + api_version
        kwargs.pop("base_url", None)
        if base_url:
            kwargs["azure_endpoint"] = base_url
        kwargs["api_version"] = settings.azure_openai_api_version

    return init_chat_model(f"{langchain_provider}:{model_name}", **kwargs)


def env_context_window() -> int:
    """`.env` 有效上下文窗口。
    环境变量写了就用它，没写就退到供应商预设的（库里那条路同样会退到预设）。
    """
    preset = get_preset(settings.llm_provider)
    return settings.llm_context_window or (preset.context_window if preset else 0)


def env_vision_enabled() -> bool:
    """`.env` 有效视觉能力。
    跟窗口一样，**没写就退到供应商预设的**（库里那条路也是：配置上没填就取预设的值）。
    """
    if settings.llm_vision_enabled is not None:
        return settings.llm_vision_enabled
    preset = get_preset(settings.llm_provider)
    return preset.vision_enabled if preset else False


def get_chat_model() -> BaseChatModel:
    """按环境变量配置构造对话模型（不走数据库）。

    ``LLM_PROVIDER`` 可以写**我们的预设 key**（``deepseek`` / ``qwen`` /
    ``moonshot`` …），也可以写 langchain 认识的 provider 名（``openai`` /
    ``anthropic`` / ``ollama`` …）。认得预设 key 就走预设 —— 拿它的
    ``langchain_provider`` 与默认地址；认不出来的当 langchain provider 名用。

    """
    preset = get_preset(settings.llm_provider)
    langchain_provider = preset.langchain_provider if preset else settings.llm_provider
    default_base_url = preset.default_base_url if preset else ""

    # 环境变量里显式写的地址优先于预设的默认地址（跟数据库那条路一个口径）
    base_url = settings.llm_base_url or default_base_url or None
    # 身份里放**有效**窗口与视觉 —— 两个配置算出同一个结果就是同一个模型
    context_window = env_context_window()
    vision_enabled = env_vision_enabled()

    identity = "env:" + "|".join(
        [
            langchain_provider,
            settings.llm_model,
            str(base_url),
            str(settings.llm_temperature),
            str(settings.llm_max_tokens),
            str(context_window),
            str(vision_enabled),
        ]
    )
    cached = _MODEL_CACHE.get(identity)
    if cached is not None:
        return cached

    model = _init_model(
        langchain_provider=langchain_provider,
        model_name=settings.llm_model,
        base_url=base_url,
        api_key=settings.llm_api_key or None,
        temperature=settings.llm_temperature,
        max_tokens=settings.llm_max_tokens,
    )
    # 上下文窗口：环境变量写了就用它，没写就退到预设的（库里那条路同样会
    # 退到预设）—— 国产模型的大窗口不给，摘要会提前太多触发
    model = _apply_context_window(model, context_window)
    model = _apply_vision(model, vision_enabled)
    return _remember(model, identity)


def build_model_for_provider(provider: LLMProvider) -> BaseChatModel:
    """按数据库里的供应商配置构造对话模型。

    API Key 在这里才解密，且只进入 SDK，不进任何响应体或日志。

    参数：

    - ``provider.base_url`` 优先于预设的默认地址（用户在界面上改过就听用户的）；
    - ``provider.default_model`` 为空直接报错 —— 没有模型名没法建模型，早失败比跑起来再失败好。
    """
    preset = resolve_preset(provider.provider)
    base_url = provider.base_url or preset.default_base_url or None
    api_key = decrypt_secret(provider.api_key_encrypted)

    identity = "provider:" + "|".join(
        [
            str(provider.id),
            provider.provider,
            str(base_url),
            str(provider.api_key_encrypted),
            str(provider.default_model),
            str(provider.temperature),
            str(provider.max_tokens),
            str(provider.timeout),
            str(provider.context_window),
            str(provider.vision_enabled),
        ]
    )
    cached = _MODEL_CACHE.get(identity)
    if cached is not None:
        return cached

    if not provider.default_model:
        raise ValueError(f"供应商配置 {provider.name} 没有设置默认模型")

    model = _init_model(
        langchain_provider=preset.langchain_provider,
        model_name=provider.default_model,
        base_url=base_url,
        api_key=api_key or None,
        temperature=provider.temperature,
        max_tokens=provider.max_tokens,
        timeout=provider.timeout,
    )
    model = _apply_context_window(model, provider.context_window or preset.context_window)
    model = _apply_vision(model, provider.vision_enabled)
    return _remember(model, identity)


# 模型对象 → 身份
_MODEL_CACHE: dict[str, BaseChatModel] = {}
# id(模型) → (模型对象, 身份)
_MODEL_IDENTITIES: dict[int, tuple[BaseChatModel, str]] = {}


def _remember(model: BaseChatModel, identity: str) -> BaseChatModel:
    """记住这个模型对象与它的身份。"""
    _MODEL_CACHE[identity] = model
    _MODEL_IDENTITIES[id(model)] = (model, identity)
    return model


def model_identity(model: Any) -> str:
    """模型的稳定身份。

    **换模型必须重建图** —— 模型对象被编进了图里（模型调用、摘要阈值都指着它）。
    缓存键里带着身份，换模型就自动重建，不用谁记得去清缓存。
    """
    if model is None:
        return "none"
    entry = _MODEL_IDENTITIES.get(id(model))
    if entry is not None and entry[0] is model:
        return entry[1]
    # 不是本模块造的（测试里的假模型）—— 按对象身份算，同一个对象永远同一个值
    return f"adhoc:{id(model)}"


__all__ = [
    "VISION_PROFILE_KEY",
    "build_model_for_provider",
    "env_context_window",
    "env_vision_enabled",
    "get_chat_model",
    "model_identity",
    "supports_vision",
]

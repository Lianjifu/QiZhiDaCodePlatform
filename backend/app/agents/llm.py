"""模型工厂：把"供应商配置"翻译成 LangChain 的聊天模型。

## 两个来源，谁用哪个

| 函数 | 配置来自 | 用在哪 |
| --- | --- | --- |
| :func:`get_chat_model` | 环境变量（`.env` 的 ``LLM_*``） | 本地开发、跑测试、兜底 |
| :func:`build_model_for_provider` | 数据库里的供应商配置 | 平台正式走的路径 |

两条路的差别只在**配置从哪来**，构造出来的模型对象是一样的，
LangChain 的接口也是一样的，所以换来源不需要改任何调用方。

**两条路共用同一个预设映射** —— `.env` 里写 `LLM_PROVIDER="deepseek"`
跟数据库里配 `provider="deepseek"` 是一回事：都翻译成 langchain 认识的
`openai` + 该厂商的默认地址。真正建模型的方式共用 :func:`_init_model`。

## 配置错误为什么不吞

两个函数都**不捕获** ``init_chat_model()`` 抛的异常。理由：

- 缺 API Key、厂商集成包没装、模型名写错 —— 这些都是**配置错误**，不是运行时
  错误，应该在启动或建模型的时候立刻炸出来；
- 如果在这里吞掉换成 ``None``，上层会拿着一个假模型跑起来，错误会漂移到
  "agent 什么都不干"或者"模型返回空"这种看不出根因的地方，排查成本高得多。

## 国内模型怎么接

国内绝大多数厂商都提供 OpenAI 兼容端点，所以预设里 ``langchain_provider``
统一是 ``"openai"``，靠 ``base_url`` 区分；只有 Anthropic、Azure 这类原生协议的
才用别的 provider 名（Azure 的参数名还不一样，见 :func:`_init_model`
里的特判）。

**这也意味着 `deepseek` 不能直接当 langchain provider 名** ——
`init_chat_model("deepseek:xxx")` 会去找没装的 `langchain-deepseek` 包，
报 "Initializing ChatDeepSeek requires the langchain-deepseek package"。
所以 :func:`get_chat_model` 先查预设表：认得就取预设的 `langchain_provider`，
认不出来的才当 langchain provider 名用。
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
# 用的是 langchain 的**标准** profile 字段（``image_inputs``），不是自己造的 ——
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

    **为什么必须显式设**：deepAgents 的摘要中间件按
    ``model.profile["max_input_tokens"]`` 算触发阈值，而 OpenAI 兼容端点挂上去的
    模型对象**没有 profile** —— 不设的话框架退回到固定的"170000 token 触发、
    保留 6 条消息"。对 1M 窗口的模型来说，那等于只用了不到两成窗口就开始
    压缩，压完只剩 6 条消息（一轮工具调用就有好几条，等于只留当前这一轮）。

    窗口未知（0）时不设 —— 那就让框架用保守默认值，比瞎猜一个大窗口安全。

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
    """按 langchain provider 名与参数建模型对象。

    **两条来源共用这一段** —— 环境变量那条与数据库那条，差别只在配置从哪来，
    真正建模型的方式必须一致（尤其 Azure 那套不一样的参数名）。
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
    """`.env` 这条路的**有效**上下文窗口。

    环境变量写了就用它，没写就退到供应商预设的（库里那条路同样会退到预设）。

    **为什么要单独一个函数**：上下文预览接口要报"这次给模型看多少字符"，
    它得跟模型对象上挂的窗口是**同一个数** —— 预览说 17 万 token 而模型
    其实有 100 万，排查时会被带偏。
    """
    preset = get_preset(settings.llm_provider)
    return settings.llm_context_window or (preset.context_window if preset else 0)


def env_vision_enabled() -> bool:
    """`.env` 这条路的**有效**视觉能力。

    跟窗口一样，**没写就退到供应商预设的**（库里那条路也是：配置上没填就取
    预设的值）。

    **怎么分"没写"和"显式写了 false"** —— ``llm_vision_enabled`` 的类型是
    ``bool | None``，``None`` 就是"没写"（显式写 ``false`` 才算关掉）。
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

    **为什么不能让 ``deepseek`` 直接当 langchain provider 名** ——
    ``init_chat_model("deepseek:xxx")`` 会去找 ``langchain-deepseek`` 这个包，
    没装就报 "Initializing ChatDeepSeek requires the langchain-deepseek
    package"。数据库那条路（:func:`build_model_for_provider`）一直是走预设
    映射的，``.env`` 这条也得走一样的，不然同一个厂商两条路配法不一样。

    窗口与视觉没写就退到预设的（见 :func:`env_context_window` /
    :func:`env_vision_enabled`）—— 跟数据库那条路同一个口径。
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
    - ``provider.default_model`` 为空直接报错 —— 没有模型名没法建模型，早失败
      比跑起来再失败好。

    **同一个配置返回同一个模型对象** —— 模型被编进了图里，图要跨请求复用，
    模型对象就得稳定（见 ``model_identity``）。配置一变（改了模型名、换了
    key、调了温度）就是另一个对象，缓存自然失效。
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
#
# **身份是"这份配置"而不是"这个对象"** —— 换了配置就是另一个身份，
# 缓存自然失效；配置没变就还是同一个模型对象。
_MODEL_CACHE: dict[str, BaseChatModel] = {}
# id(模型) → (模型对象, 身份)
#
# **连模型对象一起存着** —— 只存 id 的话，对象被回收后 id 可能被新对象复用，
# 换个模型却拿到旧身份，缓存就用了错的模型。
_MODEL_IDENTITIES: dict[int, tuple[BaseChatModel, str]] = {}


def _remember(model: BaseChatModel, identity: str) -> BaseChatModel:
    """记住这个模型对象与它的身份。"""
    _MODEL_CACHE[identity] = model
    _MODEL_IDENTITIES[id(model)] = (model, identity)
    return model


def model_identity(model: Any) -> str:
    """模型的稳定身份。

    **换模型必须重建图** —— 模型对象被编进了图里（模型调用、摘要阈值都指着
    它）。缓存键里带着身份，换模型就自动重建，不用谁记得去清缓存。
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

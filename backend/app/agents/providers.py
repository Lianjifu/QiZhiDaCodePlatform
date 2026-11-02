"""大模型供应商预设表。

LangChain 的 provider 名字和国内实际在用的服务对不上：DeepSeek、通义、Kimi
这些都要用 `langchain-openai` + 自定义 base_url 才能接。这里把常见服务整理成
预设，前端直接下拉选，URL / API key 由用户按需覆盖。

绝大多数厂商都提供了 OpenAI 兼容端点，所以 `langchain_provider` 一律填
`"openai"`，只有 Anthropic 和 Azure 这类原生协议才用别的值。

## 两个容易踩的坑

**一、`langchain_provider` 不是我们的枚举值。** 我们的枚举值是 ``key``
（``deepseek`` / ``qwen`` / …，存库用）；``langchain_provider`` 是
``init_chat_model()`` 认识的 provider 名。**填错的后果很具体**：
``init_chat_model("deepseek:xxx")`` 会去找 ``langchain-deepseek`` 这个包 ——
没装就直接报 "Initializing ChatDeepSeek requires the langchain-deepseek
package"。所以国产模型一律填 ``openai`` + 自定义 ``base_url``。

**二、base_url 要按厂商官方文档写。** 带不带 ``/v1`` 各家不一样
（DeepSeek 官方写的是 ``https://api.deepseek.com``，OpenAI 是
``https://api.openai.com/v1``），这里照抄文档，不要凭印象加后缀。
"""

from dataclasses import dataclass

from app.core.exceptions import ValidationError


@dataclass(frozen=True)
class ProviderPreset:
    """一个供应商预设。

    字段含义：

    - ``key`` —— **我们的**枚举值，存库用（``provider`` 字段就是这个值）；
    - ``langchain_provider`` —— ``init_chat_model()`` 认识的 provider 名，
      跟 ``key`` **不是一回事**（见模块说明）；
    - ``default_base_url`` —— 默认接口地址，用户可以覆盖；
    - ``api_key_env`` —— 该厂商惯用的环境变量名（仅作提示，不强制）；
    - ``api_key_required`` —— 该服务是否必须配 Key（Ollama / vLLM 这类本地
      服务不需要）；
    - ``openai_compatible`` —— 是否走 OpenAI 兼容协议。``False`` 的是原生协议
      （Anthropic / Azure），它们的请求格式和参数名都不一样；
    - ``context_window`` —— 该厂商模型的**上下文窗口**（token 数，0 = 未知）。

      这个值不只是"展示用"：deepAgents 的摘要阈值按
      ``model.profile["max_input_tokens"]`` 算比例，而 OpenAI 兼容端点挂上去的
      模型对象**没有 profile** —— 不显式告诉它，框架只能退回到固定的
      "170000 token 触发、保留 6 条消息"，对大窗口的模型来说是提前太多、
      保留得太少。
    - ``vision_enabled`` —— 该厂商的模型**能不能看图**。

      这不是可有可无的字段：对话里内联的图片是直接发给模型的，模型不认
      ``image_url`` 就会直接报错。默认值按厂商给，用户可以在配置里改 ——
      同一个厂商不同模型的视觉能力并不一样（Kimi 的 VL 系列能看，
      主力文本模型不能）。
    """

    key: str  # 我们的枚举值，存库用
    display_name: str  # 界面展示名
    langchain_provider: str  # init_chat_model 的 provider
    default_base_url: str  # 默认接口地址
    api_key_env: str  # 该厂商惯用的环境变量名
    api_key_required: bool = True
    openai_compatible: bool = True
    docs_url: str = ""
    # 上下文窗口（token）。0 = 未知 —— 此时不挂 profile，让框架用保守默认值
    context_window: int = 0
    # 能不能看图
    vision_enabled: bool = False


PROVIDER_PRESETS: tuple[ProviderPreset, ...] = (
    ProviderPreset(
        key="openai",
        display_name="OpenAI",
        langchain_provider="openai",
        default_base_url="https://api.openai.com/v1",
        api_key_env="OPENAI_API_KEY",
        docs_url="https://platform.openai.com/docs",
        context_window=128000,
        # 这家的模型能看图
        vision_enabled=True,
    ),
    ProviderPreset(
        key="anthropic",
        display_name="Anthropic Claude",
        langchain_provider="anthropic",
        default_base_url="https://api.anthropic.com",
        api_key_env="ANTHROPIC_API_KEY",
        openai_compatible=False,
        docs_url="https://docs.anthropic.com",
        context_window=200000,
        # 这家的模型能看图
        vision_enabled=True,
    ),
    ProviderPreset(
        key="deepseek",
        display_name="DeepSeek 深度求索",
        langchain_provider="openai",
        # 官方文档给的 OpenAI 兼容 base_url（带不带 /v1 都收，这里按文档写）
        default_base_url="https://api.deepseek.com",
        api_key_env="DEEPSEEK_API_KEY",
        docs_url="https://api-docs.deepseek.com",
        # 实测官方 /models 接口返回 context_window=1048576
        context_window=1048576,
        # 这家的模型能看图
        vision_enabled=True,
    ),
    ProviderPreset(
        key="moonshot",
        display_name="月之暗面 Kimi",
        langchain_provider="openai",
        default_base_url="https://api.moonshot.cn/v1",
        api_key_env="MOONSHOT_API_KEY",
        docs_url="https://platform.moonshot.cn/docs",
        context_window=131072,
    ),
    ProviderPreset(
        key="qwen",
        display_name="阿里云百炼 通义千问",
        langchain_provider="openai",
        default_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        api_key_env="DASHSCOPE_API_KEY",
        docs_url="https://help.aliyun.com/zh/model-studio/",
    ),
    ProviderPreset(
        key="zhipu",
        display_name="智谱 GLM",
        langchain_provider="openai",
        default_base_url="https://open.bigmodel.cn/api/paas/v4",
        api_key_env="ZHIPUAI_API_KEY",
        docs_url="https://open.bigmodel.cn/dev/api",
        context_window=128000,
    ),
    ProviderPreset(
        key="doubao",
        display_name="字节 豆包（火山方舟）",
        langchain_provider="openai",
        default_base_url="https://ark.cn-beijing.volces.com/api/v3",
        api_key_env="ARK_API_KEY",
        docs_url="https://www.volcengine.com/docs/82379",
    ),
    ProviderPreset(
        key="siliconflow",
        display_name="硅基流动 SiliconFlow",
        langchain_provider="openai",
        default_base_url="https://api.siliconflow.cn/v1",
        api_key_env="SILICONFLOW_API_KEY",
        docs_url="https://docs.siliconflow.cn",
    ),
    ProviderPreset(
        key="openrouter",
        display_name="OpenRouter",
        langchain_provider="openai",
        default_base_url="https://openrouter.ai/api/v1",
        api_key_env="OPENROUTER_API_KEY",
        docs_url="https://openrouter.ai/docs",
    ),
    ProviderPreset(
        key="groq",
        display_name="Groq",
        langchain_provider="openai",
        default_base_url="https://api.groq.com/openai/v1",
        api_key_env="GROQ_API_KEY",
        docs_url="https://console.groq.com/docs",
    ),
    ProviderPreset(
        key="mistral",
        display_name="Mistral AI",
        langchain_provider="openai",
        default_base_url="https://api.mistral.ai/v1",
        api_key_env="MISTRAL_API_KEY",
        docs_url="https://docs.mistral.ai",
        context_window=128000,
    ),
    ProviderPreset(
        key="together",
        display_name="Together AI",
        langchain_provider="openai",
        default_base_url="https://api.together.xyz/v1",
        api_key_env="TOGETHER_API_KEY",
        docs_url="https://docs.together.ai",
    ),
    ProviderPreset(
        key="xai",
        display_name="xAI Grok",
        langchain_provider="openai",
        default_base_url="https://api.x.ai/v1",
        api_key_env="XAI_API_KEY",
        docs_url="https://docs.x.ai",
    ),
    ProviderPreset(
        key="gemini",
        display_name="Google Gemini",
        langchain_provider="openai",
        default_base_url="https://generativelanguage.googleapis.com/v1beta/openai",
        api_key_env="GEMINI_API_KEY",
        docs_url="https://ai.google.dev/docs",
        context_window=1048576,
        # 这家的模型能看图
        vision_enabled=True,
    ),
    ProviderPreset(
        key="azure_openai",
        display_name="Azure OpenAI",
        langchain_provider="azure_openai",
        default_base_url="",
        api_key_env="AZURE_OPENAI_API_KEY",
        openai_compatible=False,
        docs_url="https://learn.microsoft.com/azure/ai-services/openai/",
        # 这家的模型能看图
        vision_enabled=True,
    ),
    ProviderPreset(
        key="ollama",
        display_name="Ollama 本地模型",
        langchain_provider="ollama",
        default_base_url="http://localhost:11434",
        api_key_env="OLLAMA_API_KEY",
        api_key_required=False,
        docs_url="https://ollama.com",
    ),
    ProviderPreset(
        key="vllm",
        display_name="vLLM 自建推理",
        langchain_provider="openai",
        default_base_url="http://localhost:8000/v1",
        api_key_env="VLLM_API_KEY",
        api_key_required=False,
        docs_url="https://docs.vllm.ai",
    ),
    ProviderPreset(
        key="custom",
        display_name="自定义 OpenAI 兼容端点",
        langchain_provider="openai",
        default_base_url="",
        api_key_env="",
        api_key_required=False,
        docs_url="",
    ),
)

_PRESET_MAP: dict[str, ProviderPreset] = {p.key: p for p in PROVIDER_PRESETS}


def list_presets() -> list[ProviderPreset]:
    """全部预设，按界面展示顺序。

    返回**副本** —— 预设是 frozen dataclass，调用方改不动，但返回新列表能避免
    有人原地增删全局表。
    """
    return list(PROVIDER_PRESETS)


def get_preset(provider_key: str) -> ProviderPreset | None:
    """按 key 取预设；不认识返回 ``None``（不报错）。

    与 :func:`resolve_preset` 的区别：这里用于"有没有这个选项"这类查询
    （比如校验请求里的 provider 值），那报错由调用方决定。
    """
    return _PRESET_MAP.get(provider_key)


def resolve_preset(provider_key: str) -> ProviderPreset:
    """取预设；不认识就报错，避免拼错 key 时静默走到错误的厂商。"""
    preset = _PRESET_MAP.get(provider_key)
    if preset is None:
        supported = "、".join(sorted(_PRESET_MAP))
        raise ValidationError(f"不支持的供应商 {provider_key}，可选：{supported}")
    return preset

"""大模型供应商预设表。

LangChain 的 provider 名字和国内实际在用的服务对不上：DeepSeek、通义、Kimi
这些都要用 `langchain-openai` + 自定义 base_url 才能接。这里把常见服务整理成
预设，前端直接下拉选，URL / API key 由用户按需覆盖。

绝大多数厂商都提供了 OpenAI 兼容端点，所以 `langchain_provider` 一律填
`"openai"`，只有 Anthropic 和 Azure 这类原生协议才用别的值。
"""

from dataclasses import dataclass

from app.core.exceptions import ValidationError


@dataclass(frozen=True)
class ProviderPreset:
    """一个供应商预设。"""

    key: str  # 我们的枚举值，存库用
    display_name: str  # 界面展示名
    langchain_provider: str  # init_chat_model 的 provider
    default_base_url: str  # 默认接口地址
    api_key_env: str  # 该厂商惯用的环境变量名
    api_key_required: bool = True
    openai_compatible: bool = True
    docs_url: str = ""


PROVIDER_PRESETS: tuple[ProviderPreset, ...] = (
    ProviderPreset(
        key="openai",
        display_name="OpenAI",
        langchain_provider="openai",
        default_base_url="https://api.openai.com/v1",
        api_key_env="OPENAI_API_KEY",
        docs_url="https://platform.openai.com/docs",
    ),
    ProviderPreset(
        key="anthropic",
        display_name="Anthropic Claude",
        langchain_provider="anthropic",
        default_base_url="https://api.anthropic.com",
        api_key_env="ANTHROPIC_API_KEY",
        openai_compatible=False,
        docs_url="https://docs.anthropic.com",
    ),
    ProviderPreset(
        key="deepseek",
        display_name="DeepSeek 深度求索",
        langchain_provider="openai",
        default_base_url="https://api.deepseek.com/v1",
        api_key_env="DEEPSEEK_API_KEY",
        docs_url="https://api-docs.deepseek.com",
    ),
    ProviderPreset(
        key="moonshot",
        display_name="月之暗面 Kimi",
        langchain_provider="openai",
        default_base_url="https://api.moonshot.cn/v1",
        api_key_env="MOONSHOT_API_KEY",
        docs_url="https://platform.moonshot.cn/docs",
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
    ),
    ProviderPreset(
        key="azure_openai",
        display_name="Azure OpenAI",
        langchain_provider="azure_openai",
        default_base_url="",
        api_key_env="AZURE_OPENAI_API_KEY",
        openai_compatible=False,
        docs_url="https://learn.microsoft.com/azure/ai-services/openai/",
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
    return list(PROVIDER_PRESETS)


def get_preset(provider_key: str) -> ProviderPreset | None:
    return _PRESET_MAP.get(provider_key)


def resolve_preset(provider_key: str) -> ProviderPreset:
    """取预设；不认识就报错，避免拼错 key 时静默走到错误的厂商。"""
    preset = _PRESET_MAP.get(provider_key)
    if preset is None:
        supported = "、".join(sorted(_PRESET_MAP))
        raise ValidationError(f"不支持的供应商 {provider_key}，可选：{supported}")
    return preset

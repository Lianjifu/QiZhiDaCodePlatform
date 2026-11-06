"""模型工厂（`app/agents/llm.py`）的供应商解析测试。

盯的是**`.env` 那条路怎么把 ``LLM_PROVIDER`` 翻译成 langchain 认识的 provider
名** —— 这里曾经有个坏兜底：``LLM_PROVIDER="deepseek"`` 被原样丢给
``init_chat_model()``，它会去找没装的 ``langchain-deepseek`` 包，
直接报 "Initializing ChatDeepSeek requires the langchain-deepseek package"。

现在 ``.env`` 里的 provider 走和数据库那条路**同一套预设映射**。
"""

import pytest

from app.agents import llm as llm_module
from app.agents.llm import get_chat_model
from app.core.config import settings


@pytest.fixture(autouse=True)
def _清模型缓存():
    """模型对象是按配置身份缓存的 —— 换配置要重新建，别让上一条测试的留下来。"""
    llm_module._MODEL_CACHE.clear()
    llm_module._MODEL_IDENTITIES.clear()
    yield
    llm_module._MODEL_CACHE.clear()
    llm_module._MODEL_IDENTITIES.clear()


@pytest.fixture
def 抓到的模型(monkeypatch):
    """把 ``init_chat_model()`` 换成记录器，返回 ``(model_str, kwargs)``。"""
    记录: dict[str, object] = {}

    def fake_init(model: str, **kwargs):
        记录["model"] = model
        记录["kwargs"] = kwargs
        return FakeModel()

    monkeypatch.setattr(llm_module, "init_chat_model", fake_init)
    return 记录


class FakeModel:
    """够用的假模型对象 —— 只挂 profile，模型工厂只碰这两处。"""

    def __init__(self):
        self.profile: dict = {}


def _设环境(monkeypatch, **覆盖):
    """改 `.env` 那套配置（默认值取自 ``settings``，逐项覆盖）。"""
    默认 = {
        "llm_provider": "openai",
        "llm_model": "gpt-4o-mini",
        "llm_api_key": "",
        "llm_base_url": "",
        "llm_temperature": 0.0,
        "llm_max_tokens": 4096,
        "llm_context_window": 0,
        # None = "没写" → 退到厂商预设（跟数据库那条路一个口径）
        "llm_vision_enabled": None,
    }
    默认.update(覆盖)
    for 名, 值 in 默认.items():
        monkeypatch.setattr(settings, 名, 值)


# ---------- .env 里的 provider 名 ----------

def test_预设_key_映射到_langchain_provider(抓到的模型, monkeypatch):
    """``LLM_PROVIDER="deepseek"`` → ``openai:模型名`` + DeepSeek 的地址。"""
    _设环境(
        monkeypatch,
        llm_provider="deepseek",
        llm_model="deepseek-chat",
        llm_api_key="sk-test",
        llm_base_url="",
    )

    get_chat_model()

    assert 抓到的模型["model"] == "openai:deepseek-chat"
    assert 抓到的模型["kwargs"]["base_url"] == "https://api.deepseek.com"
    assert 抓到的模型["kwargs"]["api_key"] == "sk-test"


def test_环境变量里的地址盖过预设默认地址(抓到的模型, monkeypatch):
    """用户在 ``.env`` 里显式写了地址就听用户的 —— 跟数据库那条路一个口径。"""
    _设环境(
        monkeypatch,
        llm_provider="deepseek",
        llm_model="deepseek-chat",
        llm_api_key="sk-test",
        llm_base_url="https://my-gateway.example.com/v1",
    )

    get_chat_model()

    assert 抓到的模型["kwargs"]["base_url"] == "https://my-gateway.example.com/v1"


def test_langchain_认识的名字不在预设表里也能直接用(抓到的模型, monkeypatch):
    """预设表没收录的 langchain provider 名，照旧原样用。

    ``fireworks`` 之类 langchain 自己认识、我们没做预设的名字，不该被拦下 ——
    预设表是"我们推荐怎么配"，不是"只许写这些"。
    """
    _设环境(
        monkeypatch,
        llm_provider="fireworks",
        llm_model="accounts/fireworks/models/llama-v3p1-70b-instruct",
        llm_api_key="sk-test",
        llm_base_url="",
    )

    get_chat_model()

    assert 抓到的模型["model"] == "fireworks:accounts/fireworks/models/llama-v3p1-70b-instruct"
    assert "base_url" not in 抓到的模型["kwargs"]


def test_不认识的_provider名不做映射(抓到的模型, monkeypatch):
    """既不是预设 key、langchain 也不认识 —— 原样传下去，由 langchain 报错。

    **不在这里吞异常**：厂商集成包没装、模型名写错，这些是配置错误，
    应该在建模型的时候立刻炸出来（见模块 docstring）。
    """
    _设环境(
        monkeypatch,
        llm_provider="some-unknown-vendor",
        llm_model="whatever",
        llm_api_key="sk-test",
        llm_base_url="",
    )

    get_chat_model()

    assert 抓到的模型["model"] == "some-unknown-vendor:whatever"
    assert "base_url" not in 抓到的模型["kwargs"]


def test_azure_走_azure_endpoint_参数名(抓到的模型, monkeypatch):
    """Azure 的参数名不一样：``azure_endpoint`` + ``api_version``，不是 ``base_url``。"""
    _设环境(
        monkeypatch,
        llm_provider="azure_openai",
        llm_model="gpt-4o",
        llm_api_key="sk-azure",
        llm_base_url="https://example.openai.azure.com",
    )

    get_chat_model()

    assert 抓到的模型["model"] == "azure_openai:gpt-4o"
    assert "base_url" not in 抓到的模型["kwargs"]
    assert 抓到的模型["kwargs"]["azure_endpoint"] == "https://example.openai.azure.com"
    assert 抓到的模型["kwargs"]["api_version"] == settings.azure_openai_api_version


# ---------- 上下文窗口与视觉 ----------

def test_上下文窗口没写就退到预设的(抓到的模型, monkeypatch):
    """``LLM_CONTEXT_WINDOW=0`` → 用预设的窗口。

    不给大窗口的话，deepAgents 的摘要中间件会退回到固定的"170000 token
    触发、保留 6 条消息"，对 1M 窗口的模型等于只用了两成。
    """
    _设环境(
        monkeypatch,
        llm_provider="deepseek",
        llm_model="deepseek-chat",
        llm_api_key="sk-test",
        llm_context_window=0,
    )

    model = get_chat_model()

    assert model.profile["max_input_tokens"] == 1048576


def test_上下文窗口写了就用写的(抓到的模型, monkeypatch):
    _设环境(
        monkeypatch,
        llm_provider="deepseek",
        llm_model="deepseek-chat",
        llm_api_key="sk-test",
        llm_context_window=64000,
    )

    model = get_chat_model()

    assert model.profile["max_input_tokens"] == 64000


def test_视觉没写就跟着厂商预设走(抓到的模型, monkeypatch):
    """``LLM_VISION_ENABLED`` 没写 → 用预设的（DeepSeek 能看图）。

    跟数据库那条路一个口径：配置上没填就取预设的值。
    """
    _设环境(
        monkeypatch,
        llm_provider="deepseek",
        llm_model="deepseek-chat",
        llm_api_key="sk-test",
        llm_vision_enabled=None,
    )

    model = get_chat_model()

    assert model.profile[llm_module.VISION_PROFILE_KEY] is True


def test_视觉没写_厂商不能看图就不挂(抓到的模型, monkeypatch):
    """预设说不能看图 → 不挂（别给模型发它认不了的图片）。"""
    _设环境(
        monkeypatch,
        llm_provider="moonshot",
        llm_model="moonshot-v1-8k",
        llm_api_key="sk-test",
        llm_vision_enabled=None,
    )

    model = get_chat_model()

    assert llm_module.VISION_PROFILE_KEY not in model.profile


def test_视觉开关打开才挂_profile(抓到的模型, monkeypatch):
    _设环境(
        monkeypatch,
        llm_provider="deepseek",
        llm_model="deepseek-chat",
        llm_api_key="sk-test",
        llm_vision_enabled=True,
    )

    model = get_chat_model()

    assert model.profile[llm_module.VISION_PROFILE_KEY] is True


def test_视觉显式关掉就不挂(抓到的模型, monkeypatch):
    """显式写 false → 盖过预设（DeepSeek 预设能看图，但用户说关）。"""
    _设环境(
        monkeypatch,
        llm_provider="deepseek",
        llm_model="deepseek-chat",
        llm_api_key="sk-test",
        llm_vision_enabled=False,
    )

    model = get_chat_model()

    assert llm_module.VISION_PROFILE_KEY not in model.profile


# ---------- 启动时要不要报「缺 Key」 ----------

def test_本地推理不报缺_key(monkeypatch):
    """ollama / vllm / custom 都不需要 Key —— 缺了 Key 不该报警。"""
    from app.main import _should_warn_missing_api_key

    monkeypatch.setattr(settings, "llm_api_key", "")
    for provider in ("ollama", "vllm", "custom"):
        monkeypatch.setattr(settings, "llm_provider", provider)
        assert _should_warn_missing_api_key() is False


def test_云厂商报缺_key(monkeypatch):
    from app.main import _should_warn_missing_api_key

    monkeypatch.setattr(settings, "llm_api_key", "")
    monkeypatch.setattr(settings, "llm_provider", "deepseek")
    assert _should_warn_missing_api_key() is True

    # 不在预设表里的名字（langchain 自己的 provider 名）—— 一律要求 Key
    monkeypatch.setattr(settings, "llm_provider", "fireworks")
    assert _should_warn_missing_api_key() is True


def test_配了_key_就不报(monkeypatch):
    from app.main import _should_warn_missing_api_key

    monkeypatch.setattr(settings, "llm_api_key", "sk-test")
    monkeypatch.setattr(settings, "llm_provider", "deepseek")
    assert _should_warn_missing_api_key() is False


# ---------- 有效窗口（预览接口与模型对象要一致） ----------

@pytest.mark.asyncio
async def test_预览接口的预算跟模型窗口一致(client, monkeypatch):
    """预览报的字符预算必须按**有效**窗口算。

    环境变量没写窗口时，预设给的是 1048576、兜底只有 128000 —— 两者差八倍。
    预览要是还按 128000 算，界面上报的预算就跟模型实际的窗口对不上。
    """
    _设环境(
        monkeypatch,
        llm_provider="deepseek",
        llm_model="deepseek-chat",
        llm_api_key="sk-test",
        llm_context_window=0,
    )

    resp = await client.get("/api/v1/context/preview")
    assert resp.status_code == 200
    预算 = resp.json()["data"]["budget_chars"]

    有效窗口 = llm_module.env_context_window()
    assert 有效窗口 == 1048576
    assert 预算 > 128000 * 0.5 * 4  # 不是那个保守兜底算出来的


def test_有效窗口跟模型对象上挂的是同一个数(抓到的模型, monkeypatch):
    """上下文预览接口报的预算必须跟模型实际的窗口对得上。

    预览说 17 万 token、模型其实有 100 万 —— 排查时会被带偏。
    """
    _设环境(
        monkeypatch,
        llm_provider="deepseek",
        llm_model="deepseek-chat",
        llm_api_key="sk-test",
        llm_context_window=0,
    )

    model = get_chat_model()

    assert llm_module.env_context_window() == model.profile["max_input_tokens"]


def test_有效窗口_不在预设表里的_provider_就是_0(抓到的模型, monkeypatch):
    """预设表里没有的名字 → 窗口未知（0），不猜。"""
    _设环境(
        monkeypatch,
        llm_provider="some-unknown-vendor",
        llm_model="whatever",
        llm_api_key="sk-test",
        llm_context_window=0,
    )

    assert llm_module.env_context_window() == 0


# ---------- 缓存 ----------

def test_同一份配置返回同一个模型对象(抓到的模型, monkeypatch):
    """模型被编进了图里，图要跨请求复用 —— 模型对象必须稳定。"""
    _设环境(monkeypatch, llm_provider="deepseek", llm_model="deepseek-chat")

    first = get_chat_model()
    second = get_chat_model()

    assert first is second


def test_配置变了就是另一个模型对象(抓到的模型, monkeypatch):
    _设环境(monkeypatch, llm_provider="deepseek", llm_model="deepseek-chat")
    first = get_chat_model()

    _设环境(monkeypatch, llm_provider="deepseek", llm_model="deepseek-reasoner")
    second = get_chat_model()

    assert first is not second

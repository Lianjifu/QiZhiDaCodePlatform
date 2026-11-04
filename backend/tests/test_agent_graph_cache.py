"""图缓存：同一会话里编译好的图能不能跨轮复用。

三条底线：

1. **复用的必须是同一张图** —— 工具集、技能、后端、权限模式都没变时，
   就不该再编一次（一百毫秒上下）；
2. **变了就得重建** —— 权限模式、能力清单、模型、工作区，任何一样变了，
   复用旧图就是"用错了规则"；
3. **每轮变化的内容不能被冻住** —— 记忆目录与上传件清单每轮都不一样，
   编进图里的话，复用等于让 agent 一直看着第一轮的旧记忆。
"""

from __future__ import annotations

import pytest

from app.agents.graph_cache import GRAPH_CACHE
from app.agents.llm import model_identity
from app.agents.permissions import set_permission_mode
from app.agents.runner import AgentRunner
from app.capabilities.execution.context import ExecutionContext
from tests.fake_chat_model import FakeChatModel


@pytest.fixture(autouse=True)
def _clear_cache():
    """每个测试从干净的缓存开始。

    缓存是**进程内**的（跟图共享命运）—— 测试之间不清，后一个测试可能拿到
    前一个的图。
    """
    GRAPH_CACHE.clear()
    yield
    GRAPH_CACHE.clear()


@pytest.fixture
def runner(tmp_path):
    return AgentRunner(model=FakeChatModel())


async def test_同一个会话复用同一张图(runner, tmp_path):
    """工具集、技能、后端、权限模式都没变 —— 第二轮不该重新编图。"""
    first = await runner._build("t-cache", tmp_path, specs=[], context=None, skill_ids=None)
    second = await runner._build("t-cache", tmp_path, specs=[], context=None, skill_ids=None)
    assert first.agent is second.agent


async def test_不同会话各编各的图(runner, tmp_path):
    """缓存是按会话分的 —— 两个会话不该共用一张图。"""
    first = await runner._build("t-a", tmp_path, specs=[], context=None, skill_ids=None)
    second = await runner._build("t-b", tmp_path, specs=[], context=None, skill_ids=None)
    assert first.agent is not second.agent


async def test_权限模式变了就重建图(runner, tmp_path):
    """用户把模式从"自动"改成"手动" —— 旧的放行规则不能留着。"""
    set_permission_mode("t-mode", "auto")
    auto = await runner._build("t-mode", tmp_path, specs=[], context=None, skill_ids=None)
    set_permission_mode("t-mode", "manual")
    manual = await runner._build("t-mode", tmp_path, specs=[], context=None, skill_ids=None)
    assert auto.agent is not manual.agent


async def test_工作区变了就重建图(runner, tmp_path):
    """文件工具指着哪个目录 —— 不能换。"""
    other = tmp_path / "other"
    other.mkdir()
    first = await runner._build("t-ws", tmp_path, specs=[], context=None, skill_ids=None)
    second = await runner._build("t-ws", other, specs=[], context=None, skill_ids=None)
    assert first.agent is not second.agent


async def test_复用图时刷新数据库会话(runner, tmp_path):
    """数据库会话每请求都换 —— 不刷新就会往已经关掉的会话里写记录。"""
    first = await runner._build("t-session", tmp_path, specs=[], context=None, skill_ids=None)
    old_session = first.context.session
    new_session = object()
    context = ExecutionContext.build(tmp_path)
    context.session = new_session
    second = await runner._build(
        "t-session", tmp_path, specs=[], context=context, skill_ids=None
    )
    assert second.agent is first.agent
    assert second.context.session is new_session
    assert old_session is not new_session


async def test_记忆目录每轮都跟着变(runner, tmp_path, session):
    """记忆目录每轮都变 —— 复用图也不能把它冻住。

    这是"图能复用"的前提：**编进提示词的东西必须每轮重填**，
    否则 agent 一直看着第一轮的旧记忆。
    """
    from langchain_core.messages import AIMessage
    from langchain_core.outputs import ChatGeneration, ChatResult

    from app.agents.dynamic_context import PLACEHOLDER_MEMORY
    from app.memory.service import MemoryService

    class _Recorder(FakeChatModel):
        systems: list[str] = []

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):  # type: ignore[override]
            first = messages[0]
            content = getattr(first, "content", "") if first is not None else ""
            if isinstance(content, list):
                # 系统提示词可能是内容块列表
                content = "\n".join(
                    block.get("text", "") for block in content if isinstance(block, dict)
                )
            self.systems.append(str(content))
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content=self.reply))])

    model = _Recorder()
    runner.model = model

    service = MemoryService(session=session, thread_id="t-live")
    await service.remember(content="项目用 pnpm 而不是 npm", kind="preference", tags=["pnpm"])

    context = ExecutionContext.build(tmp_path)
    context.session = session
    context.thread_id = "t-live"

    await runner.run("pnpm 包管理器怎么用的？", "t-live", tmp_path, context=context)
    assert model.systems, "模型没收到任何请求"
    assert PLACEHOLDER_MEMORY not in model.systems[-1], "占位符没被替换掉"
    assert "pnpm" in model.systems[-1], "记忆目录没进到提示词里"

    # 第二轮：目录重新召回（哪怕内容一样，也不能是编译期写死的那份）
    model.systems.clear()
    await runner.run("pnpm 再问一次", "t-live", tmp_path, context=context)
    assert model.systems
    assert PLACEHOLDER_MEMORY not in model.systems[-1]
    assert "pnpm" in model.systems[-1]


async def test_换模型就重建图(runner, tmp_path):
    """模型被编进图里了 —— 换模型必须重建。"""
    first = await runner._build("t-model", tmp_path, specs=[], context=None, skill_ids=None)
    runner.model = FakeChatModel()
    second = await runner._build("t-model", tmp_path, specs=[], context=None, skill_ids=None)
    assert first.agent is not second.agent


async def test_模型身份跟着配置走():
    """同一个模型对象 → 同一个身份；换对象 → 换身份。"""
    model = FakeChatModel()
    assert model_identity(model) == model_identity(model)
    assert model_identity(model) != model_identity(FakeChatModel())

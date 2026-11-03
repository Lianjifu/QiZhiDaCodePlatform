"""记忆模块的测试。

覆盖这几条最容易出问题的地方：

- **egress 维度拆分**：固定出口不该按 network 风险拦；老数据（没有新字段）
  仍然按最保守的��处理；
- **写入的硬校验**：凭据、空内容、重复合并、同主题冲突；
- **降权不淘汰**：时间衰减有下限，命中即重新激活；
- **渐进式披露**：目录有预算、search 只给摘要不给全文、MMR 去冗余。
"""

from __future__ import annotations

import pytest

from app.capabilities.errors import CapabilityError
from app.capabilities.execution.risk import assess
from app.capabilities.registration.policy import (
    EGRESS_TARGET_FIXED,
    EGRESS_TARGET_MODEL_CONTROLLED,
    Annotations,
    RiskLevel,
)
from app.capabilities.registration.spec import CapabilitySpec
from app.memory.catalog import build_catalog, mmr_select
from app.memory.models import (
    KIND_PREFERENCE,
    SCOPE_SESSION,
    MemoryEntry,
    content_hash,
    effective_score,
    time_factor,
)
from app.memory.service import MemoryService

THREAD = "t-memory-test"


# ---------------------------------------------------------------- egress 拆分


def _spec(annotations: Annotations, risk: str = RiskLevel.READ) -> CapabilitySpec:
    return CapabilitySpec(key="probe", risk=risk, annotations=annotations)


def test_fixed_egress_is_not_network_risk():
    """出口由平台定死的，不该按 network 风险拦。

    这是记忆检索能被自动放行的前提 —— 否则 balanced 模式下每查一次记忆都要
    人工审批。
    """
    spec = _spec(Annotations(read_only=True, open_world=True, egress_target=EGRESS_TARGET_FIXED))
    assessment = assess(spec, {})
    assert assessment.risk == RiskLevel.WRITE
    assert assessment.risk != RiskLevel.NETWORK


def test_model_controlled_egress_still_is_network():
    """模型能决定地址的出口照旧按 network 处理。"""
    spec = _spec(
        Annotations(
            read_only=True,
            open_world=True,
            egress_target=EGRESS_TARGET_MODEL_CONTROLLED,
        )
    )
    assessment = assess(spec, {})
    assert assessment.risk == RiskLevel.NETWORK


def test_missing_egress_fields_default_to_conservative():
    """老数据没有 egress 字段 → 按最保守的（模型能操控出口）处理。

    这样改造前的行为完全不变，不需要写数据迁移。
    """
    annotations = Annotations.from_dict({"read_only": True, "open_world": True})
    assert annotations.egress_target == EGRESS_TARGET_MODEL_CONTROLLED
    assert annotations.egress_data is True
    assert assess(_spec(annotations), {}).risk == RiskLevel.NETWORK


def test_fixed_egress_without_data_stays_read():
    """固定出口且不发数据 → 保持 read，不上调。"""
    spec = _spec(
        Annotations(
            read_only=True,
            open_world=True,
            egress_target=EGRESS_TARGET_FIXED,
            egress_data=False,
        )
    )
    assert assess(spec, {}).risk == RiskLevel.READ


def test_fixed_egress_is_not_a_threat_signal():
    """固定出口只是**分类调整**，不是威胁信号 —— 不该再挂一次让人批。

    这是个真实的坑：记忆检索会被评估成 write（有数据外发），而参数级规则
    原本把"任何风险上调"都当成要人批 —— 于是每查一次记忆都要人工审批。
    """
    spec = _spec(Annotations(read_only=True, open_world=True, egress_target=EGRESS_TARGET_FIXED))
    assessment = assess(spec, {})
    assert assessment.risk == RiskLevel.WRITE
    assert assessment.reasons  # 记了原因（审计要用）
    assert not assessment.needs_review  # 但不是威胁信号


def test_model_controlled_egress_is_a_threat_signal():
    """出口由模型决定 = SSRF 面 —— 威胁信号，要人看一眼。"""
    spec = _spec(
        Annotations(
            read_only=True,
            open_world=True,
            egress_target=EGRESS_TARGET_MODEL_CONTROLLED,
        )
    )
    assert assess(spec, {}).needs_review


# ---------------------------------------------------------------- 写入校验


async def test_write_rejects_secret(session):
    """含凭据的内容直接拒 —— 掩码后存进去是一条没用的记忆。"""
    service = MemoryService(session=session, thread_id=THREAD)
    with pytest.raises(CapabilityError) as exc:
        await service.remember(
            content="数据库密码是 password=hunter2",
            kind=KIND_PREFERENCE,
        )
    assert "凭据" in str(exc.value)


async def test_write_rejects_empty(session):
    service = MemoryService(session=session, thread_id=THREAD)
    with pytest.raises(CapabilityError):
        await service.remember(content="   ", kind=KIND_PREFERENCE)


async def test_write_rejects_oversized_content(session):
    service = MemoryService(session=session, thread_id=THREAD)
    with pytest.raises(CapabilityError) as exc:
        await service.remember(content="x" * 5000, kind=KIND_PREFERENCE)
    assert "过长" in str(exc.value)


async def test_write_merges_duplicate_instead_of_adding(session):
    """同内容不新增，而是合并 —— 记忆库膨胀会把真正需要的条目淹掉。"""
    service = MemoryService(session=session, thread_id=THREAD)
    first = await service.remember(content="项目用 pnpm 不用 npm", kind=KIND_PREFERENCE)
    second = await service.remember(content="项目用 pnpm 不用 npm", kind=KIND_PREFERENCE)
    assert first.id == second.id
    entries = await service.list_all(scope=SCOPE_SESSION)
    assert len(entries) == 1
    # use_count 是"被召回过几次"，写入不算召回
    assert entries[0].use_count == 0


async def test_conflicting_memory_requires_supersedes(session):
    """同主题但结论不同 → 必须显式覆盖，否则两条打架。

    冲突判据是「类型相同 + 有共同标签」：真正矛盾的两条说的往往不是同一套词
    （"用 pnpm" vs "改用 npm"），只有标签能指出它们讲的是同一个话题。
    """
    service = MemoryService(session=session, thread_id=THREAD)
    old = await service.remember(
        content="项目用 npm 管理依赖",
        kind=KIND_PREFERENCE,
        tags=["依赖管理"],
    )
    with pytest.raises(CapabilityError) as exc:
        await service.remember(
            content="项目改用 pnpm 管理依赖",
            kind=KIND_PREFERENCE,
            tags=["依赖管理"],
        )
    # 报错里要带上旧记忆的内容 —— 模型得先看清自己要推翻什么
    assert "supersedes" in str(exc.value)
    assert "项目用 npm 管理依赖" in str(exc.value)

    # 显式覆盖：旧的被标记为取代（不删除），新的留下
    await service.remember(
        content="项目改用 pnpm 管理依赖",
        kind=KIND_PREFERENCE,
        tags=["依赖管理"],
        supersedes=old.id,
    )
    entries = await service.list_all(scope=SCOPE_SESSION)
    superseded = [item for item in entries if item.superseded]
    alive = [item for item in entries if not item.superseded]
    assert [item.content for item in alive] == ["项目改用 pnpm 管理依赖"]
    assert [item.content for item in superseded] == ["项目用 npm 管理依赖"]
    # 被取代的不再参与召回
    hits = await service.recall("依赖", scope=SCOPE_SESSION)
    assert [item.content for item in hits] == ["项目改用 pnpm 管理依赖"]


async def test_different_topics_do_not_conflict(session):
    """标签不搭的两条偏好不该互相拦 —— 那是正常的多条记忆。"""
    service = MemoryService(session=session, thread_id=THREAD)
    await service.remember(
        content="回答一律用中文",
        kind=KIND_PREFERENCE,
        tags=["回答语言"],
    )
    entry = await service.remember(
        content="代码注释一律用英文",
        kind=KIND_PREFERENCE,
        tags=["注释语言"],
    )
    assert entry.id


async def test_memories_are_isolated_per_thread(session):
    """会话内记忆按会话隔离 —— 会话之间互不可见。"""
    a = MemoryService(session=session, thread_id="t-a")
    b = MemoryService(session=session, thread_id="t-b")
    await a.remember(content="A 会话记的偏好：用中文回答", kind=KIND_PREFERENCE)
    assert len(await a.list_all(scope=SCOPE_SESSION)) == 1
    assert len(await b.list_all(scope=SCOPE_SESSION)) == 0


async def test_forget_thread_clears_session_memories(session):
    """会话被删 → 该会话的短期记忆一起清（与对话历史同生共死）。"""
    service = MemoryService(session=session, thread_id=THREAD)
    await service.remember(content="这条要被清掉", kind=KIND_PREFERENCE)
    assert await service.forget_thread(THREAD) == 1
    assert len(await service.list_all(scope=SCOPE_SESSION)) == 0


# ---------------------------------------------------------------- 召回与降权


async def test_search_returns_bounded_summary_not_full_text(session):
    """召回只给**有长度上限**的摘要 —— 上下文成本有界。

    正文要模型自己用 memory_read 取；目录里塞全文，一搜命中 20 条就是几千
    token。
    """
    service = MemoryService(session=session, thread_id=THREAD)
    tail = "这一段不应该出现在摘要里"
    await service.remember(
        content="项目用 pnpm 而不是 npm，因为 lockfile 兼容更好。" + "补充说明。" * 60 + tail,
        kind=KIND_PREFERENCE,
        tags=["pnpm"],
    )
    hits = await service.recall("pnpm 依赖", scope=SCOPE_SESSION)
    assert hits
    summary = hits[0].summary(200)
    assert len(summary) <= 201
    assert tail not in summary


async def test_hit_refreshes_use_count(session):
    """命中即重新激活 —— 衰减从最后一次被召回重新算。"""
    service = MemoryService(session=session, thread_id=THREAD)
    await service.remember(content="回答用中文", kind=KIND_PREFERENCE, tags=["语言"])
    first = await service.recall("回答语言", scope=SCOPE_SESSION)
    second = await service.recall("回答语言", scope=SCOPE_SESSION)
    assert first[0].use_count == 0
    assert second[0].use_count == 1


def test_time_factor_has_a_floor():
    """再旧也只降到 0.7 —— 时间不能把记忆踢出视野。

    这是"第一天记的、一周后还要用"那个场景的保障：断崖式衰减会误伤。
    """
    from datetime import UTC, datetime, timedelta

    long_ago = datetime.now(UTC) - timedelta(days=365)
    assert time_factor(long_ago, 30) == pytest.approx(0.7)
    assert time_factor(datetime.now(UTC), 30) == pytest.approx(1.0)


def test_effective_score_is_dominated_by_relevance():
    """相关性主导 —— "很久没用但依然相关"的不该被"刚用过但不太相关"的挤下去。"""
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC)
    relevant_but_old = effective_score(0.9, 0, now - timedelta(days=200), 30, now)
    less_relevant_but_used = effective_score(0.5, 50, now, 30, now)
    assert relevant_but_old > less_relevant_but_used


# ---------------------------------------------------------------- 目录


def _entry(id_: str, title: str, relevance: float, tags: list[str]) -> MemoryEntry:
    return MemoryEntry(
        id=id_,
        scope=SCOPE_SESSION,
        title=title,
        content=title,
        kind=KIND_PREFERENCE,
        tags=tags,
        relevance=relevance,
    )


def test_catalog_respects_budget():
    """目录有预算 —— 装不下的就截断，并标出来。"""
    entries = [_entry(f"s:{i}", f"第{i}条记忆的内容", 1.0 - i / 100, ["标签"]) for i in range(50)]
    catalog = build_catalog(entries, budget_chars=200, max_entries=50)
    assert catalog.truncated
    assert len(catalog.entries) < 50
    assert len(catalog.render()) < 400


def test_catalog_renders_memory_block():
    entries = [_entry("s:1", "项目用 pnpm", 0.9, ["pnpm"])]
    catalog = build_catalog(entries, budget_chars=1200, max_entries=40)
    rendered = catalog.render()
    assert "<memory_catalog>" in rendered
    assert "项目用 pnpm" in rendered
    assert "memory_read" in rendered


def test_empty_catalog_renders_nothing():
    """空目录不占上下文。"""
    catalog = build_catalog([], budget_chars=1200, max_entries=40)
    assert catalog.render() == ""


def test_mmr_prefers_diverse_entries():
    """同一主题的十几条变体不该占满 top-k —— MMR 要挑出互补的。"""
    same = [
        _entry(f"s:{i}", "项目用 pnpm 管理依赖", 0.9 - i / 1000, ["pnpm", "依赖"]) for i in range(4)
    ]
    other = _entry("s:9", "回答用中文", 0.85, ["语言"])
    selected = mmr_select([*same, other], k=2)
    assert any(item.id == "s:9" for item in selected)


# ---------------------------------------------------------------- 工具函数


def test_content_hash_normalizes_whitespace_and_case():
    assert content_hash("用  pnpm") == content_hash("用 PNPM")


# ---------------------------------------------------------------- 编排层接线


async def test_runner_injects_memory_catalog(session, tmp_path):
    """每轮自动注入的是**目录**，不是记忆正文。

    这是渐进式披露的第一层：上下文里只有一行摘要，模型要用正文得自己调
    memory_read。
    """
    from langchain_core.messages import SystemMessage

    from app.agents.runner import AgentRunner
    from app.capabilities.execution.context import ExecutionContext
    from tests.fake_chat_model import FakeChatModel

    class _RecordingModel(FakeChatModel):
        """记住收到的消息，用来检查系统提示词里有什么。"""

        seen: list = []

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):  # type: ignore[override]
            self.seen = list(messages)
            return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)

    service = MemoryService(session=session, thread_id="t-runner")
    await service.remember(
        content="项目用 pnpm 而不是 npm，因为 lockfile 兼容更好",
        kind=KIND_PREFERENCE,
        tags=["pnpm"],
    )

    model = _RecordingModel()
    context = ExecutionContext.build(tmp_path)
    context.session = session
    context.thread_id = "t-runner"

    runner = AgentRunner(model=model)
    await runner.run("pnpm 是什么，怎么用的？", "t-runner", tmp_path, context=context)

    system = "\n".join(
        str(message.content) for message in model.seen if isinstance(message, SystemMessage)
    )
    assert "<memory_catalog>" in system
    assert "pnpm" in system


async def test_runner_without_session_has_no_memory_block(session, tmp_path):
    """没有数据库会话（测试、纯编排）就不注入记忆块 —— 不能因此起不来。"""
    from langchain_core.messages import SystemMessage

    from app.agents.runner import AgentRunner
    from app.capabilities.execution.context import ExecutionContext
    from tests.fake_chat_model import FakeChatModel

    class _RecordingModel(FakeChatModel):
        seen: list = []

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):  # type: ignore[override]
            self.seen = list(messages)
            return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)

    model = _RecordingModel()
    context = ExecutionContext.build(tmp_path)
    context.thread_id = "t-runner"

    runner = AgentRunner(model=model)
    await runner.run("你好", "t-runner", tmp_path, context=context)

    system = "\n".join(
        str(message.content) for message in model.seen if isinstance(message, SystemMessage)
    )
    assert "<memory_catalog>" not in system


async def test_agent_is_given_the_memory_tools(session, tmp_path):
    """记忆的三个能力要真的变成模型能调的工具。

    它们是出厂内置能力，走注册层同步进数据库，再由 ``build_tools`` 变成模型
    能调的工具 —— 少一环，模型就根本没法用记忆。
    """
    from app.capabilities.execution.context import ExecutionContext
    from app.capabilities.execution.tooling import build_tools
    from app.capabilities.registration.registry import CapabilityRegistry

    specs = await CapabilityRegistry.load(session)
    context = ExecutionContext.build(tmp_path)
    tools = await build_tools(specs, context)
    names = {tool.name for tool in tools}
    assert {"memory_search", "memory_read", "memory_write"} <= names


async def test_memory_capabilities_are_installed_and_callable(session):
    """出厂同步之后，记忆的三个能力都在清单里，且声明带正确的 scope 枚举。"""
    from app.capabilities.registration.registry import CapabilityRegistry

    specs = {spec.key: spec for spec in await CapabilityRegistry.load(session)}
    assert "memory_search" in specs
    assert "memory_read" in specs
    assert "memory_write" in specs

    search = specs["memory_search"]
    scope = search.input_schema["properties"]["scope"]
    assert "all" in scope["enum"], "检索要能一次搜两个 scope，否则模型得自己调两次"
    assert search.risk == "read"
    assert search.permission == "none"

    write = specs["memory_write"]
    assert write.risk == "write"
    assert write.permission == "none"
    assert "kind" in write.input_schema["required"], "记忆类型必填 —— 它决定召回准不准"


async def test_builtin_contract_comes_from_code(session):
    """内置能力的契约以代码为准，不能被数据库里的旧副本拖住。

    出厂声明改了参数 schema，实现跟着改了，数据库里那份旧的必须让位 ——
    否则模型看到的是旧契约，调用会直接被参数校验拦下。
    """
    from app.capabilities.registration.builtin import BUILTIN_SPECS
    from app.capabilities.registration.registry import CapabilityRegistry

    specs = {spec.key: spec for spec in await CapabilityRegistry.load(session)}
    for key, builtin in BUILTIN_SPECS.items():
        if key not in specs:
            continue
        assert specs[key].input_schema == builtin.input_schema, f"{key} 的契约与代码不一致"


async def test_resume_keeps_the_memory_catalog(session, tmp_path):
    """恢复挂起的图时也要带记忆目录。

    恢复那一轮没有新的用户输入，但 agent 还在接着做同一件事 —— 目录没了，
    它就凭空丢了记忆。
    """

    from app.agents.checkpointer import open_checkpointer
    from app.agents.runner import AgentRunner
    from app.capabilities.execution.context import ExecutionContext
    from tests.fake_chat_model import FakeChatModel

    class _RecordingModel(FakeChatModel):
        seen: list = []

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):  # type: ignore[override]
            self.seen = list(messages)
            return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)

    service = MemoryService(session=session, thread_id="t-resume")
    await service.remember(
        content="项目用 pnpm 而不是 npm",
        kind=KIND_PREFERENCE,
        tags=["pnpm"],
    )

    model = _RecordingModel()
    context = ExecutionContext.build(tmp_path)
    context.session = session
    context.thread_id = "t-resume"

    # 恢复那条路径依赖 checkpoint 里的历史，所以必须挂 checkpointer
    async with open_checkpointer() as checkpointer:
        runner = AgentRunner(checkpointer=checkpointer, model=model)
        # 先跑一轮，把用户消息写进 checkpoint
        await runner.run("pnpm 包管理器怎么用的？", "t-resume", tmp_path, context=context)

        # 恢复那条路径：先造一次图读出最后一条用户消息，再带着目录重造
        agent, _skills = await runner._build(
            "",
            "t-resume",
            tmp_path,
            specs=[],
            context=context,
            skill_ids=None,
            with_memory=False,
        )
        query = await runner._last_user_message(agent, "t-resume")
        assert "pnpm" in query

        block = await runner._memory_block(query, context)

    assert "<memory_catalog>" in block
    assert "pnpm" in block


# ---------------------------------------------------------------- 长期记忆


async def _make_collection(session, name: str):
    """建一个只走关键词检索的集合（不配 embedding，不联网）。"""
    from dataclasses import dataclass

    from app.rag.service import RagService

    @dataclass
    class _Payload:
        name: str
        description: str = ""
        scope: str = "private"
        embedding_model: str = ""
        provider_id: int | None = None
        chunk_size: int = 1000
        chunk_overlap: int = 150
        meta: dict = None  # type: ignore[assignment]

    return await RagService(session).create_collection(_Payload(name=name))


async def test_long_term_memory_round_trip(session):
    """长期记忆：写进去能搜到，能读全文，能标记取代。"""
    from app.memory.long_term_store import LongTermMemoryStore
    from app.memory.service import MemoryService

    await _make_collection(session, "mem-test")
    store = LongTermMemoryStore(session, "mem-test")
    service = MemoryService(session=session, thread_id=THREAD, long_term_store=store)

    entry = await service.remember(
        content="这个平台的鉴权暂时不做，用户主动推迟的",
        kind="decision",
        tags=["鉴权"],
        scope="long_term",
    )
    assert entry.id.startswith("l:")

    hits = await service.recall("鉴权", scope="long_term")
    assert hits
    assert "鉴权" in hits[0].content

    # 标记取代后不再参与召回，但还在（管理接口能看见）
    newer = await service.remember(
        content="鉴权决定放到下个迭代做",
        kind="decision",
        tags=["鉴权"],
        scope="long_term",
        supersedes=entry.id,
    )
    assert newer.id != entry.id
    after = await service.recall("鉴权", scope="long_term")
    assert [item.id for item in after] == [newer.id]
    assert len(await store.list_all()) == 2


async def test_long_term_memory_requires_configured_collection(session):
    """没配集合就明确报错，不静默失败。"""
    from app.memory.long_term_store import LongTermMemoryStore
    from app.memory.service import MemoryService

    store = LongTermMemoryStore(session, "")
    service = MemoryService(session=session, thread_id=THREAD, long_term_store=store)
    with pytest.raises(CapabilityError) as exc:
        await service.remember(
            content="这条没地方放",
            kind="note",
            scope="long_term",
        )
    assert "MEMORY_COLLECTION_NAME" in str(exc.value)

"""上下文模块的测试。

覆盖这几条最容易出问题的地方：

- **压缩阈值**：模型没有 profile 时不能崩（用 fraction 会直接抛 ValueError），
  阈值也不能是框架的固定 170k；
- **同名替换**：压缩中间件必须**替换**框架默认的那份，不能变成两份同时跑；
- **装箱**：预算不够时按优先级取舍，必装块不能静默丢；
- **装配**：记忆目录、运行时说明、策略说明都要进去，且顺序正确。
"""

from __future__ import annotations

import gc
from pathlib import Path

import pytest
from deepagents.backends.filesystem import FilesystemBackend
from langchain.chat_models import init_chat_model
from langchain_core.messages import HumanMessage

from app.agents.builder import build_agent
from app.agents.llm import _apply_context_window
from app.capabilities.execution.context import ExecutionContext
from app.context.assembler import assemble
from app.context.blocks import (
    OVERFLOW_DROP,
    OVERFLOW_TRUNCATE,
    ContextBlock,
)
from app.context.budget import (
    CONTEXT_BUDGET_RATIO,
    FALLBACK_CONTEXT_WINDOW,
    budget_for_window,
    conversation_budget_ratio,
    resolve_window,
)
from app.context.compaction import (
    MIDDLEWARE_NAME,
    PlatformSummarizationMiddleware,
    build_compaction_middleware,
)
from app.context.service import (
    KEY_BASE_PROMPT,
    KEY_MEMORY,
    KEY_POLICY,
    KEY_RUNTIME,
    ContextRequest,
    build_context_service,
)
from app.context.skills_quota import QuotaSkillsMiddleware

# ---------------------------------------------------------------- 预算


def test_budget_scales_with_window():
    """预算按窗口的比例算 —— 大窗口就该给更多。"""
    small = budget_for_window(32000)
    large = budget_for_window(1048576)
    assert large > small * 10


def test_unknown_window_falls_back_conservatively():
    """窗口未知用保守兜底，不猜。"""
    assert resolve_window(0) == FALLBACK_CONTEXT_WINDOW
    assert resolve_window(-1) == FALLBACK_CONTEXT_WINDOW
    assert resolve_window(123456) == 123456


# ---------------------------------------------------------------- 装箱


def test_blocks_ordered_by_priority():
    """装进去的块按优先级排 —— 重要的在前。"""
    blocks = [
        ContextBlock(key="memory", content="记忆", priority=40),
        ContextBlock(key="base", content="基准", priority=0),
        ContextBlock(key="runtime", content="运行环境", priority=10),
    ]
    result = assemble(blocks, budget_chars=10000)
    assert [b.key for b in result.blocks] == ["base", "runtime", "memory"]


def test_empty_blocks_are_skipped():
    """空块不占预算，也不该在提示词里留个空标题。"""
    result = assemble(
        [ContextBlock(key="memory", content="   ", priority=40)],
        budget_chars=1000,
    )
    assert result.blocks == []
    assert result.text == ""


def test_droppable_block_is_dropped_when_over_budget():
    """装不下时按 DROP 策略丢掉。"""
    blocks = [
        ContextBlock(key="base", content="x" * 50, priority=0),
        ContextBlock(key="memory", content="y" * 500, priority=40, overflow=OVERFLOW_DROP),
    ]
    result = assemble(blocks, budget_chars=100)
    assert "memory" not in [b.key for b in result.blocks]
    assert "memory" in [b.key for b in result.dropped]


def test_truncatable_block_keeps_head():
    """截断保留头部 —— 规矩通常写在开头。"""
    blocks = [
        ContextBlock(key="base", content="x" * 50, priority=0),
        ContextBlock(
            key="workspace",
            content="规矩在前。" + "填充" * 200,
            priority=30,
            overflow=OVERFLOW_TRUNCATE,
        ),
    ]
    result = assemble(blocks, budget_chars=120)
    kept = [b for b in result.blocks if b.key == "workspace"]
    assert kept, "可截断的块应该被截断而不是丢掉"
    assert kept[0].content.startswith("规矩在前。")
    assert "截断" in kept[0].content, "截断要留标记，否则模型以为看到的是全部"


def test_required_block_is_never_silently_dropped():
    """必装块装不下要警告，不能静默丢。"""
    blocks = [
        ContextBlock(key="base", content="x" * 500, priority=0, required=True),
    ]
    result = assemble(blocks, budget_chars=100)
    assert result.blocks, "必装块必须装进去"
    assert result.warnings, "装不下必装块要警告"


def test_required_truncatable_block_does_not_blow_the_budget():
    """必装但**可截断**的块（基准提示词）—— 截断，不撑破预算。

    超预算的后果是厂商拒掉整个请求；截断的后果只是模型少看一段提示词。
    """
    blocks = [
        ContextBlock(
            key="base",
            content="前。" + "x" * 500,
            priority=0,
            required=True,
            overflow=OVERFLOW_TRUNCATE,
        ),
    ]
    result = assemble(blocks, budget_chars=100)
    assert result.blocks, "必装块必须装进去"
    assert result.truncated, "可截断的必装块应该被截断"
    assert not result.over_budget, "装配器保证不超预算"
    assert result.used_chars <= result.budget_chars


def test_policy_block_dropped_raises_warning():
    """策略说明被挤掉要警告 —— 模型会因此反复触发审批。"""
    blocks = [
        ContextBlock(key="base", content="x" * 50, priority=0),
        ContextBlock(key="policy", content="p" * 500, priority=20, overflow=OVERFLOW_DROP),
    ]
    result = assemble(blocks, budget_chars=100)
    assert any("策略说明" in w for w in result.warnings)


def test_assembly_never_exceeds_budget():
    """装出来的东西不超预算。"""
    blocks = [
        ContextBlock(key="base", content="x" * 100, priority=0),
        ContextBlock(key="memory", content="y" * 100, priority=40),
    ]
    result = assemble(blocks, budget_chars=150)
    assert result.used_chars <= result.budget_chars


# ---------------------------------------------------------------- 装配


def test_service_collects_all_sources():
    """各来源的块都要收集齐。"""
    service = build_context_service()
    blocks = service.collect(
        ContextRequest(
            base_prompt="基准提示词",
            workspace=Path("/tmp/ws"),
            memory_catalog="<memory_catalog>…</memory_catalog>",
            policy_note="当前信任模式：balanced。",
        )
    )
    keys = [b.key for b in blocks]
    assert keys == [KEY_BASE_PROMPT, KEY_RUNTIME, KEY_POLICY, KEY_MEMORY]


def test_service_assembles_into_prompt():
    """装配结果里各块内容都在，且记忆目录保留自己的 XML 边界。"""
    service = build_context_service()
    result = service.assemble(
        ContextRequest(
            base_prompt="基准提示词",
            workspace=Path("/tmp/ws"),
            memory_catalog="<memory_catalog>\n- [s:1] 用 pnpm\n</memory_catalog>",
            policy_note="当前信任模式：balanced。",
        )
    )
    assert "基准提示词" in result.text
    assert "<memory_catalog>" in result.text
    assert "当前信任模式" in result.text
    assert "虚拟路径" in result.text


def test_memory_catalog_keeps_own_boundary():
    """记忆目录自带 XML 标签，不该再套一层标题。"""
    service = build_context_service()
    result = service.assemble(
        ContextRequest(base_prompt="基准", memory_catalog="<memory_catalog>x</memory_catalog>")
    )
    memory = [b for b in result.blocks if b.key == KEY_MEMORY][0]
    assert memory.title == ""
    assert result.text.count("<memory_catalog>") == 1


# ---------------------------------------------------------------- 压缩阈值


def test_profile_makes_threshold_fractional():
    """挂上 profile 后阈值按窗口比例走。

    触发线是**对话的份额**（窗口 − 系统提示词那份），不是另定的 0.85 ——
    两份预算互不知情的话，一次请求能到 1.35 倍窗口。
    """
    model = init_chat_model("openai:deepseek-flash", api_key="x")
    _apply_context_window(model, 1048576)
    middleware = build_compaction_middleware(model=model)
    assert middleware.trigger == ("fraction", conversation_budget_ratio())
    assert middleware.keep == ("fraction", 0.10)


def test_budget_and_compaction_share_one_notion():
    """系统提示词与对话的额度加起来正好一个窗口。"""
    assert CONTEXT_BUDGET_RATIO + conversation_budget_ratio(CONTEXT_BUDGET_RATIO) == 1.0
    # 对话的份额是**减出来的**，不是另定的
    assert conversation_budget_ratio(0.5) == 0.5
    assert conversation_budget_ratio(0.8) == pytest.approx(0.2)


@pytest.mark.asyncio
async def test_compaction_uses_two_thresholds():
    """对话与整个请求各用各的阈值。

    框架只有一个阈值，可它要同时跟"只算对话"和"整个请求"两种度量比 ——
    同一个阈值比两者，要么提前压缩，要么放任请求超窗。
    """
    model = init_chat_model("openai:deepseek-flash", api_key="x")
    _apply_context_window(model, 1000)
    middleware = build_compaction_middleware(model=model)
    messages = [HumanMessage(content="早")]
    # 对话占 400：低于对话的份额（500）—— 不压缩
    assert middleware._should_summarize(messages, 400) is False
    # 对话占 600：超过对话的份额 —— 压缩
    assert middleware._should_summarize(messages, 600) is True


def test_without_profile_does_not_crash():
    """窗口未知时不能崩。

    langchain 的摘要中间件用 fraction 而模型没有 profile 时会**直接抛
    ValueError** —— 那样整个 agent 都起不来。
    """
    model = init_chat_model("openai:deepseek-flash", api_key="x")
    middleware = build_compaction_middleware(model=model)
    assert middleware.trigger[0] == "tokens"
    assert middleware.keep[0] == "messages"
    assert middleware.keep[1] >= 20


def test_middleware_name_matches_framework():
    """名字必须与框架一致 —— deepAgents 按 name 做替换。"""
    middleware = build_compaction_middleware(
        model=init_chat_model("openai:deepseek-flash", api_key="x")
    )
    assert middleware.name == MIDDLEWARE_NAME
    assert isinstance(middleware, PlatformSummarizationMiddleware)


# ---------------------------------------------------------------- 接管


@pytest.mark.asyncio
async def test_compaction_replaces_default_not_duplicates(tmp_path):
    """接管 = 替换，不是叠加。

    叠加会变成两份摘要中间件同时跑，谁先谁后说不清。
    """
    workspace = tmp_path / "ws"
    workspace.mkdir()
    model = init_chat_model("openai:deepseek-flash", api_key="x")
    _apply_context_window(model, 1048576)

    # 先记下**建之前**已经存在的实例 —— 只数这次建出来的。
    # 直接数全局存活实例会被别的测试残留的 agent 干扰（谁先跑谁倒霉）。
    before = {
        id(obj) for obj in gc.get_objects() if isinstance(obj, PlatformSummarizationMiddleware)
    }

    agent = await build_agent(
        specs=[],
        context=ExecutionContext.build(workspace),
        model=model,
        system_prompt="你是编码助手。",
    )

    instances = [
        obj
        for obj in gc.get_objects()
        if isinstance(obj, PlatformSummarizationMiddleware) and id(obj) not in before
    ]
    assert len(instances) == 1, "应该只有一份压缩中间件"
    assert instances[0].trigger == ("fraction", conversation_budget_ratio())
    assert agent is not None


@pytest.mark.asyncio
async def test_skills_middleware_replaced_by_quota_version(tmp_path):
    """技能中间件被带配额的版本替换。"""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    skills_dir = tmp_path / "skills"
    (skills_dir / "s1").mkdir(parents=True)
    (skills_dir / "s1" / "SKILL.md").write_text(
        "---\nname: s1\ndescription: 技能一\n---\n\n正文\n", encoding="utf-8"
    )

    await build_agent(
        specs=[],
        context=ExecutionContext.build(workspace),
        model=init_chat_model("openai:deepseek-flash", api_key="x"),
        system_prompt="你是编码助手。",
        skills=["/skills/"],
        skills_dir=skills_dir,
    )

    quota = [obj for obj in gc.get_objects() if isinstance(obj, QuotaSkillsMiddleware)]
    plain = [
        obj
        for obj in gc.get_objects()
        if type(obj).__name__ == "SkillsMiddleware" and not isinstance(obj, QuotaSkillsMiddleware)
    ]
    assert len(quota) == 1
    assert not plain, "原版技能中间件应该被替换掉"


def test_skills_list_is_capped():
    """技能清单超配额时截断，并注明还剩多少。"""
    middleware = QuotaSkillsMiddleware(
        backend=FilesystemBackend(root_dir="/tmp"),
        sources=["/skills/"],
        max_skills=2,
    )
    skills = [
        {
            "name": f"s{i}",
            "description": f"技能{i}",
            "path": f"/skills/s{i}",
            "allowed_tools": [],
        }
        for i in range(5)
    ]
    rendered = middleware._format_skills_list(skills)
    assert "s0" in rendered
    assert "s2" not in rendered, "超出配额的不该列出来"
    assert "还有 3 个技能未列出" in rendered


def test_skills_list_not_capped_when_under_limit():
    """没超配额就全列。"""
    middleware = QuotaSkillsMiddleware(
        backend=FilesystemBackend(root_dir="/tmp"),
        sources=["/skills/"],
        max_skills=20,
    )
    skills = [
        {
            "name": f"s{i}",
            "description": f"技能{i}",
            "path": f"/skills/s{i}",
            "allowed_tools": [],
        }
        for i in range(3)
    ]
    rendered = middleware._format_skills_list(skills)
    assert "s2" in rendered
    assert "未列出" not in rendered


# ---------------------------------------------------------------- 产物目录


def test_artifacts_root_is_outside_workspace(tmp_path):
    """对话历史外置到工作区外面。

    deepAgents 把被压缩的历史写到 {artifacts_root}/conversation_history/。
    默认落在工作区里，用户在工作区就能看到自己的对话历史。
    """
    from app.capabilities.backends.assembly import ARTIFACTS_ROUTE

    workspace = tmp_path / "ws"
    workspace.mkdir()
    context = ExecutionContext.build(workspace)
    from app.capabilities.backends.assembly import build_backend

    backend = build_backend(context, None)
    assert backend.artifacts_root == ARTIFACTS_ROUTE
    assert ARTIFACTS_ROUTE != "/"

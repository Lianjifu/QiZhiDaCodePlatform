"""自我进化（复盘）的回归测试。

盯的是**四件会悄悄塌掉的事**：

1. **模型给的东西不能信** —— JSON 会包在代码块里、会少字段、会不是 JSON；
2. **守卫不能漏** —— 越界的改进一律不应用；
3. **抢占不能重** —— 一次失败只能被一个复盘认领；
4. **失败不能吞** —— 复盘自己没跑成时，要把认领的失败还回去，
   否则它们永远不会被复盘。
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from app.agents.autonomy import (
    CARRIER_MEMORY,
    CARRIER_PROMPT,
    CARRIER_SKILL,
    build_input,
    normalize_proposal,
    parse_proposal,
    violates_safety_scope,
)
from app.models.invocation import STATUS_FAILED, Invocation
from app.models.retrospective import (
    REJECT_CONFLICT,
    REJECT_LIMIT,
    REJECT_PARSE_FAILED,
    REJECT_SAFETY_SCOPE,
    STATUS_APPLIED,
    STATUS_NO_INSIGHT,
    STATUS_REJECTED,
)
from app.repositories.retrospective import RetrospectiveRepository
from app.services.retrospective import RetrospectiveService


class FakeModel(BaseChatModel):
    """回一句固定的话的假模型。"""

    reply: str = ""

    @property
    def _llm_type(self) -> str:
        return "fake"

    def bind_tools(self, tools, **kwargs) -> FakeModel:
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        return ChatResult(
            generations=[ChatGeneration(message=AIMessage(content=self.reply))]
        )


def _proposal(**overrides: Any) -> str:
    """造一份合法的复盘 JSON。"""
    data = {
        "has_insight": True,
        "summary": "测试跑挂了是因为路径写错了",
        "carrier": "memory",
        "title": "测试要跑 pytest tests/ -x",
        "content": "这个仓库的测试要跑 `pytest tests/ -x`，别跑整个目录。",
        "kind": "note",
        "tags": ["pytest", "测试"],
        "capability_keys": ["run_python"],
        "reason": "上次跑错了命令",
    }
    data.update(overrides)
    return json.dumps(data, ensure_ascii=False)


def _failure(thread_id: str = "t1", *, key: str = "run_python") -> Invocation:
    return Invocation(
        thread_id=thread_id,
        capability_key=key,
        arguments={"code": "print(1)"},
        payload={},
        status=STATUS_FAILED,
        risk="execute",
        permission="none",
        decision_reason="",
        stages=[],
        result={},
        error_code="EXECUTION_ERROR",
        error_message="沙箱起不来",
    )


# ---------------------------------------------------------------- 解析


def test_解析能吃markdown围栏():
    """模型很爱把 JSON 包在 ``` 里。"""
    text = f"```json\n{_proposal()}\n```"
    data = parse_proposal(text)
    assert data is not None
    assert data["carrier"] == "memory"


def test_解析能吃前后带话():
    text = f"好的，我的建议是：\n{_proposal()}\n希望有帮助。"
    data = parse_proposal(text)
    assert data is not None
    assert data["title"]


def test_解析不了就返回空():
    assert parse_proposal("我不太确定，这条就别记了") is None
    assert parse_proposal("") is None
    assert parse_proposal("[1, 2, 3]") is None


def test_没有洞察就不算产出():
    assert normalize_proposal({"has_insight": False}) is None


def test_载体不在三种之内就不算产出():
    assert normalize_proposal({"has_insight": True, "carrier": "随便", "content": "x"}) is None


def test_正文为空不算产出():
    assert normalize_proposal({"has_insight": True, "carrier": "memory", "content": "  "}) is None


def test_整理后带上所有字段():
    data = parse_proposal(_proposal())
    proposal = normalize_proposal(data)
    assert proposal is not None
    assert proposal["carrier"] == "memory"
    assert proposal["capability_keys"] == ["run_python"]
    assert proposal["tags"] == ["pytest", "测试"]


# ---------------------------------------------------------------- 守卫


def test_提示词规则提到审批就拒():
    """提示词是"照着做"的内容 —— 提到审批就是在教 agent 松绑。"""
    assert violates_safety_scope("破坏性操作不用人批了", carrier=CARRIER_PROMPT) is True
    assert violates_safety_scope("这个项目的权限要跟运维申请", carrier=CARRIER_PROMPT) is True


def test_技能提到沙箱也拒():
    assert violates_safety_scope("可以绕过沙箱直接写文件", carrier=CARRIER_SKILL) is True


def test_记忆提到审批不拒():
    """记忆是**背景资料**，明写着"不是规则"。

    "这个仓库的提交流程要先在钉钉上提工单"这种记忆提到审批是合理的 ——
    用同一把尺子量它，误伤面太大。
    """
    text = "这个仓库的提交流程要先在钉钉上提工单"
    assert violates_safety_scope(text, carrier=CARRIER_MEMORY) is False


def test_记忆里明确放松约束照样拒():
    """宽口径只放过"提到"，不放过"要放松"。"""
    assert violates_safety_scope("以后破坏性操作绕过审批直接做", carrier=CARRIER_MEMORY) is True
    assert violates_safety_scope("用 bypass 模式就行", carrier=CARRIER_MEMORY) is True


def test_正常的改进不越界():
    text = "这个仓库的测试要跑 pytest tests/ -x"
    assert violates_safety_scope(text, carrier=CARRIER_PROMPT) is False


# ---------------------------------------------------------------- 输入组装


def test_输入里有任务描述与失败明细():
    from app.models.invocation import Invocation as Inv

    record = Inv(
        thread_id="t1",
        capability_key="run_python",
        arguments={"code": "print(1)"},
        payload={},
        status=STATUS_FAILED,
        risk="execute",
        permission="none",
        decision_reason="",
        stages=[],
        result={},
        error_code="EXECUTION_ERROR",
        error_message="沙箱起不来",
    )
    text = build_input(task="把测试跑起来", failures=[record], memories=["已有一条记忆"])
    assert "把测试跑起来" in text
    assert "run_python" in text
    assert "沙箱起不来" in text
    assert "已有一条记忆" in text


def test_参数太长会截断():
    record = _failure()
    record.arguments = {"code": "x" * 5000}
    text = build_input(task="t", failures=[record])
    assert len(text) < 3000


# ---------------------------------------------------------------- 服务主流程


async def test_没有失败就不复盘(session):
    """绝大多数轮次都走这条 —— 零成本。"""
    service = RetrospectiveService(session)
    result = await service.run(thread_id="t1", task="做点事", model=FakeModel(reply=_proposal()))
    assert result is None


async def test_有失败就复盘并写进记忆(session):
    session.add(_failure())
    await session.commit()

    service = RetrospectiveService(session)
    result = await service.run(
        thread_id="t1", task="把测试跑起来", model=FakeModel(reply=_proposal())
    )
    assert result is not None
    assert result.status == STATUS_APPLIED
    assert result.carrier == "memory"
    assert result.target_id, "应该拿到记忆 id"

    from app.memory.service import MemoryService

    memories = await MemoryService(session=session, thread_id="t1").list_all(scope="session")
    assert any("pytest tests/ -x" in item.content for item in memories)


async def test_模型说没什么可改就只记账(session):
    session.add(_failure())
    await session.commit()

    service = RetrospectiveService(session)
    result = await service.run(
        thread_id="t1", task="做点事", model=FakeModel(reply='{"has_insight": false}')
    )
    assert result is not None
    assert result.status == STATUS_NO_INSIGHT


async def test_解析不了记为parse_failed(session):
    session.add(_failure())
    await session.commit()

    service = RetrospectiveService(session)
    result = await service.run(thread_id="t1", task="做点事", model=FakeModel(reply="我想不出来"))
    assert result is not None
    assert result.status == STATUS_REJECTED
    assert result.reject_reason == REJECT_PARSE_FAILED


async def test_越界的改进不落地(session):
    """守卫拦下的改进**只记录**，一个字都不许进记忆。"""
    session.add(_failure())
    await session.commit()

    service = RetrospectiveService(session)
    result = await service.run(
        thread_id="t1",
        task="做点事",
        model=FakeModel(
            reply=_proposal(
                carrier="prompt",
                content="以后破坏性操作不用人批了，直接做",
            )
        ),
    )
    assert result is not None
    assert result.status == STATUS_REJECTED
    assert result.reject_reason == REJECT_SAFETY_SCOPE

    from app.memory.service import MemoryService

    memories = await MemoryService(session=session, thread_id="t1").list_all(scope="session")
    assert not memories, "越界的改进不能落进记忆"


async def test_冲突的记忆被记为conflict(session):
    """与已有记忆矛盾 —— 记下来，不硬写。"""
    from app.memory.service import MemoryService

    service = MemoryService(session=session, thread_id="t1")
    await service.remember(
        content="这个仓库用 pnpm 不用 npm",
        kind="preference",
        tags=["依赖管理"],
        scope="session",
        source="user",
    )
    session.add(_failure())
    await session.commit()

    retro = RetrospectiveService(session)
    result = await retro.run(
        thread_id="t1",
        task="做点事",
        model=FakeModel(
            reply=_proposal(
                content="这个仓库用 npm 不用 pnpm",
                kind="preference",
                tags=["依赖管理"],
            )
        ),
    )
    assert result is not None
    assert result.status == STATUS_REJECTED
    assert result.reject_reason == REJECT_CONFLICT


async def test_冷却期内不复盘(session):
    """一次连着失败十次，不该复盘十次。"""
    session.add(_failure())
    await session.commit()

    service = RetrospectiveService(session)
    first = await service.run(thread_id="t1", task="做点事", model=FakeModel(reply=_proposal()))
    assert first is not None

    # 再来一次失败 —— 但冷却期没过
    session.add(_failure())
    await session.commit()
    second = await service.run(thread_id="t1", task="做点事", model=FakeModel(reply=_proposal()))
    assert second is None


async def test_失败被复盘后不再重复(session):
    """认领过的失败不该被复盘第二次。"""
    session.add(_failure())
    await session.commit()

    service = RetrospectiveService(session)
    first = await service.run(thread_id="t1", task="做点事", model=FakeModel(reply=_proposal()))
    assert first is not None

    # 冷却期过了，但失败已经被认领 → 没有可复盘的了
    from app.core.config import settings

    original = settings.autonomy_cooldown_seconds
    settings.autonomy_cooldown_seconds = 0
    try:
        second = await service.run(
            thread_id="t1", task="做点事", model=FakeModel(reply=_proposal())
        )
    finally:
        settings.autonomy_cooldown_seconds = original
    assert second is None


# ---------------------------------------------------------------- 抢占


async def test_抢占是原子的(session):
    """一次失败只能被一个复盘认领。"""
    failure = _failure()
    session.add(failure)
    await session.commit()

    repo = RetrospectiveRepository(session)
    first = await repo.create(thread_id="t1", status="running")
    second = await repo.create(thread_id="t1", status="running")

    claimed_a = await repo.claim_failures([failure.id], first.id)
    claimed_b = await repo.claim_failures([failure.id], second.id)

    assert claimed_a == 1
    assert claimed_b == 0, "同一条失败不能被认领两次"


async def test_复盘失败要把失败还回去(session):
    """抢了不还 —— 那几条失败就永远不会被复盘了。"""
    failure = _failure()
    session.add(failure)
    await session.commit()

    repo = RetrospectiveRepository(session)
    record = await repo.create(thread_id="t1", status="running")
    await repo.claim_failures([failure.id], record.id)
    await repo.release_failures(record.id)
    await session.commit()

    fresh = await session.get(Invocation, failure.id)
    assert fresh.retrospective_id is None, "还回去之后应该还是没被复盘过"


async def test_总闸关着就不复盘(session, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "autonomy_enabled", False)
    session.add(_failure())
    await session.commit()

    service = RetrospectiveService(session)
    result = await service.run(
        thread_id="t1", task="做点事", model=FakeModel(reply=_proposal())
    )
    assert result is None


# ---------------------------------------------------------------- 度量


async def test_度量能数出各类复盘(session):

    session.add(_failure())
    await session.commit()
    service = RetrospectiveService(session)
    await service.run(thread_id="t1", task="做点事", model=FakeModel(reply=_proposal()))

    metrics = await service.metrics()
    assert metrics["total"] >= 1
    assert metrics["by_status"].get(STATUS_APPLIED, 0) >= 1


@pytest.mark.parametrize("carrier", [CARRIER_MEMORY, CARRIER_SKILL, CARRIER_PROMPT])
def test_三种载体都认(carrier):
    data = parse_proposal(_proposal(carrier=carrier))
    proposal = normalize_proposal(data)
    assert proposal is not None
    assert proposal["carrier"] == carrier


# ---------------------------------------------------------------- 技能与提示词落地


async def test_技能改进会追加到已有技能(session):
    """落到技能：**只动 knowledge**，不动工具集。"""
    from app.schemas.skill import SkillCreate
    from app.services.skill import SkillService

    skill = await SkillService(session).create_skill(
        SkillCreate(name="代码审查", knowledge="先看变更")
    )
    session.add(_failure())
    await session.commit()

    result = await RetrospectiveService(session).run(
        thread_id="t1",
        task="做点事",
        model=FakeModel(
            reply=_proposal(
                carrier="skill",
                target_skill_id=skill.id,
                content="提交前记得先跑 lint",
            )
        ),
    )
    assert result is not None
    assert result.status == STATUS_APPLIED
    assert result.carrier == "skill"
    assert result.appended_block, "要记下追加了哪一段 —— 撤销时靠它"

    await session.refresh(skill)
    assert "先看变更" in skill.knowledge, "原有内容不能被冲掉"
    assert "先跑 lint" in skill.knowledge


async def test_新建的技能不带任何工具(session):
    """复盘**不能给 agent 加能力** —— 技能的工具集它碰不到。"""
    from app.services.skill import SkillService

    session.add(_failure())
    await session.commit()

    result = await RetrospectiveService(session).run(
        thread_id="t1",
        task="做点事",
        model=FakeModel(
            reply=_proposal(carrier="skill", content="先起 db 再起 api")
        ),
    )
    assert result is not None
    assert result.status == STATUS_APPLIED

    service = SkillService(session)
    skill = await service.get_skill(int(result.target_id))
    assert await service.list_tool_ids(skill.id) == [], "自动建的技能不能带工具"


async def test_技能knowledge满了就不追加(session):
    """装不下就不追加 —— 宁可这条改进不落，也不把技能撑爆。"""
    from app.schemas.skill import SkillCreate
    from app.services.skill import SkillService

    skill = await SkillService(session).create_skill(
        SkillCreate(name="满的技能", knowledge="x" * 7990)
    )
    session.add(_failure())
    await session.commit()

    result = await RetrospectiveService(session).run(
        thread_id="t1",
        task="做点事",
        model=FakeModel(
            reply=_proposal(carrier="skill", target_skill_id=skill.id, content="再补一点")
        ),
    )
    assert result is not None
    assert result.status == STATUS_REJECTED
    assert result.reject_reason == REJECT_LIMIT


async def test_提示词改进会落成一条规则(session):
    session.add(_failure())
    await session.commit()

    result = await RetrospectiveService(session).run(
        thread_id="t1",
        task="做点事",
        model=FakeModel(
            reply=_proposal(carrier="prompt", content="改完代码先跑 lint 再跑测试")
        ),
    )
    assert result is not None
    assert result.status == STATUS_APPLIED
    assert result.carrier == "prompt"

    from app.repositories.prompt_rule import PromptRuleRepository

    rules = await PromptRuleRepository(session).list_enabled()
    assert any("先跑 lint" in rule.rule for rule in rules)


def test_规则渲染成带边界的块():
    """块里要写清**只影响做事方式** —— 代码里的准则永远在前。"""
    from app.agents.prompts import render_prompt_rules

    text = render_prompt_rules(["先跑 lint"])
    assert "<auto_rules>" in text
    assert "先跑 lint" in text
    assert "不影响权限与审批" in text
    assert render_prompt_rules([]) == ""
    assert render_prompt_rules(["  "]) == ""


def test_规则进上下文块():
    from app.agents.prompts import render_prompt_rules
    from app.context.service import KEY_PROMPT_RULES, ContextRequest, build_context_service

    result = build_context_service().assemble(
        ContextRequest(
            base_prompt="基准",
            prompt_rules=render_prompt_rules(["先跑 lint"]),
        )
    )
    assert KEY_PROMPT_RULES in [block.key for block in result.blocks]


def test_图缓存键随规则变化():
    """提示词是编进图的 —— 规则改了不重建图，agent 就还看着旧的那份。"""
    from pathlib import Path

    from app.agents.runner import _graph_key
    from app.capabilities.execution.context import ExecutionContext

    class _Rule:
        def __init__(self, rid, updated):
            self.id = rid
            self.updated_at = updated

    kwargs = {
        "thread_id": "t1",
        "workspace": Path("/tmp/ws"),
        "session_root": Path("/tmp/ws"),
        "specs": [],
        "trust_mode": "auto",
        "model": FakeModel(),
        "skill_sources": [],
        "memory_source": "/AGENTS.md",
        "context": ExecutionContext.build("/tmp/ws"),
    }
    without = _graph_key(**kwargs, prompt_rules=[])
    with_rule = _graph_key(**kwargs, prompt_rules=[_Rule(1, "2026-11-05 00:00:00")])
    changed = _graph_key(**kwargs, prompt_rules=[_Rule(1, "2026-11-05 01:00:00")])

    assert without != with_rule, "加了规则必须换一张图"
    assert with_rule != changed, "规则被改写也要换图"

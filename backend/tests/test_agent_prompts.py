"""提示词与实际行为对得上。

准则里教模型用的东西，**必须是这一轮真有的**：

- 没绑 git 目录时平台把 ``git_*`` 与 ``delegate_worker`` 剔掉了 ——
  提示词不该再教模型用它们（模型会照着调，然后报错，白烧一轮）；
- 提示词不该写死"哪些操作要审批" —— 审批口径由策略说明按当前信任模式
  实时生成，写死就跟真实策略对不上了。
"""

from __future__ import annotations

from app.agents.prompts import (
    CODE_AGENT_PROMPT,
    base_prompt_for,
    has_collaboration,
)
from app.capabilities.registration.spec import CapabilitySpec


def _spec(key: str, *, enabled: bool = True) -> CapabilitySpec:
    return CapabilitySpec(key=key, kind="tool", enabled=enabled)


def test_没有协作能力就不教协作():
    """没绑 git 目录 —— 提示词里不该出现 delegate_worker。"""
    specs = [_spec("read_file"), _spec("execute")]
    assert has_collaboration(specs) is False
    prompt = base_prompt_for(specs)
    assert "delegate_worker" not in prompt
    assert prompt == CODE_AGENT_PROMPT


def test_有协作能力才加协作说明():
    """绑了 git 目录 —— 教模型怎么派活、怎么合。"""
    specs = [_spec("git_status"), _spec("delegate_worker")]
    assert has_collaboration(specs) is True
    prompt = base_prompt_for(specs)
    assert "delegate_worker" in prompt
    assert "git_merge" in prompt


def test_关掉的协作能力不算数():
    """能力被停用了 —— 也不该教。"""
    specs = [_spec("delegate_worker", enabled=False)]
    assert has_collaboration(specs) is False
    assert "delegate_worker" not in base_prompt_for(specs)


def test_提示词不写死审批口径():
    """审批口径归策略说明 —— 提示词里只说"要说清楚影响"。"""
    prompt = base_prompt_for([_spec("execute")])
    assert "先说明影响再执行" not in prompt
    assert "策略说明" in prompt

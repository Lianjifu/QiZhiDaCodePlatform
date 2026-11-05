"""复盘服务：把执行结果变成改进。

## 一次复盘走过的路

```text
这一轮有失败？
  ├─ 没有 → 不跑（绝大多数轮次都走这条，零成本）
  └─ 有 ──→ 冷却期过了？
              ├─ 没有 → 不跑
              └─ 有 ──→ 抢占失败记录（CAS）
                         ├─ 没抢到 → 别人在复盘，空跑
                         └─ 抢到 ──→ 调模型 → 解析 → 守卫 → 落地 → 记账
```

## 三条硬约定

1. **先抢后干活** —— 抢占是"认领"，不是"标记"。两个复盘同时看到同一批失败时，
   只有一个能抢到（``UPDATE ... WHERE retrospective_id IS NULL`` 的行数说了算）。
   抢到之后如果复盘自己没跑成，要把失败**还回去** —— 否则它们永远不会被复盘。

2. **守卫先行** —— 落哪一层都要过范围守卫。提示词与技能用严口径（提到审批就拒），
   记忆用宽口径（只在明确放松约束时才拒）。

3. **全程留痕** —— 改了什么、为什么改、成功没有，都落 ``retrospective`` 表。
   免审批的前提就是**事后查得到、撤得回**。
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.autonomy import (
    MAX_FAILURES,
    RETROSPECTIVE_PROMPT,
    build_input,
    normalize_proposal,
    parse_proposal,
    violates_safety_scope,
)
from app.capabilities.errors import CapabilityError, ErrorCode
from app.core.config import settings
from app.core.locking import TaskLock
from app.memory.models import SCOPE_LONG_TERM, SCOPE_SESSION, SOURCE_MODEL
from app.memory.service import MemoryService
from app.models.retrospective import (
    CARRIER_MEMORY,
    CARRIER_PROMPT,
    CARRIER_SKILL,
    REJECT_CONFLICT,
    REJECT_LIMIT,
    REJECT_PARSE_FAILED,
    REJECT_SAFETY_SCOPE,
    REJECT_TARGET_MISSING,
    STATUS_APPLIED,
    STATUS_FAILED,
    STATUS_NO_INSIGHT,
    STATUS_REJECTED,
    STATUS_REVERTED,
    STATUS_RUNNING,
    Retrospective,
)
from app.repositories.invocation import InvocationRepository
from app.repositories.prompt_rule import PromptRuleRepository
from app.repositories.retrospective import RetrospectiveRepository

logger = logging.getLogger(__name__)

# 技能 knowledge 的长度上限 —— 追加前要看着点，别把已有内容挤掉
SKILL_KNOWLEDGE_LIMIT = 8000

# 追加进技能 knowledge 的那段带标记 —— 撤销时按它删回去
SKILL_BLOCK_TEMPLATE = "\n\n【复盘补充】{content}\n"


class RetrospectiveService:
    """复盘的业务逻辑。"""

    def __init__(self, session: AsyncSession, *, lock: TaskLock | None = None) -> None:
        """构造。

        Args:
            session: 数据库会话。
            lock: 数据库锁（记忆写入要与别的工具串行）。
        """
        self.session = session
        self.lock = lock or TaskLock()
        self.repo = RetrospectiveRepository(session)
        self.rules = PromptRuleRepository(session)
        self._invocations = InvocationRepository(session)

    # ---------- 主流程 ----------

    async def run(
        self,
        *,
        thread_id: str,
        task: str,
        model: Any,
    ) -> Retrospective | None:
        """跑一次复盘。

        Args:
            thread_id: 会话 ID。
            task: 这次想做什么（用户消息）。
            model: 复盘用的模型对象。

        Returns:
            复盘记录；没跑（没失败 / 冷却期没过 / 被别人抢了）返回 ``None``。
        """
        if not settings.autonomy_enabled:
            return None
        if not thread_id:
            return None
        if await self._in_cooldown(thread_id):
            return None

        failures = await self._invocations.list_uncovered_failures(
            thread_id,
            since=datetime.now(UTC) - timedelta(seconds=settings.autonomy_lookback_seconds),
            limit=MAX_FAILURES,
        )
        if not failures:
            return None

        record = await self.repo.create(
            thread_id=thread_id,
            task_summary=(task or "")[:500],
            status=STATUS_RUNNING,
            carrier="",
            title="",
            content="",
            kind="",
            tags=[],
            target_id="",
            reason="",
            capability_keys=[],
            invocation_count=0,
        )
        # **先抢后干活** —— 抢到的行数说了算
        candidates = [failure.id for failure in failures]
        claimed = await self.repo.claim_failures(candidates, record.id)
        if claimed == 0:
            # 别人抢到了 —— 把自己这行撤掉，空跑
            await self.repo.delete(record)
            await self.session.commit()
            return None
        # 抢到的具体是哪几条 —— 条件更新只给行数，得再查一次
        mine = await self.repo.list_claimed(record.id, candidates)
        record.invocation_count = len(mine)
        await self.session.commit()

        try:
            await self._produce(record, task=task, failures=mine, model=model)
        except Exception as exc:  # pragma: no cover - 复盘失败不该拖垮任何东西
            logger.warning("复盘失败 thread=%s: %s", thread_id, exc, exc_info=True)
            # **把认领还回去** —— 抢了不还，这批失败就永远不会被复盘了。
            #
            # 还回去之后，下一轮的复盘会重新捡起它们（还没被复盘过）——
            # 所以复盘失败是**自愈**的，不必重试机制。
            await self.repo.set_status(record.id, STATUS_FAILED, reject_reason=None)
            await self.repo.release_failures(record.id)
            await self.session.commit()
            return record

        await self.session.commit()
        return record

    async def _produce(
        self,
        record: Retrospective,
        *,
        task: str,
        failures: list[Any],
        model: Any,
    ) -> None:
        """调模型 → 解析 → 守卫 → 落地 → 记账。"""
        memories = await self._related_memories(task)
        prompt = build_input(task=task, failures=failures, memories=memories)

        response = await model.ainvoke(
            [
                SystemMessage(content=RETROSPECTIVE_PROMPT),
                HumanMessage(content=prompt),
            ]
        )
        raw = _response_text(response)
        data = parse_proposal(raw)
        if data is None:
            await self.repo.set_status(
                record.id, STATUS_REJECTED, reject_reason=REJECT_PARSE_FAILED
            )
            return
        proposal = normalize_proposal(data)
        if proposal is None:
            # 模型说"没什么可改的" —— 这也是有用的信息，记一笔
            await self.repo.set_status(record.id, STATUS_NO_INSIGHT)
            return

        carrier = proposal["carrier"]
        content = proposal["content"]

        # **守卫先行** —— 越界的改进只记录，不落地
        if violates_safety_scope(content, carrier=carrier):
            logger.warning(
                "复盘建议越界，未应用：thread=%s carrier=%s %s",
                record.thread_id,
                carrier,
                proposal["summary"][:80],
            )
            await self.repo.set_status(
                record.id,
                STATUS_REJECTED,
                reject_reason=REJECT_SAFETY_SCOPE,
                carrier=carrier,
                title=proposal["title"],
                content=content,
                kind=proposal["kind"],
                tags=proposal["tags"],
                reason=proposal["reason"],
                capability_keys=proposal["capability_keys"],
            )
            return

        if carrier == CARRIER_MEMORY:
            applied = await self._apply_memory(record, proposal)
        elif carrier == CARRIER_SKILL:
            applied = await self._apply_skill(record, proposal)
        else:
            applied = await self._apply_prompt(record, proposal)

        if applied is None:
            return
        target_id, appended_block, target_skill_id = applied
        await self.repo.set_status(
            record.id,
            STATUS_APPLIED,
            reject_reason=None,
            carrier=carrier,
            title=proposal["title"],
            content=content,
            kind=proposal["kind"],
            tags=proposal["tags"],
            target_id=target_id,
            target_skill_id=target_skill_id,
            appended_block=appended_block,
            reason=proposal["reason"],
            capability_keys=proposal["capability_keys"],
            applied_at=datetime.now(UTC),
        )

    # ---------- 落地 ----------

    async def _apply_memory(
        self,
        record: Retrospective,
        proposal: dict[str, Any],
    ) -> tuple[str, str, int | None] | None:
        """改进落到记忆里。

        写的是**长期**记忆（跨会话）—— 一次任务的经验下次也要能用。

        **长期集合没建好就降级成会话内**（不是丢掉）：一条改进因为这个丢了才亏。
        """
        service = await self._memory_service(record.thread_id or "")
        scopes = [SCOPE_SESSION]
        if service.long_term_store is not None:
            scopes.insert(0, SCOPE_LONG_TERM)

        last_error: Exception | None = None
        for scope in scopes:
            try:
                async with self.lock:
                    entry = await service.remember(
                        content=proposal["content"],
                        kind=proposal["kind"],
                        tags=proposal["tags"],
                        scope=scope,
                        source=SOURCE_MODEL,
                    )
                return entry.id, "", None
            except CapabilityError as exc:
                if exc.code == ErrorCode.INVALID_ARGUMENTS:
                    # **冲突**（与已有记忆矛盾）—— 记下来，不硬写。
                    # 不用再试下一个 scope：会话内的旧记忆一样会跟它撞。
                    logger.info("复盘的记忆与已有记忆矛盾：%s", exc)
                    await self._reject(
                        record,
                        proposal,
                        REJECT_CONFLICT,
                        carrier=CARRIER_MEMORY,
                    )
                    return None
                # 其余（集合不存在之类的）—— 换一个 scope 再试
                last_error = exc
            except Exception as exc:  # pragma: no cover - 兜底
                logger.warning("复盘写记忆失败: %s", exc)
                await self.repo.set_status(
                    record.id,
                    STATUS_FAILED,
                    reject_reason=None,
                    carrier=CARRIER_MEMORY,
                    title=proposal["title"],
                    content=proposal["content"],
                    reason=proposal["reason"],
                    capability_keys=proposal["capability_keys"],
                )
                return None

        logger.info("复盘的记忆没地方写：%s", last_error)
        await self._reject(
            record,
            proposal,
            REJECT_TARGET_MISSING,
            carrier=CARRIER_MEMORY,
        )
        return None

    async def _reject(
        self,
        record: Retrospective,
        proposal: dict[str, Any],
        reason: str,
        *,
        carrier: str,
    ) -> None:
        """记一条"没落地"的复盘。"""
        await self.repo.set_status(
            record.id,
            STATUS_REJECTED,
            reject_reason=reason,
            carrier=carrier,
            title=proposal["title"],
            content=proposal["content"],
            reason=proposal["reason"],
            capability_keys=proposal["capability_keys"],
        )

    async def _apply_skill(
        self,
        record: Retrospective,
        proposal: dict[str, Any],
    ) -> tuple[str, str, int | None] | None:
        """改进落到技能里。

        **只动 ``knowledge``，不碰 ``tool_ids``** —— 技能是"一组工具 + 怎么用"，
        复盘能改的只有后半截。要是它能塞工具，就是自己给自己扩权。
        """
        from app.schemas.skill import SkillCreate, SkillUpdate
        from app.services.skill import SkillService

        service = SkillService(self.session)
        target_id = proposal.get("target_skill_id")
        block = SKILL_BLOCK_TEMPLATE.format(content=proposal["content"])

        if target_id:
            skill = await service.repo.get(target_id)
            if skill is None:
                await self.repo.set_status(
                    record.id,
                    STATUS_REJECTED,
                    reject_reason=REJECT_TARGET_MISSING,
                    carrier=CARRIER_SKILL,
                    title=proposal["title"],
                    content=proposal["content"],
                    reason=proposal["reason"],
                    capability_keys=proposal["capability_keys"],
                )
                return None
            if len(skill.knowledge or "") + len(block) > SKILL_KNOWLEDGE_LIMIT:
                # 装不下就不追加 —— 宁可这条改进不落，
                # 也不要把技能撑爆或把已有内容截掉
                await self.repo.set_status(
                    record.id,
                    STATUS_REJECTED,
                    reject_reason=REJECT_LIMIT,
                    carrier=CARRIER_SKILL,
                    title=proposal["title"],
                    content=proposal["content"],
                    reason=proposal["reason"],
                    capability_keys=proposal["capability_keys"],
                )
                return None
            await service.update_skill(
                skill.id,
                SkillUpdate(knowledge=(skill.knowledge or "") + block),
            )
            return str(skill.id), block, skill.id

        # 没有指定技能 —— 新建一个
        # **tool_ids 留空**：复盘不给 agent 加能力
        name = await _unique_skill_name(self.session, proposal["title"])
        knowledge = block.strip()
        try:
            skill = await service.create_skill(
                SkillCreate(
                    name=name,
                    description=proposal["summary"][:200] or proposal["title"][:60],
                    knowledge=knowledge,
                    tags=proposal["tags"],
                )
            )
        except Exception as exc:
            logger.info("复盘的技能没建起来：%s", exc)
            await self.repo.set_status(
                record.id,
                STATUS_REJECTED,
                reject_reason=REJECT_TARGET_MISSING,
                carrier=CARRIER_SKILL,
                title=proposal["title"],
                content=proposal["content"],
                reason=proposal["reason"],
                capability_keys=proposal["capability_keys"],
            )
            return None
        # 记下**实际写进去的那段**（新建时是去掉首尾空白的版本）——
        # 撤销时按它删回去
        return str(skill.id), knowledge, skill.id

    async def _apply_prompt(
        self,
        record: Retrospective,
        proposal: dict[str, Any],
    ) -> tuple[str, str, int | None] | None:
        """改进落到提示词规则表里。

        规则**不是**直接进代码里的提示词 —— 它进的是上下文装配的一个独立块
        （见 ``app/context/service.py``），代码里的准则永远在前、永远完整。
        """
        rule = await self.rules.create(
            rule=proposal["content"],
            enabled=True,
            retrospective_id=record.id,
        )
        return str(rule.id), "", None

    # ---------- 辅助 ----------

    async def _in_cooldown(self, thread_id: str) -> bool:
        """这个会话是不是刚复盘过。

        **正在跑的也算** —— 那说明另一个复盘任务正在处理这一批失败，
        这次不用再跑一遍。
        """
        last = await self.repo.last_for_thread(thread_id)
        if last is None:
            return False
        if last.status == STATUS_RUNNING:
            return True
        created = last.created_at
        if created is None:
            return False
        if created.tzinfo is None:
            created = created.replace(tzinfo=UTC)
        return datetime.now(UTC) - created < timedelta(
            seconds=settings.autonomy_cooldown_seconds
        )

    async def _related_memories(self, task: str) -> list[str]:
        """已有的相关记忆（给复盘看，避免重复记）。

        召回失败不该让复盘挂掉 —— 空着就行。
        """
        if not task.strip():
            return []
        try:
            service = await self._memory_service("")
            catalog = await service.recall_catalog(task)
        except Exception as exc:
            logger.debug("复盘召回记忆失败: %s", exc)
            return []
        # **目录条目里的 ``summary`` 是字段，不是方法** —— 那是
        # ``CatalogEntry``（目录行），不是 ``MemoryEntry``（记忆本体）。
        return [str(entry.summary or "")[:160] for entry in catalog.entries]

    async def _memory_service(self, thread_id: str) -> MemoryService:
        """按执行上下文造一个记忆服务（长期记忆要连 RAG 集合）。

        **集合不在就不给长期存储** —— 那样写长期会抛错，而写长期失败会被
        当成"记忆冲突"一路降级，白白多走一遍。
        """
        long_term = None
        if self.session is not None:
            from app.memory.long_term_store import long_term_store_for

            long_term = await long_term_store_for(self.session, lock=self.lock)
        return MemoryService(
            session=self.session,
            thread_id=thread_id,
            long_term_store=long_term,
            db_lock=self.lock,
        )

    # ---------- 撤销 ----------

    async def revert(self, record_id: int) -> tuple[bool, str]:
        """撤销一次复盘做的改动。

        **免审批的兜底** —— 平台不让人先看一眼再改，那事后就得能撤。
        三层各撤各的：

        - **记忆** —— 删掉那一条；
        - **技能** —— 把追加的那段从 knowledge 里删回去；
        - **提示词** —— 把那条规则停用（关掉就不进提示词）。

        Args:
            record_id: 复盘记录 id。

        Returns:
            ``(撤成功没有, 说明)``。
        """
        record = await self.repo.get(record_id)
        if record is None:
            return False, "复盘记录不存在"
        if record.status != STATUS_APPLIED:
            return False, f"这条复盘没有落地（{record.status}），没什么可撤"

        if record.carrier == CARRIER_MEMORY:
            ok = await self._revert_memory(record)
        elif record.carrier == CARRIER_SKILL:
            ok = await self._revert_skill(record)
        elif record.carrier == CARRIER_PROMPT:
            ok = await self._revert_prompt(record)
        else:
            return False, f"不知道这种载体怎么撤（{record.carrier}）"

        if ok:
            await self.repo.set_status(
                record.id,
                STATUS_REVERTED,
                reject_reason=None,
                applied_at=None,
            )
            await self.session.commit()
            return True, "已撤销"
        return False, "撤销失败：目标已经不在了"

    async def _revert_memory(self, record: Retrospective) -> bool:
        """删掉那条记忆。"""
        from app.memory.service import MemoryService

        if not record.target_id:
            return False
        service = MemoryService(session=self.session, thread_id=record.thread_id or "")
        async with self.lock:
            return await service.forget_any(record.target_id)

    async def _revert_skill(self, record: Retrospective) -> bool:
        """把追加的那段从 knowledge 里删回去。"""
        from app.schemas.skill import SkillUpdate
        from app.services.skill import SkillService

        if not record.target_skill_id or not record.appended_block:
            return False
        service = SkillService(self.session)
        skill = await service.repo.get(record.target_skill_id)
        if skill is None:
            return False
        knowledge = skill.knowledge or ""
        if record.appended_block not in knowledge:
            # 别人后来改过这一段 —— 硬删会伤到别人的内容，不撤
            return False
        await service.update_skill(
            skill.id,
            SkillUpdate(knowledge=knowledge.replace(record.appended_block, "").strip()),
        )
        return True

    async def _revert_prompt(self, record: Retrospective) -> bool:
        """停用那条规则。"""
        if not record.target_id:
            return False
        try:
            rule_id = int(record.target_id)
        except ValueError:
            return False
        rule = await self.rules.get(rule_id)
        if rule is None:
            return False
        await self.rules.set_enabled(rule_id, False)
        return True

    # ---------- 度量 ----------

    async def metrics(self, *, thread_id: str | None = None) -> dict[str, Any]:
        """复盘这条链路的度量。

        三层：

        1. **跑了多少** —— 按状态统计（复盘次数 / 应用数 / 被拒数）；
        2. **被谁拦下** —— 按拒绝原因统计（多少条被守卫拦了）；
        3. **有没有用** —— 同类失败的复发率：某次复盘涉及的能力，
           在它**之前**失败过几次、**之后**又失败过几次。
           后者比前者少，才说明改进起作用了。
        """
        by_status = await self.repo.count_by_status()
        by_reason = await self.repo.count_by_reject_reason()

        repo = self._invocations
        applied = await self.repo.list_page(
            thread_id=thread_id, status=STATUS_APPLIED, limit=200
        )
        recurrence: list[dict[str, Any]] = []
        for record in applied:
            keys = list(record.capability_keys or [])
            if not keys or not record.thread_id:
                continue
            before = await repo.count_failures_for_capabilities(
                record.thread_id, keys, until=record.created_at
            )
            after = await repo.count_failures_for_capabilities(
                record.thread_id, keys, since=record.created_at
            )
            recurrence.append(
                {
                    "retrospective_id": record.id,
                    "thread_id": record.thread_id,
                    "capability_keys": keys,
                    "failures_before": before,
                    "failures_after": after,
                    # 复发率：改进之后又失败的比例（0 = 没再犯，1 = 照旧）
                    "recurrence_rate": round(after / before, 3) if before else None,
                }
            )
        return {
            "by_status": by_status,
            "by_reject_reason": by_reason,
            "total": sum(by_status.values()),
            "recurrence": recurrence,
        }


def _response_text(response: Any) -> str:
    """模型响应 → 纯文本。

    不同厂商给的内容形状不一样（字符串 / 内容块列表），都归一成字符串。
    """
    content = getattr(response, "content", response)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
            elif isinstance(block, str):
                parts.append(block)
        return "\n".join(parts)
    return str(content or "")


async def _unique_skill_name(session: AsyncSession, title: str) -> str:
    """给新技能起个不重名的名字。

    技能名全局唯一（``skill.name`` 有 unique 约束），复盘起的名字大概率
    与已有的撞 —— 撞了就加后缀。
    """
    from app.repositories.skill import SkillRepository

    repo = SkillRepository(session)
    base = (title or "").strip()[:50] or "复盘补充"
    name = base
    suffix = 2
    while await repo.get_by_name(name) is not None:
        name = f"{base}（{suffix}）"
        suffix += 1
        if suffix > 50:  # pragma: no cover - 名字撞到离谱时的兜底
            name = f"{base}-{suffix}"
            break
    return name


async def run_retrospective_in_background(
    thread_id: str,
    task: str,
    model: Any,
) -> None:
    """后台跑一次复盘（自带数据库会话）。

    **为什么自己开会话**：触发它的那个 HTTP 请求的 session 随后就关了 ——
    复盘是异步的，等它跑完，请求早结束了。

    这个函数**从不抛异常** —— 它跑在后台任务里，异常会变成"任务异常结束"
    的日志，用户那边什么也看不到，还以为复盘跑了。
    """
    try:
        from app.db.session import SessionLocal

        async with SessionLocal() as session:
            service = RetrospectiveService(session)
            await service.run(thread_id=thread_id, task=task, model=model)
    except Exception as exc:  # pragma: no cover - 后台任务不该把异常抛出去
        logger.warning("复盘任务失败 thread=%s: %s", thread_id, exc)


__all__ = [
    "SKILL_BLOCK_TEMPLATE",
    "SKILL_KNOWLEDGE_LIMIT",
    "RetrospectiveService",
    "run_retrospective_in_background",
]

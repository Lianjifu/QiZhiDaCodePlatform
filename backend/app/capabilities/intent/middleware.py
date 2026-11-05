"""治理中间件：模型的一次 tool call → 受治理的一次执行。

deepAgents 接管文件工具之后，治理落点从"能力实现"上移到**中间件层**：
deepAgents 的所有工具调用（它自己的文件工具、我们的工具）都会经过
``awrap_tool_call``，所以在这里能看见每一次调用的名字和参数 ——
风险判定、审批挂起、留痕都在这一层做。

三件事：

1. **模型调用前**：把工具收敛到本次允许的范围，并把"哪些工具会要人批"告诉模型；
2. **工具调用前**：参数级风险规则命中（命令含 ``rm -rf``、URL 指内网）时，
   用 LangGraph 的 ``interrupt()`` 挂起等人批；
3. **工具调用后**：写调用记录、更新任务置信度。

审批有两条路径，都落在 LangGraph 的中断机制上：

- **声明级**（执行命令、写文件、联网…）：deepAgents 的 ``interrupt_on``，
  在工具执行**之前**抛中断；
- **参数级**（``rm -rf``、SSRF）：本模块的 ``interrupt()``，在工具执行**中**抛中断。

**一次调用只让人批一次**：声明级先于工具执行发生，工具能跑到参数级检查就说明
人已经批过了 —— 参数级不再重复问，只管"声明级放行过的那类调用"（平时不用人批
的工具碰到破坏性命令时单独拎出来问一次）。

两处中断的载荷形状不同，但恢复值统一成 deepAgents HITL 的格式
（``{"decisions": [{"type": "approve"}]}``），审批接口只需要认一种。
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware, ModelRequest
from langchain_core.messages import SystemMessage, ToolMessage
from langgraph.types import interrupt

from app.agents.tasks import REGISTRY, describe_call
from app.capabilities.errors import CapabilityError
from app.capabilities.execution.context import ExecutionContext
from app.capabilities.execution.redaction import redact
from app.capabilities.execution.risk import RiskAssessment, assess_call
from app.capabilities.execution.task_state import adjust_confidence, load_task_state
from app.capabilities.registration.policy import (
    PermissionMode,
    RiskLevel,
    normalize_mode,
)
from app.capabilities.registration.spec import KIND_TOOL, CapabilitySpec
from app.capabilities.registration.tool_policy import (
    FS_TOOL_NAMES,
    READ_ONLY_TOOL_NAMES,
    approval_required_tools,
    policy_for_tool,
)
from app.files.diffstat import line_diff

logger = logging.getLogger(__name__)

# 恢复值的类型：HITL 与我们自己的中断统一用这一套
APPROVE = "approve"
REJECT = "reject"

# 会改文件的工具 —— 它们的改动量要统计出来（前端工具块上显示 ``+N −M``）
DIFF_TOOLS: frozenset[str] = frozenset({"write_file", "edit_file"})


class CapabilityMiddleware(AgentMiddleware):
    """能力治理中间件。"""

    def __init__(
        self,
        specs: list[CapabilitySpec],
        allowed_tool_keys: set[str] | None = None,
        context: ExecutionContext | None = None,
        thread_id: str = "",
        trust_mode: str = "auto",
        interrupt_enabled: bool = True,
    ) -> None:
        """构造中间件。

        Args:
            specs: 统一能力清单（我们的工具用它；文件工具走映射表）。
            allowed_tool_keys: 本次允许的工具集；``None`` 表示不限制。
            context: 执行上下文（工作区、数据库会话）。
            thread_id: 会话 ID。
            trust_mode: 会话信任模式。
            interrupt_enabled: 是否允许挂起等人批。

                **关掉它的地方只有一处**：子 agent（worker）。worker 跑在
                自己的 worktree 里、命令在断网容器中，没有前端交互界面 ——
                挂起就永远等不到人批。改为不挂起，代价是 worker 里的破坏性
                命令也无人拦，防线只剩"物理隔离"这一层。
        """
        self.specs = specs
        self.allowed_tool_keys = allowed_tool_keys
        self.context = context
        self.thread_id = thread_id
        self.trust_mode = trust_mode
        self.permission_mode = normalize_mode(trust_mode)
        self.interrupt_enabled = interrupt_enabled

    # ---------- 模型调用前 ----------

    async def awrap_model_call(self, request: ModelRequest, handler):
        """收敛工具集 + 追加策略说明。

        Args:
            request: LangChain 的模型请求。
            handler: 继续往下走的处理器。

        Returns:
            模型请求的响应。
        """
        overrides: dict[str, Any] = {}
        allowed = self.allowed_tool_keys
        if self.permission_mode == PermissionMode.PLAN:
            # 只读模式：写类与执行类工具**不暴露给模型** —— 不是"问一句再放行"，
            # 是模型根本看不到它们，只能读与规划。
            readonly = READ_ONLY_TOOL_NAMES | {
                spec.key for spec in self.specs if spec.risk == RiskLevel.READ
            }
            allowed = readonly if allowed is None else (allowed & readonly)
        if allowed is not None:
            kept = [
                tool
                for tool in request.tools
                if getattr(tool, "name", None) in allowed
            ]
            if kept:
                # 一个都没匹配上就别收窄，保留原样，避免把 agent 关死
                overrides["tools"] = kept

        note = self._policy_note()
        base = request.system_message.text if request.system_message else ""
        # **别重复** —— 策略说明已经在上下文装配（app/context/）里进过一次
        # 提示词了。这里再追加一遍，模型会看到同一段话两遍，白费 token
        # 还让人以为有两种规则。
        #
        # 内容不一样时才追加（allowed_tool_keys 变了的话，两边算出来的
        # 说明会不同 —— 那时以这里这份为准，它知道本次允许的工具集）。
        if note and note not in base:
            overrides["system_message"] = SystemMessage(content=f"{base}\n\n{note}".strip())

        if not overrides:
            return await handler(request)
        return await handler(request.override(**overrides))

    # ---------- 工具调用 ----------

    async def awrap_tool_call(self, request, handler):
        """工具调用：参数级风险挂起 → 执行 → 留痕 → 更新置信度。

        Args:
            request: LangChain 的工具调用请求。
            handler: 真正执行工具的处理器。

        Returns:
            工具结果消息。

        Raises:
            CapabilityError: 参数级风险规则命中且人工拒绝时（返回错误消息，不抛）。
        """
        tool_call = request.tool_call or {}
        name = str(tool_call.get("name") or "")
        args = dict(tool_call.get("args") or {})

        # 1) 参数级风险：命中就挂起等人批
        #
        # **判定只算一次** —— 这次结果既用来决定要不要挂起，也用来写调用记录
        # （记录里的风险档位要是参数级上调后的，不是工具的基线档位）。
        assessment = assess_call(name, args, self.specs)
        denied = await self._check_param_risk(name, args, assessment)
        if denied is not None:
            return denied

        # 2) 执行
        # 改文件的工具：执行前后各读一次文件，中间差多少行（前端显示 +N −M）
        watches_file = name in DIFF_TOOLS
        before = await self._read_target(args) if watches_file else None

        # 登记"正在跑" —— 状态面板的「后台任务」段要它
        token = REGISTRY.start(self.thread_id, name, describe_call(name, args))
        started = time.perf_counter()
        try:
            message = await handler(request)
        except CapabilityError as exc:
            message = ToolMessage(
                content=exc.to_text(),
                tool_call_id=str(tool_call.get("id") or ""),
                status="error",
            )
        finally:
            REGISTRY.finish(self.thread_id, token)
        duration_ms = int((time.perf_counter() - started) * 1000)

        if watches_file and before is not None:
            after = await self._read_target(args)
            if after is not None:
                stat = line_diff(before, after)
                if stat.changed:
                    message.additional_kwargs = {
                        **(getattr(message, "additional_kwargs", None) or {}),
                        "diff": {"added": stat.added, "removed": stat.removed},
                    }

        # 3) 留痕 + 置信度（只对文件工具；我们自己的工具由执行层写记录）
        blocked = False
        if name in FS_TOOL_NAMES:
            blocked = await self._record(name, args, message, duration_ms, assessment)
            if blocked:
                # 任务越过阻塞线 → 明确告诉模型停下来，别再重试
                message = _with_blocked_notice(message)

        return message

    async def _check_param_risk(
        self,
        name: str,
        args: dict[str, Any],
        assessment: RiskAssessment | None = None,
    ) -> ToolMessage | None:
        """参数级风险规则：命中就挂起等人批。

        挂起用 LangGraph 的 ``interrupt()``：它在工具执行过程中抛中断，
        恢复时把决定值交回这里。批准就继续执行，拒绝就返回一条错误消息给模型。

        **声明级批过，不等于这一次调用就安全了。** 声明级看的是**工具**
        （"execute 这个工具要人批"），参数级看的是**这次调用的参数**
        （"这条命令里有 rm -rf"）。两者不是一回事 —— 一条 ``rm -rf`` 走的
        同样是 ``execute`` 工具，声明级只会告诉人"执行命令"，
        不会告诉人"这条命令在删库"。

        所以声明级那张审批卡片上显示的风险档位，是**参数级上调后的**
        （"破坏性"而不是"执行命令"）—— 由 ``runner._pending_entries``
        算出来。人不清楚自己批的是删库还是跑命令，卡片上的字就得说清楚。

        **绕过权限模式下不挂起**：那个模式的承诺是"不会有人工审批"，
        参数级再挂一次就是违背承诺。但判定仍然要跑 —— 结果会落到调用记录里，
        事后查得到"这次绕过了什么"。

        Args:
            name: 工具名。
            args: 工具参数。
            assessment: 已经算好的风险评估。**没给就现算** ——
                调用方算过了就别算第二遍（规则要跑正则，不便宜）。

        Returns:
            人工拒绝时返回一条错误消息；批准或没命中时返回 ``None``（继续执行）。
        """
        if assessment is None:
            assessment = assess_call(name, args, self.specs)

        if not self.interrupt_enabled:
            # worker：没有前端界面，挂起就永远等不到人批。
            #
            # **但判定已经跑过了**（上面算的），结果照样落到调用记录里 ——
            # "不让人批"不等于"不记账"。事后要能查"worker 干过什么、
            # 有没有命中风险规则"，靠的就是这条记录。
            if assessment.needs_review:
                logger.warning(
                    "worker 放行了命中风险规则的调用（无人工审批）：tool=%s risk=%s reasons=%s",
                    name,
                    assessment.risk,
                    assessment.reasons,
                )
            return None

        if name in self._approval_tool_names():
            # 声明级已经就**这个工具**问过人了 —— 再问一次就是同一次调用让人批两遍。
            #
            # 但声明级批的是工具，不是这次调用的参数：一条 ``rm -rf`` 走的同样是
            # ``execute`` 工具。所以参数级规则仍然要跑 —— 跑出来的结果由
            # ``runner._pending_entries`` 落到审批卡片上，人看到的是
            # **参数级上调后的档位**（"破坏性"而不是"执行命令"），
            # 而不是只看到工具的基线档位。
            return None

        if not assessment.needs_review:
            # 没有威胁信号 —— 只是风险分类被调整了（比如"会向固定出口发数据"
            # 记成 write）。那不是危险，不该再挂一次让人批。
            return None

        if self.permission_mode == PermissionMode.BYPASS:
            # 绕过权限：用户明确说了不要人工审批。这里不挂起 ——
            # 挂了就违背承诺。但判定跑过了，结果记进调用记录。
            logger.warning(
                "绕过权限模式放行了命中风险规则的调用：tool=%s risk=%s reasons=%s",
                name,
                assessment.risk,
                assessment.reasons,
            )
            return None

        payload = {
            "kind": "param_risk",
            "tool": name,
            "arguments": redact(args),
            # 风险等级存字符串：中断载荷要进 checkpoint 序列化，枚举类型不被认
            "risk": str(assessment.risk),
            "reasons": assessment.reasons,
            "message": (
                f"能力 {name} 的参数命中风险规则（{'；'.join(assessment.reasons)}），"
                f"风险等级 {assessment.risk}，需要人工批准"
            ),
        }
        decision = interrupt(payload)
        if _is_approved(decision):
            return None
        return ToolMessage(
            content=(
                f"[RISK_BLOCKED] {payload['message']}；人工拒绝，未执行。"
                "请调整参数后再试（不要重复同样的调用）。"
            ),
            tool_call_id="",
            status="error",
        )

    # ---------- 留痕 ----------

    async def _read_target(self, args: dict[str, Any]) -> str | None:
        """读这次调用要改的文件当前内容。

        Args:
            args: 工具参数（取 ``file_path``）。

        Returns:
            文件内容；**文件不存在**返回空串（新建文件 = 从空变满）；
            **读不了**（越界、不是文本、路径非法）返回 ``None`` ——
            统计不了改动量不该影响执行。
        """
        if self.context is None:
            return None
        raw = str(args.get("file_path") or "")
        if not raw:
            return None
        root = Path(self.context.workspace).resolve()
        # 软链接可能指向工作区外面 —— resolve 之后再判边界
        target = (root / raw.lstrip("/")).resolve()
        if target != root and root not in target.parents:
            return None
        try:
            if not target.exists():
                return ""
            return target.read_text(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            return None

    async def _record(
        self,
        name: str,
        args: dict[str, Any],
        message: Any,
        duration_ms: int,
        assessment: RiskAssessment | None = None,
    ) -> bool:
        """给文件工具写一条调用记录，并更新任务置信度。

        我们的工具（web_* / run_python / MCP）由执行层写记录 —— 那条路径有
        完整的阶段结论；文件工具不走执行层，所以在这里补一条简化的。

        Args:
            name: 工具名。
            args: 工具参数。
            message: 工具返回的消息。
            duration_ms: 耗时（毫秒）。
            assessment: 参数级风险评估。给了就用它算风险档位 ——
                **记录里要的是参数级上调后的档位**（"破坏性"而不是
                "执行命令"），不然事后翻记录会以为那条 ``rm -rf``
                只是普通命令。

        Returns:
            任务是否已经越过阻塞线。
        """
        if self.context is None or self.context.session is None:
            return False

        from app.models.invocation import (
            STATUS_BLOCKED,
            STATUS_FAILED,
            STATUS_SUCCEEDED,
            Invocation,
        )

        failed = getattr(message, "status", "") == "error"
        text = str(getattr(message, "content", "") or "")
        error_code = "EXECUTION_ERROR" if failed else None

        policy = policy_for_tool(name)
        # **风险档位取参数级上调后的那个** —— 评估算出来的风险只会上调，
        # 不会下调（`risk.max_risk`），所以直接用评估结果就是最严格的。
        #
        # 为什么重要：一条 ``rm -rf`` 走的也是 ``execute`` 工具。只记工具的
        # 基线档位（"执行命令"），事后翻记录会以为那只是普通命令 ——
        # 而它其实已经被参数级规则判定成破坏性了。
        risk = str(assessment.risk) if assessment is not None else policy.risk
        async with self.context.db():
            state = await load_task_state(self.context.session, self.thread_id, name)
            confidence = adjust_confidence(state.confidence, error_code)
            blocked = confidence < self._block_threshold()

            # 改动量也存进记录 —— 会话级「本次改了多少行」从这些记录汇总
            diff = (getattr(message, "additional_kwargs", None) or {}).get("diff")
            result: dict[str, Any] = {"content": text[:2000]}
            if isinstance(diff, dict):
                result["diff"] = diff

            record = Invocation(
                thread_id=self.thread_id or None,
                capability_key=name,
                arguments=redact(args),
                payload=dict(args),
                status=(
                    STATUS_BLOCKED
                    if blocked
                    else (STATUS_FAILED if failed else STATUS_SUCCEEDED)
                ),
                risk=risk,
                permission=policy.permission,
                decision_reason="文件工具经由 deepAgents 后端执行",
                result=result,
                error_code=error_code,
                error_message=text[:500] if failed else None,
                duration_ms=duration_ms,
                confidence=confidence,
                started_at=datetime.now(UTC),
                decided_at=datetime.now(UTC),
                finished_at=datetime.now(UTC),
            )
            self.context.session.add(record)
            await self.context.session.flush()
            # **每条记录自己提交** —— 用户点「停止」时这一轮会被取消，
            # 整轮末尾的提交不会执行。不在这里提交的话，这一轮的调用记录
            # （含改动量）就跟着回滚了：文件已经改了，记录却查不到。
            await self.context.session.commit()
        return blocked

    def _block_threshold(self) -> float:
        """置信度阻塞阈值。"""
        from app.core.config import settings

        return settings.capability_confidence_block_threshold

    # ---------- 策略说明 ----------

    def _policy_note(self) -> str:
        """把本次的策略上下文告诉模型。

        特别要说清**哪些工具会触发人工审批** —— 模型提前知道了，就知道这类
        调用要把参数一次做对；不知道的话，它会反复触发审批、反复中断任务。
        """
        return build_policy_note(self.specs, self.trust_mode, self.allowed_tool_keys)

    def _approval_tool_names(self) -> set[str]:
        """本次哪些工具会要人批。

        用的是**唯一那份规则**（``tool_policy.approval_required_tools``）——
        和 deepAgents 的 ``interrupt_on``、执行层的判定完全一致。
        """
        from app.capabilities.registration.tool_policy import approval_required_tools

        return approval_required_tools(self.specs, self.trust_mode, self.allowed_tool_keys)

    # ---------- 内部 ----------


# 权限模式 → 中文名（给模型看的那句话）
_MODE_LABELS: dict[str, str] = {
    PermissionMode.AUTO: "自动",
    PermissionMode.MANUAL: "手动",
    PermissionMode.ACCEPT_EDITS: "接受编辑",
    PermissionMode.PLAN: "只读",
    PermissionMode.BYPASS: "绕过权限",
}


def build_policy_note(
    specs: list[CapabilitySpec],
    trust_mode: str,
    allowed_tool_keys: set[str] | None = None,
) -> str:
    """生成策略说明（告诉模型本次的审批规则与可用工具数）。

    抽成模块级函数是为了让**上下文模块**也能生成同一份说明 ——
    它要进上下文预算，就得能独立拿到内容，不能只依赖中间件往提示词上追加。

    Args:
        specs: 统一能力清单。
        trust_mode: 会话信任模式。
        allowed_tool_keys: 本次允许的工具集；``None`` 表示不限制。

    Returns:
        策略说明正文。没有可说的内容时返回空串。
    """
    mode = normalize_mode(trust_mode)
    notes = [f"当前权限模式：{_MODE_LABELS.get(mode, mode)}。"]
    if mode == PermissionMode.AUTO:
        notes.append(
            "审批按风险档位：只读、写入是低档，直接执行；执行命令、联网是中档，"
            "需要人工审批；破坏性操作是高档，永远需要人工审批。"
        )
    elif mode == PermissionMode.MANUAL:
        notes.append("手动模式：只读直接执行，其余任何操作都要人工审批。")
    elif mode == PermissionMode.ACCEPT_EDITS:
        notes.append("接受编辑模式：文件编辑与命令都直接执行，只有破坏性操作仍要人工审批。")
    elif mode == PermissionMode.PLAN:
        notes.append("只读模式：写文件与执行命令的工具**没有提供给你**，只做阅读与规划。")
    elif mode == PermissionMode.BYPASS:
        notes.append("绕过权限模式：所有操作都会直接执行，不会有人工审批。")

    approval = approval_required_tools(specs, trust_mode, allowed_tool_keys)
    if approval:
        notes.append(
            "以下工具需要人工批准才能执行："
            + "、".join(sorted(approval))
            + "。每次触发审批都会中断当前任务等人工处理，调用前请把参数一次做对。"
        )
        notes.append(
            "另外：平时不用人批的工具，碰到命令里含删除/写设备等特征、"
            "或抓取的是内网地址，也会命中参数级风险规则，单独问一次。"
        )
    count = _exposed_count(specs, allowed_tool_keys)
    if count:
        notes.append(f"本次可用工具 {count} 个。")
    return "\n".join(notes)


def _exposed_count(specs: list[CapabilitySpec], allowed_tool_keys: set[str] | None) -> int:
    """本次暴露给模型的工具数量。

    跟"哪些工具会要人批"用同一套过滤条件（tool + enabled + 在允许集内），
    免得提示词里两个数字口径不一致。
    """
    count = 0
    for spec in specs:
        if spec.kind != KIND_TOOL or not spec.enabled:
            continue
        if allowed_tool_keys is not None and spec.key not in allowed_tool_keys:
            continue
        count += 1
    return count + len(FS_TOOL_NAMES)


def _with_blocked_notice(message: Any) -> Any:
    """给工具消息补一句"任务已阻塞，请停下来"。

    置信度低于阈值说明模型在反复做错事 —— 它自己不会停，得我们告诉它。
    """
    notice = "\n\n[任务已阻塞] 任务置信度已低于阈值，请停止重试并告知用户需要人工介入。"
    content = getattr(message, "content", "")
    if isinstance(content, str):
        message.content = content + notice
    return message


def _is_approved(decision: Any) -> bool:
    """恢复值里是不是"批准"。

    两种中断都用同一套恢复格式（langchain HITL 的形状）：
    ``{"decisions": [{"type": "approve"}]}`` 批准，
    ``{"decisions": [{"type": "reject"}]}`` 拒绝。
    也兼容直接给一个列表的老写法。
    """
    if decision is None:
        return False
    if isinstance(decision, dict):
        items = decision.get("decisions")
        if items is None:
            items = [decision]
    elif isinstance(decision, list):
        items = decision
    else:
        items = [decision]
    for item in items:
        if isinstance(item, dict):
            if item.get("type") in (APPROVE, "accept"):
                return True
            if item.get("type") in (REJECT, "deny"):
                return False
    return False


__all__ = ["APPROVE", "REJECT", "CapabilityMiddleware", "build_policy_note"]

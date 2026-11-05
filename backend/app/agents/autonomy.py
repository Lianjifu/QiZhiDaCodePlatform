"""自主进化：把执行结果变成改进。

## 这一层放什么

复盘这条链路上，**纯逻辑**都在这儿 —— 组输入、解析模型给的答案、范围守卫。
凡是碰数据库的都 ``app/services/retrospective.py``（那是业务层）。

```text
失败明细 + 任务描述 + 已有记忆
        │
        ▼  build_input()          组给模型看的东西
   ┌────────────────┐
   │  复盘模型      │  用**犯错的同一个模型**
   └────────────────┘
        │  一段 JSON
        ▼  parse_proposal()       容错解析
   改进建议 {carrier, content, ...}
        │
        ▼  violates_safety_scope() 范围守卫
   放行 / 拒绝
```

## 范围守卫为什么必须有

复盘是**免审批**的 —— 平台不让人先看一眼再改。所以得有一道机械的闸，
拦住"改自己的安全规则"这类改进：

- 代码里的 ``CODE_AGENT_PROMPT`` 永远只由人改；
- 权限模式、审批档位、沙箱配置由 ``policy.py`` 与权限模式管，不由经验决定；
- 自生成的规则只允许影响**做事方式**。

所以命中审批 / 权限那一层关键词的规则**一律拒绝应用**，只记录下来给人看。

**这是宁可误伤的粗判** —— 漏判的代价（agent 改掉自己的安全规则）远大于
误判的代价（一条无害的规则没自动上，人手动加即可）。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from app.models.retrospective import CARRIER_MEMORY, CARRIER_PROMPT, CARRIER_SKILL

logger = logging.getLogger(__name__)

_CARRIERS = (CARRIER_MEMORY, CARRIER_SKILL, CARRIER_PROMPT)

# 范围守卫：命中任一关键词的规则**不应用**
#
# 这些词覆盖的是「权限与审批层」—— 那一层由代码与权限模式决定，
# 不是经验能改的。规则要是改了它，agent 就能给自己松绑。
SAFETY_SCOPE_KEYWORDS: tuple[str, ...] = (
    "审批",
    "批准",
    "权限",
    "授权",
    "绕过",
    "bypass",
    "沙箱",
    "鉴权",
    "免确认",
    "破坏性",
)

# **放松约束**的说法 —— 任何载体都拦。
#
# 上面那组是"谈到权限层就拒"（宁可误伤），用在**提示词与技能**上：
# 那是两段"照着做"的内容，提到审批就是在教 agent 松绑。
#
# 记忆层不用那组 —— 记忆是**背景资料**，明确写着"不是规则"，
# 而"这个仓库的提交流程要先在钉钉上提工单"这种记忆提到审批是合理的。
# 记忆层只拦**明确在放松约束**的说法。
LOOSENING_PATTERNS: tuple[str, ...] = (
    "绕过",
    "bypass",
    "免确认",
    "不用人批",
    "不需要人批",
    "无需批准",
    "不需要批准",
    "不经过审批",
    "跳过审批",
    "直接执行不需",
    "不要询问用户",
)

# 复盘最多看几条失败
MAX_FAILURES = 10

# 单条失败在输入里最多占多少字（参数可能很长）
MAX_ARGS_CHARS = 200

# 任务描述最多截多少字
MAX_TASK_CHARS = 500


RETROSPECTIVE_PROMPT = """\
你在做一次**复盘**：看 agent 刚才这次任务失败在哪，提炼出一条下次能用的改进。

## 你看到的东西

1. **这次想做什么** —— 用户的需求。
2. **失败明细** —— 每次没成功的调用：调了什么能力、参数是什么、报了什么错。
3. **已有的相关记忆** —— 别重复记已经记过的。

## 要回答的三个问题

1. **错在哪** —— 根因是什么？
   不是「命令失败了」，是「这条命令的参数写错了，因为路径带空格没加引号」。
2. **下次怎么改** —— 一条**具体**的做法，别人照着能做。
3. **改在哪一层** —— 这条改进属于哪一层：
   - `memory`：一条事实或经验（"这个仓库的测试要跑 `pytest tests/ -x`"）
   - `skill`：一组工具怎么用的知识（"要跑多个服务时先起 db 再起 api，
     否则 api 连不上"）
   - `prompt`：一条行为准则（"改完代码先跑 lint 再跑测试"）

## 判断标准

- **只记能复用的** —— "刚才那个路径打错了"没用，"这个仓库的脚本都在
  `scripts/` 下"才有用。
- **只记能推出来的** —— 你看到的是失败，不是原因。原因要从失败里推出来，
  推不出来就别记。
- **宁可少提** —— 没有值得改的就说 `has_insight: false`。
  一次没价值的改进会污染 agent 的记忆与提示词。
- **`content` 要自包含** —— 脱离这次对话也能读懂，带上将来能检索到的关键词。

## 输出

只输出一个 JSON 对象，不要有别的文字：

```json
{
  "has_insight": true,
  "summary": "这次失败的原因是……",
  "carrier": "memory | skill | prompt",
  "title": "一句话标题",
  "content": "可自包含的正文",
  "kind": "note | convention | preference | fact",
  "tags": ["检索标签"],
  "target_skill_id": null,
  "capability_keys": ["这次失败涉及的能力名"],
  "reason": "为什么这样改"
}
```

`has_insight` 为 false 时，`carrier` 之后的字段都可以留空。
"""


def build_input(
    *,
    task: str,
    failures: list[Any],
    memories: list[str] | None = None,
) -> str:
    """把复盘要看的三样东西组在一起。

    Args:
        task: 这次想做什么（用户消息）。
        failures: 失败的调用记录（``Invocation`` 行）。
        memories: 已有相关记忆的正文（避免重复记）。

    Returns:
        给模型看的一段文本。
    """
    lines = ["## 这次想做什么", "", (task or "").strip()[:MAX_TASK_CHARS] or "（用户没说要做什么）"]

    lines += ["", "## 失败明细", ""]
    if not failures:
        lines.append("（没有失败）")
    for record in failures:
        lines.append(_describe_failure(record))

    lines += ["", "## 已有的相关记忆", ""]
    if not memories:
        lines.append("（没有）")
    else:
        lines.extend(f"- {text}" for text in memories)

    return "\n".join(lines)


def _describe_failure(record: Any) -> str:
    """一次失败 → 一行描述。"""
    name = str(getattr(record, "capability_key", "") or "?")
    status = str(getattr(record, "status", "") or "")
    parts = [f"- **{name}**（{status}）"]

    args = getattr(record, "arguments", None)
    if isinstance(args, dict) and args:
        # 参数可能很长（整段代码），截断
        rendered = json.dumps(args, ensure_ascii=False)
        if len(rendered) > MAX_ARGS_CHARS:
            rendered = rendered[:MAX_ARGS_CHARS] + "…"
        parts.append(f"参数 `{rendered}`")

    error_code = str(getattr(record, "error_code", "") or "")
    if error_code:
        parts.append(f"错误码 {error_code}")
    error_message = str(getattr(record, "error_message", "") or "")
    if error_message:
        parts.append(f"报错：{error_message.strip()[:300]}")

    exit_code = _exit_code_of(record)
    if exit_code is not None:
        parts.append(f"退出码 {exit_code}")

    return "　".join(parts)


def _exit_code_of(record: Any) -> int | None:
    """从记录的结果里取退出码。

    工具跑成了但**要做的事没做成**（退出码非零）—— 那也是失败，
    而且是代码 agent 最常见的那种。
    """
    result = getattr(record, "result", None)
    if not isinstance(result, dict):
        return None
    value = result.get("exit_code")
    return value if isinstance(value, int) else None


def parse_proposal(text: str) -> dict[str, Any] | None:
    """解析模型给的 JSON。

    **容错是必需的** —— 模型会把 JSON 包在 ``` 代码块里、前面带一句
    "好的，我的建议是："、或者干脆少给一个字段。解析不出来就当这次复盘
    没有产出（调用方记 ``parse_failed``），不能因此让整轮对话挂掉。

    Returns:
        解析出来的字典；解析不出来返回 ``None``。
    """
    raw = (text or "").strip()
    if not raw:
        return None

    # 剥 markdown 代码块围栏
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1] if "\n" in raw else ""
        if raw.rstrip().endswith("```"):
            raw = raw.rstrip()[:-3]
        raw = raw.strip()

    candidates = [raw]
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        candidates.append(raw[start : end + 1])

    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(data, dict):
            return data
    logger.warning("复盘结果解析不出 JSON: %s", raw[:200])
    return None


def violates_safety_scope(text: str, *, carrier: str = CARRIER_PROMPT) -> bool:
    """这条改进是不是越界了（在放松安全约束）。

    分两种口径：

    - **提示词 / 技能** —— 命中 ``SAFETY_SCOPE_KEYWORDS`` 就拒。
      这两段是"照着做"的内容，提到审批 / 权限就是在教 agent 松绑。
    - **记忆** —— 只拦 ``LOOSENING_PATTERNS``。
      记忆是**背景资料**（目录里明写着"不是规则"），而
      "这个仓库的提交流程要先在钉钉上提工单"这种记忆提到审批是合理的 ——
      用同一把尺子量它，误伤面太大。

    Args:
        text: 改进的正文（记忆正文 / 技能知识 / 提示词规则）。
        carrier: 落在哪一层。

    Returns:
        越界返回 ``True`` —— 调用方应当拒绝应用，只记录。
    """
    lowered = (text or "").lower()
    if any(pattern.lower() in lowered for pattern in LOOSENING_PATTERNS):
        return True
    if carrier == CARRIER_MEMORY:
        return False
    return any(keyword.lower() in lowered for keyword in SAFETY_SCOPE_KEYWORDS)


def normalize_proposal(data: dict[str, Any]) -> dict[str, Any] | None:
    """把模型给的原始字典整理成平台认的形状。

    **模型给什么不重要，平台认什么才重要** —— 载体不在三种之内、
    正文是空的，都算没有有效产出。

    Returns:
        整理好的提案；无效返回 ``None``。
    """
    if not data.get("has_insight"):
        return None
    carrier = str(data.get("carrier") or "").strip()
    if carrier not in _CARRIERS:
        return None
    content = str(data.get("content") or "").strip()
    if not content:
        return None
    title = str(data.get("title") or "").strip() or content[:60]
    capability_keys = [
        str(key).strip() for key in (data.get("capability_keys") or []) if str(key).strip()
    ]
    target_skill_id = data.get("target_skill_id")
    try:
        target = int(target_skill_id) if target_skill_id is not None else None
    except (TypeError, ValueError):
        target = None
    return {
        "summary": str(data.get("summary") or "").strip(),
        "carrier": carrier,
        "title": title,
        "content": content,
        "kind": str(data.get("kind") or "note").strip() or "note",
        "tags": [str(tag).strip() for tag in (data.get("tags") or []) if str(tag).strip()],
        "target_skill_id": target,
        "capability_keys": capability_keys,
        "reason": str(data.get("reason") or "").strip(),
    }


__all__ = [
    "CARRIER_MEMORY",
    "CARRIER_PROMPT",
    "CARRIER_SKILL",
    "LOOSENING_PATTERNS",
    "MAX_FAILURES",
    "RETROSPECTIVE_PROMPT",
    "SAFETY_SCOPE_KEYWORDS",
    "build_input",
    "normalize_proposal",
    "parse_proposal",
    "violates_safety_scope",
]

"""每轮都会变的那两块提示词 —— 在模型调用时才注进去。

系统提示词里两块内容**每轮都不一样**：

- **记忆目录** —— 按本轮这句话召回的记忆；
- **上传件清单** —— 本轮用户传了哪些文件。

它们被编进图里，图就不能跨轮复用 —— 复用等于让 agent 一直看着第一轮的
旧记忆，越聊越不搭。

所以这两块在编译期只留**占位符**，等真的要调模型时再填。填什么放在
:class:`PromptHolder` 里，由中间件在 ``wrap_model_call`` 里替换占位符 ——
位置不变（还是原来那一块在原来那个位置），只是内容换成了这一轮的。

## 为什么是替换占位符，不是追加到末尾

上下文模块是**按优先级装箱**的：上传件（25）在策略说明（20）后面、
记忆目录（40）在最后。追加到末尾会把顺序打乱，模型先读到的内容就不对了。
占位符让装配器照常排位，中间件只换内容。

代价：装配器算预算时看到的是占位符的长度（几十个字符），不是真实目录的
长度。记忆模块自己管召回条数与预算，所以这块的溢出风险由它兜着。
"""

from __future__ import annotations

from typing import Any

from langchain.agents.middleware.types import AgentMiddleware, ModelRequest
from langchain_core.messages import SystemMessage

# 提示词里的占位符 —— 内容在模型调用时才替换
PLACEHOLDER_MEMORY = "<!--qzd:memory-catalog-->"
PLACEHOLDER_UPLOADS = "<!--qzd:uploads-->"


class PromptHolder:
    """当前这一轮要注入的内容。

    **可变是故意的** —— 图复用时，holder 也跟着复用，每轮把内容换掉。
    """

    __slots__ = ("memory_catalog", "uploads_note")

    def __init__(self) -> None:
        self.memory_catalog = ""
        self.uploads_note = ""

    def update(self, *, memory_catalog: str = "", uploads_note: str = "") -> None:
        """换成本轮的内容。"""
        self.memory_catalog = memory_catalog
        self.uploads_note = uploads_note


class DynamicContextMiddleware(AgentMiddleware):
    """把每轮变化的内容填进系统提示词。"""

    def __init__(self, holder: PromptHolder) -> None:
        """构造中间件。

        Args:
            holder: 装着本轮内容的 holder。
        """
        self.holder = holder

    async def awrap_model_call(self, request: ModelRequest, handler):
        """替换占位符，再往下走。"""
        message = request.system_message
        replaced = _replace_placeholders(message, self.holder)
        if replaced is not None:
            request = request.override(system_message=replaced)
        return await handler(request)


def _replace_placeholders(
    message: SystemMessage | None, holder: PromptHolder
) -> SystemMessage | None:
    """把占位符换成 holder 里的内容。

    找不到占位符就返回 None（不改提示词）—— 图不是用我们的方式编的时候
    （比如测试里直接塞了句提示词），也别把它弄坏。
    """
    if message is None:
        return None
    replacements = {
        PLACEHOLDER_MEMORY: holder.memory_catalog,
        PLACEHOLDER_UPLOADS: holder.uploads_note,
    }
    blocks: list[dict[str, Any]] = []
    changed = False
    for block in message.content_blocks:
        if block.get("type") != "text":
            blocks.append(block)
            continue
        text = str(block.get("text", ""))
        new_text = text
        for token, value in replacements.items():
            if token in new_text:
                new_text = new_text.replace(token, value)
        if new_text != text:
            changed = True
            # **空块直接丢掉** —— 记忆目录与上传件清单为空时（这一轮没有
            # 记忆、没传附件），留着空块就是提示词里几行空行。
            if not new_text.strip():
                continue
            blocks.append({**block, "text": new_text})
        else:
            blocks.append(block)
    if not changed:
        return None
    return SystemMessage(content_blocks=blocks)


__all__ = [
    "PLACEHOLDER_MEMORY",
    "PLACEHOLDER_UPLOADS",
    "DynamicContextMiddleware",
    "PromptHolder",
]

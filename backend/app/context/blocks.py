"""上下文块：一次上下文里的一个片段。

## 为什么要有这个抽象

现在提示词是几段字符串拼起来的：基准提示词 + 运行环境 + 策略说明 + 记忆目录。
每段各自只知道自己的存在 —— 谁长谁就挤掉别人。记忆目录有 1200 字符预算，
但那是**它自己**的预算；基准提示词、运行时说明全都不限量。

所以改成**块**：每块自带标题、优先级与溢出策略，装配器按配额装箱。

## 块为什么要自带标题

模型分不清「这是我要遵守的规矩」还是「这是背景资料」。记忆目录是背景，
工作区说明是规矩 —— 混在一个标题下，模型会把背景当指令执行。

## 溢出策略为什么要分三种

装不下时的处理**不是**一刀切：

- ``DROP`` —— 整块丢掉。记忆目录丢了下一轮还在召回，无所谓；
- ``TRUNCATE`` —— 截断到剩余预算。工作区说明不能丢（agent 会违反项目规矩），
  但截断可以 —— 规矩通常写在开头；
- ``COMPACT`` —— 换成摘要。给对话历史这类「内容有先后、砍掉中间就断片」的
  场景用；裁掉中间的推理过程，剩下的前后两截接不上，模型会得出错误结论。
"""

from __future__ import annotations

from dataclasses import dataclass, field

# 溢出策略
OVERFLOW_DROP = "drop"
OVERFLOW_TRUNCATE = "truncate"
OVERFLOW_COMPACT = "compact"

OVERFLOW_POLICIES = (OVERFLOW_DROP, OVERFLOW_TRUNCATE, OVERFLOW_COMPACT)


@dataclass
class ContextBlock:
    """上下文里的一个块。

    字段：
    - ``key`` —— 稳定标识（"memory" / "workspace" / "runtime" / "policy"），
      用于排查与测试断言；
    - ``title`` —— 独立成块的标题。空串表示不单独成块（直接并入提示词）；
    - ``content`` —— 正文；
    - ``priority`` —— 数字越小越先装。**必填** —— 没有优先级就没法定装谁；
    - ``overflow`` —— 装不下时怎么办（见模块说明）；
    - ``unit`` —— 计量单位："chars" 或 "tokens"。默认按字符算 ——
      近似估算 token 要引入分词器，而块之间的相对大小关系用字符就够了；
    - ``required`` —— 必须装进去的块。装不下时**报错**而不是静默丢 ——
      策略说明（哪些操作要人批）丢了，模型会反复触发审批。
    """

    key: str
    content: str
    priority: int = 50
    title: str = ""
    overflow: str = OVERFLOW_DROP
    unit: str = "chars"
    required: bool = False
    # 装配过程的诊断信息：装了什么、丢了什么、截了多少
    notes: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.overflow not in OVERFLOW_POLICIES:
            raise ValueError(
                f"未知的溢出策略 {self.overflow}，可选：{'、'.join(OVERFLOW_POLICIES)}"
            )

    @property
    def empty(self) -> bool:
        """块是不是空的。空块不占预算，也不该在提示词里留个空标题。"""
        return not self.content.strip()

    def measure(self) -> int:
        """块占用的预算（按 unit 折算成字符当量）。

        预算一律用**字符**算 —— token 数要分词器，而不同厂商的分词粒度差
        好几倍，用字符更可预测、也更好在文档里解释。
        """
        if self.unit == "tokens":
            return len(self.content)
        return len(self.content)

    def rendered(self) -> str:
        """渲染成最终进提示词的样子。"""
        body = self.content.strip()
        if not body:
            return ""
        if not self.title:
            return body
        return f"{self.title}\n{body}"

    def truncated(self, limit: int) -> ContextBlock:
        """返回截断到 ``limit`` 字符的副本。

        截断时保留**头部** —— 规矩、说明这类内容通常写在开头，砍尾比砍头好。
        """
        body = self.content.strip()
        if limit <= 0:
            return ContextBlock(
                key=self.key,
                content="",
                priority=self.priority,
                title=self.title,
                overflow=self.overflow,
                unit=self.unit,
                required=self.required,
                notes=[*self.notes, f"截断到 {limit} 字符"],
            )
        if len(body) <= limit:
            return self
        kept = body[:limit]
        # 留个标记，让模型知道后面被截了 —— 静默截断会让它以为看到的是全部
        note = f"\n…（内容过长，已截断，剩余 {len(body) - limit} 字符）"
        return ContextBlock(
            key=self.key,
            content=kept + note,
            priority=self.priority,
            title=self.title,
            overflow=self.overflow,
            unit=self.unit,
            required=self.required,
            notes=[*self.notes, f"截断到 {limit} 字符"],
        )


__all__ = [
    "OVERFLOW_COMPACT",
    "OVERFLOW_DROP",
    "OVERFLOW_POLICIES",
    "OVERFLOW_TRUNCATE",
    "ContextBlock",
]

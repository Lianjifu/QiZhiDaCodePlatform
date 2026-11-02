"""斜杠命令清单。

前端输入框里敲 ``/`` 弹出的那份清单。**放在后端**是因为命令清单是产品的一部分 ——
前端不该硬编码一份，后端加命令前端自动就有了。

两类命令：

- ``prompt`` —— 把一段提示词填进输入框，用户改完再发。带上下文的任务用这种
  （"先给方案"里的方案是什么，取决于用户接下来补什么）；
- ``action`` —— 前端自己执行的动作，不进对话（换模型、开新会话……）。
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class SlashCommand:
    """一条斜杠命令。"""

    name: str
    description: str
    kind: str = "prompt"
    #: ``prompt`` 类的提示词正文；``action`` 类为空
    template: str = ""
    #: ``action`` 类的动作名；``prompt`` 类为空
    action: str = ""


COMMANDS: tuple[SlashCommand, ...] = (
    SlashCommand(
        name="help",
        description="这个编码助手能做什么，怎么用",
        template=(
            "用几句话说明这个编码助手的用法：我能上传什么、"
            "有哪些斜杠命令、@ 提及怎么用、工具调用和审批是怎么回事。"
        ),
    ),
    SlashCommand(
        name="plan",
        description="先给方案，等我确认再动手",
        template="先给出实现方案，等我确认后再动手：",
    ),
    SlashCommand(
        name="files",
        description="列出工作区里的文件",
        template="列出当前工作区里的文件，按目录组织，并说明每个文件是做什么的。",
    ),
    SlashCommand(
        name="capabilities",
        description="列出当前挂载的能力",
        template="列出当前挂载的能力（工具、技能、MCP 通道），并说明各自适合做什么。",
    ),
    SlashCommand(
        name="skills",
        description="列出可用的技能",
        template="列出当前可用的技能，并说明每个技能在什么时候用。",
    ),
    SlashCommand(
        name="mcp",
        description="列出已配置的 MCP 服务器",
        template="列出已配置的 MCP 服务器，以及它们各自提供了哪些工具。",
    ),
    SlashCommand(
        name="status",
        description="检查平台当前状态",
        template=(
            "检查一下平台当前状态：模型接入是否正常、能力挂载了哪些、"
            "工作区里有多少文件，各说明是否正常。"
        ),
    ),
    SlashCommand(
        name="model",
        description="换一个模型",
        kind="action",
        action="model",
    ),
    SlashCommand(
        name="mention",
        description="提及工作区里的文件或目录",
        kind="action",
        action="mention",
    ),
    SlashCommand(
        name="new",
        description="新开一个会话",
        kind="action",
        action="new",
    ),
    SlashCommand(
        name="clear",
        description="清空当前会话的对话",
        kind="action",
        action="clear",
    ),
    SlashCommand(
        name="rename",
        description="给当前会话改名",
        kind="action",
        action="rename",
    ),
)

__all__ = ["COMMANDS", "SlashCommand"]

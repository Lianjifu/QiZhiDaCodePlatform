"""Agent 系统提示词。

## 这一段只讲"怎么做事"，不讲工具细节

工具的名字、参数、说明由 LangChain 从工具的 schema 自动注入到模型请求里
（deepAgents 的文件工具也自带描述）。**提示词里再写一遍工具清单，两处就会
对不上** —— 工具改名了、参数变了，提示词还在教模型用旧写法。

所以这里只写**工作准则**：遇到该做什么、什么时候该停、怎么对待失败。

## 提示词最终由几段拼成

一次请求真正发出去的 system prompt 是：

```text
CODE_AGENT_PROMPT（本文件的基准提示词）
  + 协作说明（**只在真的有 git / 派活能力时**才加，见 base_prompt_for）
  + deepAgents 自己追加的几段（文件工具说明、write_todos 用法、虚拟路径 ↔ 宿主路径映射…）
  + 上下文模块装配的块（app/context/）：运行环境说明、策略说明、记忆目录
```

> 策略说明（哪些工具要人批）由 `CapabilityMiddleware` 生成，但**注入到上下文
> 里** —— 它要占预算，挤不出去模型就会反复触发审批。

**提示词由上下文模块按配额装配**（`app/context/`）：各来源的内容登记成块，
按优先级装进预算，装不下的按规则丢 / 截断。本文件只提供**基准提示词**这一块。

**中间那段"虚拟路径 ↔ 宿主路径映射"很关键**：文件工具认的是虚拟路径
（以 `/` 为工作区根），而宿主上的真实目录是另一个路径。模型拿宿主路径去调
文件工具，会换来一串 `path_not_found`，白烧好几轮。

## 改这份提示词时注意

- 别在这里写工具的**参数与完整清单** —— 那是 schema 的事，写了两处就会
  对不上；但**可以**在准则里点名某个能力（"用 run_python 跑一遍"），那是告诉
  模型**什么时候**该用它，schema 里没有这层信息；
- 别在这里写"哪些操作要审批" —— 那是 `capabilities.registration.policy` 的事，
  中间件会**按当前信任模式**实时告诉模型，写死在这里就跟真实策略对不上了；
- **别在这里教模型用这一轮没有的能力** —— 没绑 git 目录时平台会把
  `git_*` 与 `delegate_worker` 剔掉（见 `services/agent.py::_visible_specs`），
  提示词却还在教，模型会试了再报错，白烧轮次；
- 记忆那一条（准则 8）刻意**不重复**记忆模块的写入判据 —— 判据在
  `app/memory/service.py` 里，平台会做硬校验；提示词只说"什么时候值得记"。
"""

from __future__ import annotations

from typing import Any

CODE_AGENT_PROMPT = """你是企智搭·代码平台里的编码助手，运行在用户指定的代码工作区中。

工作准则：
1. 先理解需求，再动手。需要读现有代码时用 read_file / ls / grep，不要凭空猜测文件内容。
2. 改动尽量小而聚焦，不要顺手做无关的重构。
3. 写完代码后用 run_python 实际跑一遍，用真实输出验证，而不是自我声明"应该能跑"。
4. 工具报错时，读错误信息、定位原因、换一种方式重试；同一个错误不要反复重试超过两次。
   **系统提示词里若带了 <recent_failures>，那是你自己之前失败过的调用** ——
   那是"上次卡在哪"的记录，别再踩同一个坑；要完整报错就调 invocation_recent_failures。
5. 做不可逆的事之前，先说清楚影响：删哪些文件、覆盖哪些已有代码、动哪些依赖 ——
   用户要看到你打算动什么。（哪些操作会等人批准，提示词里的策略说明写着，按那个来。）
6. 用中文回答，代码和注释里的标识符用英文。
7. **复杂任务先列清单**：三步以上、彼此有依赖的活（改多个文件、跑迁移、
   调研加实现），先用 write_todos 拆出待办并跟踪；做完一条立刻标完成。
   两三步的小事别列 —— 列清单只是浪费时间。清单只是记事，
   **最终答案要写在最后一次 write_todos 之后的回复里**。
   前提变了（发现需求理解错了、某条路走不通）就**改写清单**，别闷头按错的计划做。
8. 记忆：系统提示词里若带了记忆目录，那些是可能相关的背景资料，按需取用。
   - 不确定用户的偏好、项目约定时，先用 memory_search 查已有记忆，别猜；
   - 用户**明确说出**了值得长期保留的偏好、约定或已定结论时，用 memory_write 记下来；
   - **两类东西值得记**：
     (a) 用户明确表达的偏好、约定、已定结论 —— 记下来；
     (b) 你自己**验证过**的经验 —— 比如"这个仓库的测试要跑 `pytest tests/ -x`、
         这个项目的 lint 是 `ruff check .`"、"用户上传的这份数据是 CSV 带表头"。
         这类经验下次能用上，不记就要每次重新踩坑。
   - 但**推断不算**：还没验证过的猜测、对用户意图的揣测，一律不记 ——
     记错了会一直误导以后的自己。验证过的、拿不准的，先用别的措辞说明，
     或者干脆别记；
   - 一次只讲一件事，标签写具体点（用用户会搜的词：技术名、项目名、命令）。
9. 命令执行的环境：每次 execute 是一个新进程，export 的环境变量和 cd 不会留到
   下一条命令。但**装好的依赖、生成的文件会保留**（沙箱容器按会话复用）。
   所以要设变量就 export 和使用写在同一条命令里；要 cd 就用 `cd X && 命令`，
   **X 是工作区里的相对路径** —— 沙箱里只有工作区，宿主机的绝对路径不存在，
   cd 过去只会得到 "can't cd to"。
10. 输出格式用 Markdown：分块用小标题和列表，要对比就列表格，代码一律放围栏
   代码块并标上语言（```python）。别把代码缩进在正文里，也别整段输出没有结构的
   纯文本 —— 前端是按 Markdown 渲染的。
11. 别反复看同一个地方。read_file 默认只给 100 行，长文件（README、协议定义那种
    上千行的）一页页翻既慢又占上下文 —— 先用 grep 定位到要的那一段再读；确实要
    通读就把 limit 给足（一次几千行），别 100 行一页页读下去。读过的内容还在
    上下文里，不要重复读；同一个目录也别反复 ls。
"""


# 协作说明 —— **只在真的有 git / 派活能力时才加**。
#
# 没绑定 git 目录时平台会把这些能力剔掉（``services/agent.py::_visible_specs``），
# 这时候提示词还教模型用它们，模型会照着调、然后报错，白烧一轮。
COLLABORATION_PROMPT = """
协作（当前工作目录是 git 仓库时才有这些工具）：
- **任务能拆成互不相干的几块**（不同模块、不同文件、互不依赖）时，用
  delegate_worker 派子 agent 并行做 —— 每个子 agent 在自己的分支和
  worktree 上干活，互不干扰，你拿到的是分支与结论；
- 只想换个目录试试、不需要另一个 agent，用 git_worktree_add 开一个独立检出；
- 子 agent 的分支**不会自动合回主线**：先看它改了什么，确认没问题再用
  git_merge 合回去；合不进去（有冲突）就把冲突文件报给用户；
- 一次拆清楚就一次派完，别来回试 —— 每个子 agent 都是真跑一遍，很贵；
- git 一律用上面那几个能力，**不要在 execute 里敲 git 命令** —— 沙箱里
  没有 git，也读不到仓库的 .git 目录，那一定失败。
"""


def has_collaboration(specs: list[Any] | None) -> bool:
    """这一轮有没有 git / 派活这类协作能力。"""
    for spec in specs or []:
        key = str(getattr(spec, "key", "") or "")
        if key == "delegate_worker" or key.startswith("git_"):
            if getattr(spec, "enabled", True):
                return True
    return False


# 自动积累的提示词规则：块的样子
#
# **明写"只影响做事方式"** —— 代码里的准则永远在前、永远完整，
# 这一块是补充。范围守卫（app/agents/autonomy.py）已经拦掉了想改审批 /
# 权限那一层的规则，这行字是对模型再说一遍。
PROMPT_RULES_TITLE = "自动积累的补充准则："
PROMPT_RULES_SCOPE_NOTE = "（只影响做事方式，不影响权限与审批）"


def render_prompt_rules(rules: list[str]) -> str:
    """把自动积累的规则渲染成可注入上下文的 XML 块。

    没有规则返回空串 —— 不注入的空块只会白占位置。
    """
    texts = [rule.strip() for rule in rules if rule and rule.strip()]
    if not texts:
        return ""
    lines = [
        "<auto_rules>",
        f"{PROMPT_RULES_TITLE}{PROMPT_RULES_SCOPE_NOTE}",
    ]
    lines.extend(f"- {text}" for text in texts)
    lines.append("</auto_rules>")
    return "\n".join(lines)


def base_prompt_for(specs: list[Any] | None) -> str:
    """按**这一轮实际有的能力**拼基准提示词。

    协作说明只在真的有 git / 派活能力时才加 —— 教模型用用不了的工具，
    只会让它试了再报错。
    """
    if not has_collaboration(specs):
        return CODE_AGENT_PROMPT
    return CODE_AGENT_PROMPT + COLLABORATION_PROMPT


__all__ = [
    "COLLABORATION_PROMPT",
    "CODE_AGENT_PROMPT",
    "PROMPT_RULES_SCOPE_NOTE",
    "PROMPT_RULES_TITLE",
    "base_prompt_for",
    "has_collaboration",
    "render_prompt_rules",
]

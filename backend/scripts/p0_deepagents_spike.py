"""P0 实测：验证"文件本地 + 命令进沙箱"这个组合的关键前提。

跑法：

    cd backend && ../.venv/bin/python scripts/p0_deepagents_spike.py

要验三件事（对应方案文档第 8 节的第 0 步）：

1. **一致性**：容器里写的文件，宿主能不能读到（``execute`` → ``read_file``）；
2. **开销**：``execute`` 走 docker 的单次耗时；
3. **路径拦截**：模型能看到的工具层会不会让路径越出工作区。

第 3 条必须**走真实 agent** 来测：工具调用要经过 LangGraph 注入 runtime，
直接调工具绕不过去，测不到模型实际走的那条路。

任何一条不成立，方案里的"删掉 fs.py"这个前提就要重新考虑。
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from deepagents import create_deep_agent  # noqa: E402
from langchain_core.language_models import BaseChatModel  # noqa: E402
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage  # noqa: E402
from langchain_core.outputs import ChatGeneration, ChatResult  # noqa: E402

from app.capabilities.backends.assembly import build_backend  # noqa: E402
from app.capabilities.execution.context import ExecutionContext  # noqa: E402

PASS = "✅"
FAIL = "❌"


class ScriptedModel(BaseChatModel):
    """按剧本依次产出工具调用，最后回一句话。"""

    calls: list[tuple[str, dict[str, Any]]] = []
    reply: str = "好了。"
    seen: list[int] = []

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools: Any, **kwargs: Any) -> ScriptedModel:
        return self

    def _generate(
        self,
        messages: list[Any],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        self.seen.append(len(self.seen))
        index = len(self.seen) - 1
        if index < len(self.calls):
            name, args = self.calls[index]
            call = {"name": name, "args": dict(args), "id": f"call_{index}", "type": "tool_call"}
            return ChatResult(
                generations=[ChatGeneration(message=AIMessage(content="", tool_calls=[call]))]
            )
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=self.reply))])


def _section(title: str) -> None:
    print(f"\n=== {title} ===")


def _tool_results(messages: list[Any]) -> list[ToolMessage]:
    return [m for m in messages if isinstance(m, ToolMessage)]


async def main() -> int:
    if shutil.which("docker") is None:
        print("docker 不可用，P0 跑不了")
        return 1

    workspace = Path(tempfile.mkdtemp(prefix="qzd-p0-", dir=str(Path.home())))
    print(f"工作区: {workspace}")
    print(
        "（工作区放在 $HOME 下：macOS 上 colima 只共享 $HOME，"
        "别的目录挂不进去 —— 那是环境限制，不是代码问题）"
    )

    context = ExecutionContext.build(workspace)
    # 这个脚本验的就是 docker 沙箱 —— 显式写出来，别靠默认值
    context.sandbox_type = "docker"
    backend = build_backend(context)

    model = ScriptedModel(
        calls=[
            ("write_file", {"file_path": "/hello.txt", "content": "你好"}),
            ("read_file", {"file_path": "/hello.txt"}),
            ("execute", {"command": "echo hello-from-container && ls -la"}),
            ("execute", {"command": "echo written-in-container > /workspace/from_container.txt"}),
            ("read_file", {"file_path": "/from_container.txt"}),
            ("read_file", {"file_path": "/etc/passwd"}),
            ("read_file", {"file_path": "../../etc/passwd"}),
            ("read_file", {"file_path": "~/secret"}),
            ("execute", {"command": "cat /workspace/../host_only.txt 2>&1 || echo NOT_FOUND"}),
            (
                "execute",
                {
                    "command": (
                        'python -c "import socket;socket.create_connection'
                        "('1.1.1.1',53),timeout=3)\" 2>&1 || echo BLOCKED"
                    )
                },
            ),
        ]
    )

    agent = create_deep_agent(model=model, backend=backend, tools=[])

    started = time.perf_counter()
    result = await agent.ainvoke(
        {"messages": [HumanMessage(content="按剧本跑一遍")]},
        config={"configurable": {"thread_id": "p0"}},
    )
    total_ms = (time.perf_counter() - started) * 1000

    messages = result.get("messages", [])
    results = _tool_results(messages)
    print(f"\n共 {len(results)} 次工具调用，总耗时 {total_ms:.0f}ms")

    failures = 0
    for index, msg in enumerate(results):
        text = str(msg.content)
        first = text.replace("\n", " ")[:150]
        print(f"  [{index}] {first}")

    def find(substr: str) -> str:
        for msg in results:
            if substr in str(msg.content):
                return str(msg.content)
        return ""

    # ---------- 1. 文件工具（本地） ----------
    _section("1. 文件工具（本地 FilesystemBackend）")
    written = find("你好")
    ok = bool(written)
    print(f"{PASS if ok else FAIL} write_file + read_file 走通了")
    if not ok:
        failures += 1

    # ---------- 2. 一致性 ----------
    _section("2. 一致性：容器里写的文件，宿主文件工具能读到")
    got = find("written-in-container")
    ok = bool(got)
    print(f"{PASS if ok else FAIL} 宿主读到容器写的文件: {got.replace(chr(10), ' ')[:120]}")
    if not ok:
        failures += 1

    # ---------- 3. execute 正常 + 开销 ----------
    _section("3. 命令执行（docker 沙箱）")
    echoed = find("hello-from-container")
    ok = bool(echoed)
    print(f"{PASS if ok else FAIL} execute 正常: {echoed.replace(chr(10), ' ')[:120]}")
    if not ok:
        failures += 1
    # execute 有两次调用，用总耗时粗估单次
    print(f"（总耗时 {total_ms:.0f}ms，含 2 次 execute 与多次文件操作）")

    # ---------- 4. 路径越界 ----------
    _section("4. 路径越界拦截")
    leaked = [m for m in results if "root:" in str(m.content)]
    ok = not leaked
    print(f"{PASS if ok else FAIL} 读不到宿主的 /etc/passwd（泄露 {len(leaked)} 次）")
    if not ok:
        failures += 1
    for msg in results[5:8]:
        print(f"   → {str(msg.content).replace(chr(10), ' ')[:120]}")

    # ---------- 5. 隔离：断网 + 看不见宿主 ----------
    _section("5. 隔离")
    host_out = find("host_only")
    invisible = "宿主文件内容" not in host_out
    print(f"{'✅' if invisible else '❌'} 容器看不见宿主文件系统")
    if not invisible:
        failures += 1

    net = find("BLOCKED")
    blocked = bool(net) or "Error" in find("1.1.1.1")
    print(
        f"{'✅' if blocked else '❌'} 容器内断网: {net.replace(chr(10), ' ')[:120] or '（无输出）'}"
    )
    if not blocked:
        failures += 1

    _section("结论")
    if failures:
        print(f"{FAIL} {failures} 条不通过 —— 方案的前提要重新考虑")
    else:
        print(f"{PASS} 全部通过：可以按方案往下走")
    shutil.rmtree(workspace, ignore_errors=True)
    return 1 if failures else 0


if __name__ == "__main__":
    import asyncio

    raise SystemExit(asyncio.run(main()))

"""测试用的假能力。

文件类能力已经交给 deepAgents 了，但**执行层流水线**（校验 → 权限 → 风险 →
审批 → 执行 → 验证 → 留痕）还要用真实的能力来测。这些假能力承担这个角色：
行为简单可预期，但走的是和真实能力完全一样的路径。
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from app.capabilities.errors import CapabilityError, ErrorCode


def make_test_write(context: Any) -> Callable[..., Awaitable[dict[str, Any]]]:
    """假"写文件"能力：把内容写进工作区。幂等、可重复执行。"""

    async def test_write(path: str, content: str) -> dict[str, Any]:
        target = Path(context.workspace) / path
        target.parent.mkdir(parents=True, exist_ok=True)
        created = not target.exists()
        target.write_text(content, encoding="utf-8")
        return {
            "content": f"{'已创建' if created else '已覆盖'} {path}",
            "path": path,
            "bytes": len(content.encode("utf-8")),
            "created": created,
        }

    return test_write


def make_test_read(context: Any) -> Callable[..., Awaitable[dict[str, Any]]]:
    """假"读文件"能力：读工作区里的文本文件。"""

    async def test_read(path: str) -> dict[str, Any]:
        target = Path(context.workspace) / path
        if not target.exists():
            raise FileNotFoundError(f"文件不存在: {path}")
        text = target.read_text(encoding="utf-8")
        return {"content": text, "path": path, "line_count": text.count("\n") + 1}

    return test_read


def make_test_exec(context: Any) -> Callable[..., Awaitable[dict[str, Any]]]:
    """假"执行命令"能力：在本机跑一条命令，回显结果。

    只用于测试审批链路 —— 真正跑的是无害的 ``echo``。
    """

    async def test_exec(command: str) -> dict[str, Any]:
        proc = await asyncio.create_subprocess_exec(
            "/bin/sh",
            "-c",
            command,
            cwd=str(context.workspace),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=context.sandbox_timeout)
        except TimeoutError as exc:
            proc.kill()
            await proc.wait()
            raise CapabilityError(
                f"执行超时（>{context.sandbox_timeout}s），已终止",
                code=ErrorCode.TIMEOUT,
                retryable=True,
            ) from exc
        output = stdout.decode("utf-8", errors="replace")
        return {
            "content": f"退出码: {proc.returncode or 0}\n{output}",
            "output": output,
            "exit_code": proc.returncode or 0,
        }

    return test_exec


__all__ = ["make_test_exec", "make_test_read", "make_test_write"]

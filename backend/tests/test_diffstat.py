"""改动量统计（工具块上的 ``+N −M``）。

工具自己不说改了多少行 —— deepAgents 的 ``write_file`` 只回一句
"Updated file xxx"。要显示改动量，就得执行前后各读一次文件比一比。
"""

from __future__ import annotations

import json

import pytest

from app.agents.runner import AgentRunner
from app.api.deps import get_agent_service
from app.core.workspace import resolve_workspace
from app.files.diffstat import line_diff
from app.services.agent import AgentService
from tests.test_agent_capabilities import RecordingModel

API = "/api/v1/agent"


def _frames(text: str) -> list[dict]:
    frames = []
    for block in text.split("\n\n"):
        block = block.strip()
        if not block.startswith("data:"):
            continue
        payload = block[len("data:") :].strip()
        if payload == "[DONE]":
            frames.append({"type": "[DONE]"})
            continue
        frames.append(json.loads(payload))
    return frames


# ---------- 纯函数 ----------


def test_line_diff_counts_added_and_removed() -> None:
    before = "a\nb\nc\n"
    after = "a\nB\nc\nd\n"
    stat = line_diff(before, after)
    assert stat.added == 2  # B（替换 B）+ d
    assert stat.removed == 1  # b
    assert stat.changed


def test_line_diff_identical_is_unchanged() -> None:
    stat = line_diff("same\n", "same\n")
    assert stat.added == 0
    assert stat.removed == 0
    assert not stat.changed


def test_line_diff_new_file_counts_every_line() -> None:
    stat = line_diff("", "a\nb\nc\n")
    assert stat.added == 3
    assert stat.removed == 0


def test_line_diff_deleted_file_counts_every_line() -> None:
    stat = line_diff("a\nb\n", "")
    assert stat.added == 0
    assert stat.removed == 2


# ---------- 走真实工具 ----------


@pytest.mark.asyncio
async def test_write_file_reports_added_lines(app, client, session) -> None:
    """新建文件：整份都是新增。"""
    thread_id = "t-diff-write"
    workspace = resolve_workspace(thread_id)
    workspace.mkdir(parents=True, exist_ok=True)
    # 工作区是留着的 —— 上次跑测试留下的同名文件会让"改动量"变成 0
    (workspace / "note.txt").unlink(missing_ok=True)

    model = RecordingModel(
        tool_name="write_file",
        tool_args={"file_path": "/note.txt", "content": "第一行\n第二行\n第三行\n"},
        reply="写好了",
    )
    app.dependency_overrides[get_agent_service] = lambda: AgentService(
        session=session, runner=AgentRunner(model=model)
    )

    resp = await client.post(API + "/stream", json={"message": "写个文件", "thread_id": thread_id})
    frames = _frames(resp.text)
    results = [f["tool_result"] for f in frames if f["type"] == "tool_result"]
    assert results, f"没有工具结果：{[f['type'] for f in frames]}"

    diff = results[0]
    assert diff["added"] == 3, f"新增行数不对：{diff}"
    assert diff["removed"] == 0, f"删除行数不对：{diff}"
    assert (workspace / "note.txt").exists()


@pytest.mark.asyncio
async def test_edit_file_reports_removed_and_added(app, client, session) -> None:
    """改一行：删一行、加一行。"""
    thread_id = "t-diff-edit"
    workspace = resolve_workspace(thread_id)
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "note.txt").write_text("一\n二\n三\n", encoding="utf-8")

    model = RecordingModel(
        tool_name="edit_file",
        tool_args={"file_path": "/note.txt", "old_string": "二", "new_string": "贰"},
        reply="改好了",
    )
    app.dependency_overrides[get_agent_service] = lambda: AgentService(
        session=session, runner=AgentRunner(model=model)
    )

    resp = await client.post(API + "/stream", json={"message": "改一下", "thread_id": thread_id})
    frames = _frames(resp.text)
    results = [f["tool_result"] for f in frames if f["type"] == "tool_result"]
    assert results, f"没有工具结果：{[f['type'] for f in frames]}"

    diff = results[0]
    assert diff["added"] == 1, f"新增行数不对：{diff}"
    assert diff["removed"] == 1, f"删除行数不对：{diff}"


@pytest.mark.asyncio
async def test_read_file_has_no_diff(app, client, session) -> None:
    """读文件不改东西 —— 不该报改动量。"""
    thread_id = "t-diff-read"
    workspace = resolve_workspace(thread_id)
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "note.txt").write_text("一\n", encoding="utf-8")

    model = RecordingModel(
        tool_name="read_file",
        tool_args={"file_path": "/note.txt"},
        reply="读完了",
    )
    app.dependency_overrides[get_agent_service] = lambda: AgentService(
        session=session, runner=AgentRunner(model=model)
    )

    resp = await client.post(API + "/stream", json={"message": "读一下", "thread_id": thread_id})
    frames = _frames(resp.text)
    results = [f["tool_result"] for f in frames if f["type"] == "tool_result"]
    assert results
    assert results[0]["added"] is None
    assert results[0]["removed"] is None

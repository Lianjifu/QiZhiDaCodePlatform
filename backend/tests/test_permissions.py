"""会话级权限模式的测试。

模式决定「这一轮要不要人批」—— 语义对齐 Claude Code 的权限模式：
自动 / 手动 / 接受编辑 / 只读 / 绕过权限。

**为什么要有会话级**：全局只有一个配置项，改一次要重启服务；实际需要的是
随手切换（读代码时自动、改代码时逐个确认、只想看方案时只读）。
"""

from __future__ import annotations

import pytest

from app.agents.permissions import (
    clear_permission_mode,
    permission_path,
    resolve_permission_mode,
    set_permission_mode,
)
from app.capabilities.registration.policy import PermissionMode, normalize_mode
from app.core.config import settings


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    """权限文件写到临时目录 —— 别动真实的会话目录。"""
    monkeypatch.setattr(settings, "workspace_root", str(tmp_path / "workspace"))
    monkeypatch.setattr(settings, "capability_trust_mode", "balanced")
    yield


# ---------- 模式取值 ----------


def test_老名字映射到新模式():
    """配置里的老值还能用 —— 不用改 .env。"""
    assert normalize_mode("balanced") == PermissionMode.AUTO
    assert normalize_mode("strict") == PermissionMode.MANUAL
    assert normalize_mode("permissive") == PermissionMode.BYPASS
    assert normalize_mode("auto") == PermissionMode.AUTO
    assert normalize_mode("accept_edits") == PermissionMode.ACCEPT_EDITS
    assert normalize_mode("plan") == PermissionMode.PLAN
    # 认不出来的按自动 —— 宁可多问一次，也不要变成"全放行"
    assert normalize_mode("乱写的") == PermissionMode.AUTO
    assert normalize_mode("") == PermissionMode.AUTO


# ---------- 会话级存储 ----------


def test_没设置时用全局配置():
    assert resolve_permission_mode("t-none") == "auto"


def test_设置后按会话生效():
    assert set_permission_mode("t-a", "accept_edits") == "accept_edits"
    assert resolve_permission_mode("t-a") == "accept_edits"
    # 别的会话不受影响
    assert resolve_permission_mode("t-b") == "auto"
    assert permission_path("t-a").exists()


def test_恢复默认(monkeypatch):
    monkeypatch.setattr(settings, "capability_allow_bypass", True)
    set_permission_mode("t-a", "bypass")
    assert resolve_permission_mode("t-a") == "bypass"
    set_permission_mode("t-a", "default")
    assert resolve_permission_mode("t-a") == "auto"
    assert not permission_path("t-a").exists()


def test_不支持的模式要报错():
    with pytest.raises(ValueError):
        set_permission_mode("t-a", "whatever")


def test_清掉覆盖():
    set_permission_mode("t-a", "plan")
    clear_permission_mode("t-a")
    assert resolve_permission_mode("t-a") == "auto"


def test_文件坏了也不影响会话():
    """权限文件读不出来就退回全局默认 —— 不该让会话挂掉。"""
    set_permission_mode("t-a", "plan")
    permission_path("t-a").write_text("{ 这不是 json", encoding="utf-8")
    assert resolve_permission_mode("t-a") == "auto"


# ---------- 接口 ----------


async def test_权限模式接口(client, monkeypatch):
    """GET 读当前模式，POST 改 —— 界面那个选择器靠它。"""
    monkeypatch.setattr(settings, "capability_allow_bypass", True)
    response = await client.get("/api/v1/agent/t-perm-1/permission")
    assert response.status_code == 200
    assert response.json()["data"]["permission_mode"] == "auto"

    response = await client.post(
        "/api/v1/agent/t-perm-1/permission",
        json={"mode": "bypass"},
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["permission_mode"] == "bypass"
    assert data["session_override"] is True

    response = await client.get("/api/v1/agent/t-perm-1/permission")
    assert response.json()["data"]["permission_mode"] == "bypass"


async def test_不支持的模式接口要报错(client):
    response = await client.post(
        "/api/v1/agent/t-perm-2/permission",
        json={"mode": "whatever"},
    )
    assert response.status_code >= 400


# ---------- bypass 总闸 ----------


def test_bypass默认关着(monkeypatch):
    """bypass 必须有进程级总闸 —— 平台没鉴权，那条接口本身就是绕过链。"""
    monkeypatch.setattr(settings, "capability_allow_bypass", False)
    with pytest.raises(ValueError, match="bypass"):
        set_permission_mode("t-gate", "bypass")
    assert not permission_path("t-gate").exists()


def test_bypass总闸开着才能切(monkeypatch):
    monkeypatch.setattr(settings, "capability_allow_bypass", True)
    assert set_permission_mode("t-gate", "bypass") == "bypass"
    assert resolve_permission_mode("t-gate") == "bypass"


def test_总闸关掉后存的bypass也不生效(monkeypatch):
    """闸是后来关的 —— 之前存下的 bypass 也不能继续用。"""
    monkeypatch.setattr(settings, "capability_allow_bypass", True)
    set_permission_mode("t-gate2", "bypass")
    monkeypatch.setattr(settings, "capability_allow_bypass", False)
    assert resolve_permission_mode("t-gate2") == "auto"


def test_全局配置写bypass也要过总闸(monkeypatch):
    """运维在配置里写了 bypass —— 总闸关着也不生效（退回 auto）。"""
    monkeypatch.setattr(settings, "capability_trust_mode", "bypass")
    monkeypatch.setattr(settings, "capability_allow_bypass", False)
    assert resolve_permission_mode("t-gate3") == "auto"
    monkeypatch.setattr(settings, "capability_allow_bypass", True)
    assert resolve_permission_mode("t-gate3") == "bypass"


async def test_bypass被拒要报422(client):
    """请求不合法 —— 该是 4xx（平台的校验错误码是 422），不是 500。"""
    response = await client.post(
        "/api/v1/agent/t-perm-3/permission",
        json={"mode": "bypass"},
    )
    assert response.status_code == 422

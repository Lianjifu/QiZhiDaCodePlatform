"""会话级权限模式 —— 「这一轮/这个会话怎么决定要不要人批」。

**为什么要有会话级**：全局只有一个 ``CAPABILITY_TRUST_MODE``，要改就得改配置、
重启服务。可实际需要的是随手切换：读代码时想自动放行，改代码时想逐个确认，
只想看方案时想只读。

模式是**会话级**的（存在文件里），全局配置只是默认值。语义对齐 Claude Code
的权限模式（见 ``app/capabilities/registration/policy.py::PermissionMode``）：

======================  ====================================================
模式                    行为
======================  ====================================================
``auto``（默认）        按风险档位：低档自动，中档与高档要人批
``manual``              只读不问，其余任何操作都要人批
``accept_edits``        文件编辑自动，执行命令 / 联网 / 破坏性照旧问
``plan``                只读：写类与执行类工具不暴露给模型
``bypass``              全放行（含破坏性）
======================  ====================================================

**存文件而不是建表**：跟绑定关系同一个理由（``app/environments/store.py``）——
一个会话一条，跨进程可见（审批恢复可能落在另一个进程上），不用迁移。

文件放在 ``<workspace_root>/.qzd/permissions/<会话目录名>.json``，目录名用
``workspace_dir_name(thread_id)`` —— 与工作区目录同名，一眼能对上。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from app.capabilities.registration.policy import PermissionMode, normalize_mode
from app.core.config import settings
from app.core.workspace import workspace_dir_name

logger = logging.getLogger(__name__)

# 权限文件所在目录（相对工作区根）
PERMISSIONS_DIRNAME = ".qzd/permissions"


def permissions_root() -> Path:
    """权限文件的根目录。"""
    return Path(settings.workspace_root).resolve() / PERMISSIONS_DIRNAME


def permission_path(thread_id: str) -> Path:
    """某个会话的权限文件路径。"""
    return permissions_root() / f"{workspace_dir_name(thread_id)}.json"


def resolve_permission_mode(thread_id: str) -> str:
    """这个会话实际用的权限模式。

    会话有覆盖就用覆盖的，没有就用全局配置。

    Args:
        thread_id: 会话 ID。

    Returns:
        ``auto`` / ``manual`` / ``accept_edits`` / ``plan`` / ``bypass``。
    """
    override = _read(thread_id)
    if override:
        return override
    return normalize_mode(settings.capability_trust_mode)


def set_permission_mode(thread_id: str, mode: str | None) -> str:
    """设置这个会话的权限模式。

    Args:
        thread_id: 会话 ID。
        mode: 模式名；``None`` / ``"default"`` / ``"auto"`` 恢复默认
            （``auto`` 显式写出来也按"恢复默认"处理 —— 全局配置可能不是 auto）。

    Returns:
        设置之后的实际模式。

    Raises:
        ValueError: 给了不支持的取值。
    """
    text = str(mode or "").strip().lower()
    if text in ("", "default", "auto"):
        clear_permission_mode(thread_id)
        return normalize_mode(settings.capability_trust_mode)
    if text not in PermissionMode:
        raise ValueError(f"不支持的权限模式：{mode!r}")
    path = permission_path(thread_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"mode": text}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return text


def clear_permission_mode(thread_id: str) -> None:
    """清掉这个会话的覆盖（文件不存在就当本来就没有）。"""
    permission_path(thread_id).unlink(missing_ok=True)


def _read(thread_id: str) -> str | None:
    """读这个会话的覆盖值。

    读不出来（文件没了 / 坏了）就当没有 —— 退回全局默认，不让会话挂掉。
    """
    path = permission_path(thread_id)
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("权限文件坏了，忽略: %s", path)
        return None
    if not isinstance(data, dict):
        return None
    mode = str(data.get("mode") or "").strip().lower()
    return mode if mode in PermissionMode else None


__all__ = [
    "PERMISSIONS_DIRNAME",
    "clear_permission_mode",
    "normalize_mode",
    "permission_path",
    "permissions_root",
    "resolve_permission_mode",
    "set_permission_mode",
]

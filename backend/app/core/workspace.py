"""工作区路径解析。

**每个会话一份工作区**，agent 的文件工具以 ``/`` 为工作区根目录看它。

## 为什么目录名要带哈希

早先的实现只做字符过滤：``"".join(ch for ch in thread_id if ch.isalnum() or ch in "-_")``。
问题是它**不一一对应** —— ``"a/b"`` 与 ``"ab"`` 过滤后都是 ``"ab"``，两个不同的
会话会落到同一个目录。

平台现在没有鉴权，``thread_id`` 由客户端自己给。这种碰撞等于把两个会话的
文件混在一起：一边能读到另一边的上传件与产物。所以目录名后面缀一段
``thread_id`` 的哈希，保证不同 ``thread_id`` 一定落在不同目录。
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from app.core.config import settings
from app.core.exceptions import ValidationError

# 目录名前缀的最大长度（哈希后缀另算）
PREFIX_MAX = 48


def workspace_dir_name(thread_id: str) -> str:
    """把 ``thread_id`` 映射成工作区目录名。

    目录名 = 可读前缀 + ``_`` + ``thread_id`` 的 sha256 前 8 位。
    哈希保证不同 ``thread_id`` 一定落到不同目录。

    Args:
        thread_id: 会话 ID。

    Returns:
        目录名（不含路径分隔符）。

    Raises:
        ValidationError: ``thread_id`` 为空。
    """
    if not thread_id or not thread_id.strip():
        raise ValidationError("thread_id 不能为空")
    prefix = "".join(ch for ch in thread_id if ch.isalnum() or ch in "-_")[:PREFIX_MAX]
    digest = hashlib.sha256(thread_id.encode("utf-8")).hexdigest()[:8]
    return f"{prefix or 'thread'}_{digest}"


def workspace_root() -> Path:
    """工作区根目录。"""
    return Path(settings.workspace_root).resolve()


def resolve_workspace(thread_id: str) -> Path:
    """会话的工作区目录（不存在就建）。

    Args:
        thread_id: 会话 ID。

    Returns:
        工作区目录。
    """
    workspace = workspace_root() / workspace_dir_name(thread_id)
    workspace.mkdir(parents=True, exist_ok=True)
    return workspace


__all__ = ["PREFIX_MAX", "resolve_workspace", "workspace_dir_name", "workspace_root"]

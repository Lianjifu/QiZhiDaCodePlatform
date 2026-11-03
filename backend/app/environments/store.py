"""会话 ↔ 工作目录的绑定关系。

**为什么存文件而不是建表**：``resolve_workspace()`` 是同步函数，被文件接口、
agent service 等一大片地方调用；绑定关系一旦放进数据库，这个函数就得变成
async，牵动的东西太多。而绑定关系天生就是「一个会话一条」，放文件里刚好。

文件放在 ``<workspace_root>/.qzd/bindings/<会话目录名>.json``：

- ``.qzd`` 是隐藏目录，**不会**出现在会话工作区的文件列表里（那个列表只看
  会话自己的目录）；
- 名字用 ``workspace_dir_name(thread_id)`` —— 与工作区目录同名，一眼能对上，
  而且天然避开了路径穿越（那个函数会过滤字符并加哈希）。

**跨进程可见**：审批恢复可能落在另一个 worker 进程上，绑定关系必须所有进程
都读得到 —— 文件满足这一点（与 ``app/agents/checkpointer.py`` 同一个理由）。
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from app.core.config import settings
from app.core.workspace import workspace_dir_name

logger = logging.getLogger(__name__)

# 绑定文件所在目录（相对工作区根）
BINDINGS_DIRNAME = ".qzd"

# 绑定的两种环境
KIND_FOLDER = "folder"
KIND_WORKTREE = "worktree"


@dataclass
class Binding:
    """一个会话当前绑定的环境。"""

    path: str
    kind: str = KIND_FOLDER
    #: 显示名 —— 用户给的名字（新建环境时填的），没给就是目录名
    name: str = ""
    branch: str = ""
    repo: str | None = None
    bound_at: str = ""

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> Binding:
        return cls(
            path=str(data.get("path") or ""),
            kind=str(data.get("kind") or KIND_FOLDER),
            name=str(data.get("name") or ""),
            branch=str(data.get("branch") or ""),
            repo=str(data.get("repo") or None),
            bound_at=str(data.get("bound_at") or ""),
        )


def bindings_root() -> Path:
    """绑定文件的根目录。"""
    return Path(settings.workspace_root).resolve() / BINDINGS_DIRNAME / "bindings"


def binding_path(thread_id: str) -> Path:
    """某个会话的绑定文件路径。"""
    return bindings_root() / f"{workspace_dir_name(thread_id)}.json"


def load_binding(thread_id: str) -> Binding | None:
    """读这个会话的绑定。

    Returns:
        绑定；没绑过或文件坏了都是 ``None``（**不抛异常** —— 绑定读不出来
        不该让整个会话挂掉，退回默认工作区就行）。
    """
    path = binding_path(thread_id)
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("绑定文件坏了，忽略: %s", path)
        return None
    if not isinstance(data, dict):
        return None
    binding = Binding.from_dict(data)
    if not binding.path:
        return None
    return binding


def save_binding(thread_id: str, binding: Binding) -> Binding:
    """写这个会话的绑定。"""
    if not binding.bound_at:
        binding.bound_at = datetime.now(UTC).isoformat()
    path = binding_path(thread_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(binding.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return binding


def clear_binding(thread_id: str) -> None:
    """解绑这个会话（文件不存在就当已经解绑）。"""
    binding_path(thread_id).unlink(missing_ok=True)


__all__ = [
    "BINDINGS_DIRNAME",
    "KIND_FOLDER",
    "KIND_WORKTREE",
    "Binding",
    "binding_path",
    "bindings_root",
    "clear_binding",
    "load_binding",
    "save_binding",
]

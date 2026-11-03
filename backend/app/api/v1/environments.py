"""工作目录（环境）接口。

**安全提醒**：平台目前没有鉴权（见记忆里的 ``no-auth-for-now``）。这组接口
能在**宿主机**上做两件事：

- 把会话绑到白名单内的任意目录（agent 之后就在那里读写文件、跑命令）；
- 建/删 git worktree（在仓库里开分支、改仓库状态）。

所以白名单（``REPO_ROOTS``）是唯一的闸门 —— 默认留空 = 一个都不许绑。
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Query

from app.environments.paths import repo_roots
from app.environments.service import EnvironmentService
from app.environments.store import load_binding
from app.git.service import GitService
from app.schemas.common import ResponseModel
from app.schemas.environment import (
    BindEnvironmentIn,
    CreateWorktreeIn,
    EnvironmentBindingOut,
    EnvironmentListOut,
    EnvironmentOut,
    WorktreeListOut,
    WorktreeOut,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/environments", tags=["工作目录"])


def _service() -> EnvironmentService:
    return EnvironmentService()


@router.get("", response_model=ResponseModel[EnvironmentListOut], summary="列出可用的工作目录")
async def list_environments() -> ResponseModel[EnvironmentListOut]:
    """列出白名单下的目录、仓库与已存在的 worktree。

    白名单没配置时 ``enabled=False``、列表为空 —— 平台不猜用户想开放什么。
    """
    roots = repo_roots()
    if not roots:
        return ResponseModel[EnvironmentListOut](
            data=EnvironmentListOut(environments=[], roots=[], enabled=False)
        )
    service = _service()
    environments = service.list_environments()
    return ResponseModel[EnvironmentListOut](
        data=EnvironmentListOut(
            environments=[EnvironmentOut(**env.to_dict()) for env in environments],
            roots=[str(root) for root in roots],
            enabled=True,
        )
    )


@router.post("", response_model=ResponseModel[EnvironmentOut], summary="创建 worktree")
async def create_worktree(payload: CreateWorktreeIn) -> ResponseModel[EnvironmentOut]:
    """从一个仓库派生出新的 worktree（独立检出 + 新分支）。

    worktree 落在白名单根目录下的 ``.qzd-worktrees/`` 里 —— 不在仓库旁边，
    免得给用户的目录塞一堆未跟踪的文件。
    """
    env = _service().create_worktree(payload.repo_path, payload.branch, base=payload.base)
    return ResponseModel[EnvironmentOut](data=EnvironmentOut(**env.to_dict()))


@router.delete("", response_model=ResponseModel[dict[str, str]], summary="删除 worktree")
async def remove_worktree(
    path: Annotated[str, Query(description="worktree 目录")],
) -> ResponseModel[dict[str, str]]:
    """删掉一个平台创建的 worktree（分支还留在仓库里）。"""
    _service().remove_worktree(path)
    return ResponseModel[dict[str, str]](data={"path": path, "status": "deleted"})


@router.get("/worktrees", response_model=ResponseModel[WorktreeListOut], summary="列出 worktree")
async def list_worktrees(
    repo_path: Annotated[str | None, Query(description="仓库目录；不给就扫白名单下的仓库")] = None,
) -> ResponseModel[WorktreeListOut]:
    """列出 worktree。

    给了 ``repo_path`` 就只列那个仓库的；不给就把白名单下的仓库都扫一遍。
    """
    service = _service()
    targets: list[str] = []
    if repo_path:
        targets = [repo_path]
    else:
        targets = [env.path for env in service.list_environments() if env.is_repo]

    seen: dict[str, WorktreeOut] = {}
    for target in targets:
        git = GitService(target)
        if not git.is_repo():
            continue
        for item in git.worktree_list():
            seen[item.path] = WorktreeOut(
                path=item.path,
                branch=item.branch,
                head=item.head[:8],
                kind="repo" if item.path == str(git.repo_root()) else "worktree",
            )
    return ResponseModel[WorktreeListOut](data=WorktreeListOut(worktrees=list(seen.values())))


@router.get(
    "/binding/{thread_id}",
    response_model=ResponseModel[EnvironmentBindingOut],
    summary="看会话当前绑的工作目录",
)
async def get_binding(thread_id: str) -> ResponseModel[EnvironmentBindingOut]:
    """这个会话当前绑在哪个目录上。"""
    binding = load_binding(thread_id)
    if binding is None:
        return ResponseModel[EnvironmentBindingOut](data=EnvironmentBindingOut(thread_id=thread_id))
    return ResponseModel[EnvironmentBindingOut](
        data=EnvironmentBindingOut(
            thread_id=thread_id,
            path=binding.path,
            kind=binding.kind,
            name=binding.name,
            branch=binding.branch,
            repo=binding.repo,
            bound_at=binding.bound_at,
        )
    )


@router.post(
    "/binding/{thread_id}",
    response_model=ResponseModel[EnvironmentBindingOut],
    summary="把会话绑到工作目录",
)
async def bind_environment(
    thread_id: str,
    payload: BindEnvironmentIn,
) -> ResponseModel[EnvironmentBindingOut]:
    """把会话绑到某个目录。

    目录必须在白名单里（``REPO_ROOTS``）—— 这是平台没有鉴权时唯一的闸门。
    """
    binding = _service().bind(thread_id, payload.path, label=payload.label)
    return ResponseModel[EnvironmentBindingOut](
        data=EnvironmentBindingOut(
            thread_id=thread_id,
            path=binding.path,
            kind=binding.kind,
            name=binding.name,
            branch=binding.branch,
            repo=binding.repo,
            bound_at=binding.bound_at,
        )
    )


@router.delete(
    "/binding/{thread_id}",
    response_model=ResponseModel[dict[str, str]],
    summary="解绑工作目录",
)
async def unbind_environment(thread_id: str) -> ResponseModel[dict[str, str]]:
    """解绑这个会话（回到平台自带的会话工作区）。"""
    _service().unbind(thread_id)
    return ResponseModel[dict[str, str]](data={"thread_id": thread_id, "status": "unbound"})


__all__ = ["router"]

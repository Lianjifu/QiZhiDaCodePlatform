"""工作目录（环境）的请求/响应模型。"""

from pydantic import BaseModel, Field


class EnvironmentOut(BaseModel):
    """一个可作为工作目录的环境。"""

    path: str = Field(description="宿主上的绝对路径")
    name: str = Field(default="", description="目录名")
    kind: str = Field(default="folder", description="folder（普通目录）/ worktree（独立检出）")
    is_repo: bool = Field(default=False, description="是不是 git 仓库")
    branch: str = Field(default="", description="当前分支")
    repo: str | None = Field(default=None, description="worktree 所属的主仓库路径")
    dirty: bool = Field(default=False, description="工作区有没有未提交的改动")


class EnvironmentListOut(BaseModel):
    """环境清单。"""

    environments: list[EnvironmentOut] = Field(default_factory=list)
    roots: list[str] = Field(default_factory=list, description="配置里允许的根目录")
    enabled: bool = Field(default=False, description="平台有没有配置可用的根目录")


class BindEnvironmentIn(BaseModel):
    """把会话绑到某个环境。"""

    path: str = Field(min_length=1, max_length=1024, description="要绑定的目录绝对路径")


class CreateWorktreeIn(BaseModel):
    """从一个仓库派生 worktree。"""

    repo_path: str = Field(min_length=1, max_length=1024, description="仓库目录")
    branch: str = Field(min_length=1, max_length=200, description="新分支名")
    base: str | None = Field(default=None, max_length=200, description="基点分支或提交")


class EnvironmentBindingOut(BaseModel):
    """会话当前绑定的环境。"""

    thread_id: str = Field(description="会话 ID")
    path: str = Field(default="", description="绑定的目录；空串表示没绑")
    kind: str = Field(default="", description="folder / worktree")
    name: str = Field(default="", description="目录名")
    branch: str = Field(default="", description="分支")
    repo: str | None = Field(default=None, description="所属的主仓库")
    bound_at: str = Field(default="", description="绑定时间")


class WorktreeOut(BaseModel):
    """一个 worktree（给状态面板的协作段用）。"""

    path: str = Field(description="worktree 目录")
    branch: str = Field(default="", description="分支")
    head: str = Field(default="", description="HEAD 的短 sha")
    kind: str = Field(default="worktree", description="folder / worktree")
    repo: str | None = Field(default=None, description="所属的主仓库")


class WorktreeListOut(BaseModel):
    """worktree 清单。"""

    worktrees: list[WorktreeOut] = Field(default_factory=list)


__all__ = [
    "BindEnvironmentIn",
    "CreateWorktreeIn",
    "EnvironmentBindingOut",
    "EnvironmentListOut",
    "EnvironmentOut",
    "WorktreeListOut",
    "WorktreeOut",
]

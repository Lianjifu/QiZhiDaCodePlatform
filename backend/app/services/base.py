"""服务层：承载业务规则，是本项目的核心层。

约定：
- Service 只依赖 Repository 与 Schema，不直接碰 FastAPI 的 Request/Response；
- 需要 HTTP 语义时抛 `app.core.exceptions.AppError` 子类；
- 事务边界放在 Service 里（一个 Service 方法 = 一个业务用例 = 一个事务）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.base import BaseRepository
from app.schemas.common import PageMeta

RepoT = TypeVar("RepoT", bound=BaseRepository)


@dataclass
class PageResult:
    """服务层返回的分页结果。

    items 里放领域对象（ORM 实例），由 Controller 负责转成响应 Schema。
    """

    items: list[Any] = field(default_factory=list)
    meta: PageMeta = field(default_factory=PageMeta)


class BaseService(Generic[RepoT]):
    """服务基类，持有一个仓储实例。"""

    repository: type[BaseRepository]

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = self.repository(session)

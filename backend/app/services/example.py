"""示例服务：演示业务规则的写法和事务边界。"""

from __future__ import annotations

from sqlalchemy.exc import IntegrityError

from app.core.exceptions import ConflictError, NotFoundError
from app.models.example import Example
from app.repositories.example import ExampleRepository
from app.schemas.common import PageMeta
from app.schemas.example import ExampleCreate, ExampleUpdate
from app.services.base import BaseService, PageResult


class ExampleService(BaseService[ExampleRepository]):
    repository = ExampleRepository

    async def get_example(self, example_id: int) -> Example:
        example = await self.repo.get(example_id)
        if example is None:
            raise NotFoundError(f"示例 {example_id} 不存在")
        return example

    async def list_examples(self, *, page: int = 1, page_size: int = 20) -> PageResult:
        page = max(page, 1)
        page_size = min(max(page_size, 1), 100)
        total = await self.repo.count()
        items = await self.repo.list(offset=(page - 1) * page_size, limit=page_size)
        pages = (total + page_size - 1) // page_size
        return PageResult(
            items=list(items),
            meta=PageMeta(total=total, page=page, page_size=page_size, pages=pages),
        )

    async def create_example(self, payload: ExampleCreate) -> Example:
        if await self.repo.get_by_name(payload.name) is not None:
            raise ConflictError(f"名称 {payload.name} 已存在")
        try:
            example = await self.repo.create(name=payload.name, description=payload.description)
        except IntegrityError as exc:  # 唯一约束兜底，防止并发写入绕过上面的检查
            await self.session.rollback()
            raise ConflictError(f"名称 {payload.name} 已存在") from exc
        await self.session.commit()
        return example

    async def update_example(self, example_id: int, payload: ExampleUpdate) -> Example:
        example = await self.get_example(example_id)
        data = payload.model_dump(exclude_unset=True)
        if data.get("name") is None:
            data.pop("name", None)
        if "name" in data and data["name"] != example.name:
            if await self.repo.get_by_name(data["name"]) is not None:
                raise ConflictError(f"名称 {data['name']} 已存在")
        example = await self.repo.update(example, **data)
        await self.session.commit()
        return example

    async def delete_example(self, example_id: int) -> None:
        example = await self.get_example(example_id)
        await self.repo.delete(example)
        await self.session.commit()

"""工具（原子能力）服务：CRUD + 可用性自测。"""

from __future__ import annotations

from sqlalchemy.exc import IntegrityError

from app.capabilities.execution.context import ExecutionContext
from app.capabilities.execution.tooling import build_tools
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.models.mcp_server import MCPServer
from app.models.tool import Tool
from app.repositories.mcp_server import MCPServerRepository
from app.repositories.skill import SkillRepository
from app.repositories.tool import ToolRepository
from app.schemas.common import PageMeta
from app.schemas.tool import ToolCreate, ToolTestOut, ToolUpdate, validate_parameters
from app.services.base import BaseService, PageResult


class ToolService(BaseService[ToolRepository]):
    repository = ToolRepository

    async def get_tool(self, tool_id: int) -> Tool:
        tool = await self.repo.get(tool_id)
        if tool is None:
            raise NotFoundError(f"工具 {tool_id} 不存在")
        return tool

    async def list_tools(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
        type: str | None = None,
        enabled: bool | None = None,
        origin: str | None = None,
        tag: str | None = None,
    ) -> PageResult:
        page = max(page, 1)
        page_size = min(max(page_size, 1), 100)
        items = await self.repo.list_all()
        if type:
            items = [item for item in items if item.type == type]
        if enabled is not None:
            items = [item for item in items if item.enabled is enabled]
        if origin:
            items = [item for item in items if item.origin == origin]
        if tag:
            items = [item for item in items if tag in (item.tags or [])]
        total = len(items)
        start = (page - 1) * page_size
        window = items[start : start + page_size]
        pages = (total + page_size - 1) // page_size
        return PageResult(
            items=window,
            meta=PageMeta(total=total, page=page, page_size=page_size, pages=pages),
        )

    async def create_tool(self, payload: ToolCreate) -> Tool:
        if await self.repo.get_by_name(payload.name) is not None:
            raise ConflictError(f"工具 {payload.name} 已存在")
        await self._check_mcp_source(payload.source, payload.type)
        try:
            tool = await self.repo.create(**payload.model_dump())
        except IntegrityError as exc:
            await self.session.rollback()
            raise ConflictError(f"工具 {payload.name} 已存在") from exc
        await self.session.commit()
        return tool

    async def update_tool(self, tool_id: int, payload: ToolUpdate) -> Tool:
        tool = await self.get_tool(tool_id)
        data = payload.model_dump(exclude_unset=True)
        if "name" in data and data["name"] and data["name"] != tool.name:
            if await self.repo.get_by_name(data["name"]) is not None:
                raise ConflictError(f"工具 {data['name']} 已存在")
        merged_type = data.get("type", tool.type)
        merged_source = data.get("source", tool.source)
        await self._check_mcp_source(merged_source, merged_type)
        # **入参 schema 只有 http 类型才要求** —— 合并后的类型说了算
        # （改的只是名字、类型还是 builtin 的，不该被入参 schema 拦下）。
        if merged_type == "http":
            try:
                validate_parameters(data.get("parameters", tool.parameters) or {})
            except ValueError as exc:
                raise ValidationError(str(exc)) from exc
        tool = await self.repo.update(tool, **data)
        await self.session.commit()
        return tool

    async def delete_tool(self, tool_id: int) -> None:
        tool = await self.get_tool(tool_id)
        # 工具被删，技能里对它的引用也要清掉，否则会留下悬挂关联
        skill_repo = SkillRepository(self.session)
        await skill_repo.remove_tool_globally(tool_id)
        await self.repo.delete(tool)
        await self.session.commit()

    async def test_tool(self, tool_id: int) -> ToolTestOut:
        """尝试把工具构造出来，验证配置是否可用（不真正调用）。"""
        tool = await self.get_tool(tool_id)
        servers: dict[str, MCPServer] = {}
        if tool.type == "mcp":
            server_repo = MCPServerRepository(self.session)
            server = await server_repo.get_by_name(tool.source or "")
            if server is None:
                return ToolTestOut(
                    ok=False,
                    tool_id=tool.id,
                    name=tool.name,
                    message=f"MCP 服务器 {tool.source} 不存在",
                )
            servers[server.name] = server
        try:
            spec = _spec_of(tool)
            context = ExecutionContext.build(mcp_servers=servers)
            built_list = await build_tools([spec], context)
        except Exception as exc:
            return ToolTestOut(ok=False, tool_id=tool.id, name=tool.name, message=str(exc)[:500])
        if not built_list:
            return ToolTestOut(
                ok=False, tool_id=tool.id, name=tool.name, message="能力不可用，已跳过"
            )
        built = built_list[0]
        properties = _schema_property_names(getattr(built, "args_schema", None))
        return ToolTestOut(
            ok=True,
            tool_id=tool.id,
            name=built.name,
            message=f"构造成功，入参：{', '.join(properties) or '无'}",
        )

    async def _check_mcp_source(self, source: str | None, tool_type: str | None) -> None:
        if tool_type != "mcp" or not source:
            return
        server_repo = MCPServerRepository(self.session)
        if await server_repo.get_by_name(source) is None:
            raise ConflictError(f"MCP 服务器 {source} 不存在")


def _spec_of(tool: Tool):
    """把 ORM 行转成能力声明（复用注册层的映射规则）。"""
    from app.capabilities.registration.registry import _tool_spec

    return _tool_spec(tool)


def _schema_property_names(schema: object) -> list[str]:
    """取出工具的入参名。

    schema 可能是 Pydantic 模型（内置能力自动推断的）或 JSON Schema 字典
    （http 能力由用户登记的），两种都要兼容。
    """
    if isinstance(schema, dict):
        return list((schema.get("properties") or {}).keys())
    model_fields = getattr(schema, "model_fields", None)
    if model_fields:
        return list(model_fields.keys())
    return []

"""编码助手接口。

提供两种交互方式：
- `POST /agent/chat`：跑完一轮再整体返回，适合脚本调用；
- `POST /agent/stream`：SSE 流式推送，适合前端边生成边显示。
"""

from __future__ import annotations

import json
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from app.api.deps import get_agent_service
from app.schemas.agent import AgentChatOut, AgentChatRequest
from app.schemas.common import ResponseModel
from app.services.agent import AgentService

router = APIRouter(prefix="/agent", tags=["编码助手"])

ServiceDep = Annotated[AgentService, Depends(get_agent_service)]


@router.post(
    "/chat",
    response_model=ResponseModel[AgentChatOut],
    summary="与编码助手对话（一轮）",
)
async def chat(
    payload: AgentChatRequest,
    service: ServiceDep,
) -> ResponseModel[AgentChatOut]:
    result = await service.chat(payload.message, payload.thread_id, payload.provider_id)
    return ResponseModel[AgentChatOut](
        data=AgentChatOut(
            thread_id=result.thread_id,
            content=result.content,
            steps=result.steps,
            tool_calls=[{"name": call["name"], "args": call["args"]} for call in result.tool_calls],
        )
    )


@router.post("/stream", summary="与编码助手对话（SSE 流式）")
async def stream(payload: AgentChatRequest, service: ServiceDep) -> StreamingResponse:
    async def event_stream():
        async for text in service.stream(payload.message, payload.thread_id, payload.provider_id):
            frame = json.dumps({"text": text}, ensure_ascii=False)
            yield f"data: {frame}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )

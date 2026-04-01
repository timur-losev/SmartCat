"""Text chat endpoint with SSE streaming."""

from __future__ import annotations

import json
import uuid

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from smartcat.api.deps import get_agent
from smartcat.api.models import ChatRequest

router = APIRouter()


@router.post("/chat")
async def chat(req: ChatRequest):
    """Stream agent reasoning as SSE events."""
    agent = await get_agent()
    session_id = req.session_id or str(uuid.uuid4())

    async def event_stream():
        async for event in agent.chat_stream(req.message, session_id=session_id):
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Session-Id": session_id,
        },
    )


@router.get("/health")
async def health():
    return {"status": "ok", "service": "smartcat"}

"""Text chat endpoint with SSE streaming + async task fallback."""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Optional

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from smartcat.api.deps import get_agent
from smartcat.api.models import ChatRequest

router = APIRouter()

# In-memory task store for async mode
_tasks: dict[str, dict] = {}


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


@router.post("/chat/async")
async def chat_async(req: ChatRequest):
    """Submit question, get task_id. Poll /chat/result/{task_id} for answer.

    For mobile / unreliable connections where SSE may break.
    """
    agent = await get_agent()
    session_id = req.session_id or str(uuid.uuid4())
    task_id = str(uuid.uuid4())

    _tasks[task_id] = {
        "status": "running",
        "session_id": session_id,
        "question": req.message,
        "steps": [],
        "answer": "",
    }

    async def run_task():
        try:
            async for event in agent.chat_stream(req.message, session_id=session_id):
                etype = event.get("event")
                if etype == "step_start":
                    _tasks[task_id]["steps"].append({
                        "step": event["step"],
                        "tools": [],
                        "thinking": "",
                    })
                elif etype == "token":
                    text = event.get("text", "")
                    if _tasks[task_id]["steps"]:
                        _tasks[task_id]["steps"][-1]["thinking"] += text
                elif etype == "tool_call":
                    if _tasks[task_id]["steps"]:
                        _tasks[task_id]["steps"][-1]["tools"].append(event.get("tool", ""))
                elif etype == "done":
                    # Extract answer from last step thinking
                    full = ""
                    for s in _tasks[task_id]["steps"]:
                        full += s["thinking"]
                    import re
                    match = re.search(r"Answer:\s*(.*)", full, re.DOTALL | re.IGNORECASE)
                    if match:
                        _tasks[task_id]["answer"] = match.group(1).strip()
                    else:
                        _tasks[task_id]["answer"] = full

            _tasks[task_id]["status"] = "done"
        except Exception as e:
            _tasks[task_id]["status"] = "error"
            _tasks[task_id]["answer"] = str(e)

    asyncio.create_task(run_task())

    return {"task_id": task_id, "session_id": session_id, "status": "running"}


@router.get("/chat/result/{task_id}")
async def chat_result(task_id: str):
    """Poll for async task result."""
    task = _tasks.get(task_id)
    if not task:
        return {"status": "not_found"}

    # Always return current steps progress
    steps_summary = []
    for s in task["steps"]:
        steps_summary.append({
            "step": s["step"],
            "tools": s["tools"],
            "thinking": s["thinking"] if s["thinking"] else "",
        })

    result = {
        "status": task["status"],
        "question": task["question"],
        "steps_count": len(task["steps"]),
        "steps": steps_summary,
    }

    if task["status"] == "done" or task["status"] == "error":
        result["answer"] = task["answer"]
        # Clean up after retrieval (keep for 5 min)
        asyncio.get_event_loop().call_later(300, lambda: _tasks.pop(task_id, None))

    return result


@router.get("/health")
async def health():
    return {"status": "ok", "service": "smartcat"}

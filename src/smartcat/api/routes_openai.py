"""OpenAI-compatible /v1/chat/completions endpoint for Agora ConvoAI."""

from __future__ import annotations

import json
import re
import uuid

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from smartcat.api.deps import get_agent
from smartcat.api.models import OpenAIChatRequest

router = APIRouter()


def _make_chunk(content: str, finish_reason: str | None = None) -> str:
    """Format a single SSE chunk in OpenAI streaming format."""
    chunk = {
        "choices": [{
            "index": 0,
            "delta": {"content": content} if content else {},
            "finish_reason": finish_reason,
        }]
    }
    return f"data: {json.dumps(chunk)}\n\n"


@router.post("/v1/chat/completions")
async def chat_completions(req: OpenAIChatRequest):
    """Agora-compatible streaming endpoint.

    Agora ConvoAI sends OpenAI-format requests.
    We run the SmartCat agent loop and stream back the final answer
    as OpenAI delta chunks.
    """
    agent = await get_agent()

    # Extract last user message as query
    query = ""
    for msg in reversed(req.messages):
        if msg.role == "user":
            query = msg.content
            break

    if not query:
        async def empty():
            yield _make_chunk("I didn't receive a question.", "stop")
            yield "data: [DONE]\n\n"
        return StreamingResponse(empty(), media_type="text/event-stream")

    session_id = str(uuid.uuid4())
    sent_filler = False

    async def stream():
        nonlocal sent_filler
        answer_started = False

        async for event in agent.chat_stream(query, session_id=session_id):
            etype = event.get("event")

            if etype == "tool_call" and not sent_filler:
                # Send brief filler so Agora TTS has something to say
                yield _make_chunk("Let me search for that... ")
                sent_filler = True

            elif etype == "done":
                yield _make_chunk("", "stop")
                break

            elif etype == "token":
                text = event.get("text", "")
                # Only stream answer tokens (after "Answer:" marker)
                if "Answer:" in text:
                    answer_started = True
                    # Stream text after "Answer:"
                    after = text.split("Answer:", 1)[1]
                    if after.strip():
                        yield _make_chunk(after)
                elif answer_started:
                    yield _make_chunk(text)

        yield "data: [DONE]\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream")

"""Agora ConvoAI voice session lifecycle endpoints."""

from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException

from smartcat.api.models import AgoraStartRequest, AgoraStopRequest

router = APIRouter()


@router.post("/start")
async def start_agent(req: AgoraStartRequest):
    """Start an Agora ConvoAI voice agent session.

    Requires AGORA_APP_ID and AGORA_APP_CERTIFICATE env vars.
    This is a placeholder — full Agora token generation and agent
    registration requires the agora-token package.
    """
    app_id = os.environ.get("AGORA_APP_ID")
    app_cert = os.environ.get("AGORA_APP_CERTIFICATE")

    if not app_id or not app_cert:
        raise HTTPException(
            status_code=503,
            detail="Agora voice not configured. Set AGORA_APP_ID and AGORA_APP_CERTIFICATE.",
        )

    # TODO: implement full Agora token generation and ConvoAI agent registration
    # See codex_x/prototype/app/api/start-agent/route.ts for reference
    return {
        "status": "not_implemented",
        "message": "Agora voice integration requires token generation setup. "
                   "Text chat is available at /api/chat.",
    }


@router.post("/stop")
async def stop_agent(req: AgoraStopRequest):
    """Stop an Agora ConvoAI voice agent session."""
    # TODO: implement Agora agent leave API call
    return {"status": "not_implemented", "agent_id": req.agent_id}

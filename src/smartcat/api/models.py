"""Pydantic models for API requests/responses."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None


class OpenAIChatMessage(BaseModel):
    role: str
    content: str


class OpenAIChatRequest(BaseModel):
    messages: list[OpenAIChatMessage]
    model: str = "smartcat"
    stream: bool = True
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None


class AgoraStartRequest(BaseModel):
    prompt: Optional[str] = None
    greeting: Optional[str] = None


class AgoraStopRequest(BaseModel):
    agent_id: str

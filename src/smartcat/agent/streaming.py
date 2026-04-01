"""Async streaming ReAct agent for web interface.

Mirrors ReactAgent logic but uses httpx async streaming for token-by-token
output and yields structured events for SSE endpoints.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import AsyncGenerator, Optional

import httpx
import structlog

from smartcat.agent.tools import AgentTools
from smartcat.config import LLM_SERVER_URL, AGENT_MAX_STEPS

log = structlog.get_logger()

SYSTEM_PROMPT = """/no_think
You are SmartCat, an AI assistant specialized in searching and analyzing email correspondence.
You have access to the following tools to find information:

{tool_descriptions}

## How to use tools

To use a tool, respond with a JSON block in this exact format:
```tool
{{"tool": "tool_name", "args": {{"param1": "value1", "param2": "value2"}}}}
```

## Rules
1. Think step by step about what information you need.
2. Use tools to find specific emails, threads, or entities.
3. After getting tool results, analyze them and decide if you need more information.
4. When you have enough information, provide a final answer with specific citations (Message-ID, date, sender).
5. Maximum {max_steps} tool calls per question. Use them wisely.
6. ALWAYS cite your sources with Message-ID or email_id and date — even when the answer comes from pre-computed QA pairs in search results. Use get_email tool to retrieve the original email for proper citation.
7. If you cannot find the answer, say so clearly.
8. You can reason and provide analysis beyond what's in the emails, but clearly distinguish between facts from emails and your own reasoning.
9. If search results contain Q/A pairs (lines starting with "Q:" and "A:"), use them as hints but ALWAYS verify by fetching the original email with get_email for accurate citation.
10. The email corpus is in English. ALWAYS translate non-English queries to English when calling tools (search_emails, search_by_participant, etc.). Respond to the user in their language.

## Response format
Think out loud, then either call a tool or give your final answer.
Prefix your reasoning with "Thinking:" and your final answer with "Answer:".
"""

_TOOL_CALL_PATTERN = re.compile(
    r"```tool\s*\n?\s*(\{.*?\})\s*\n?\s*```",
    re.DOTALL,
)


class AsyncReactAgent:
    """Async streaming ReAct agent for web endpoints."""

    def __init__(
        self,
        tools: AgentTools,
        llm_url: str = LLM_SERVER_URL,
        max_steps: int = AGENT_MAX_STEPS,
        temperature: float = 0.3,
        max_tokens: int = 2048,
    ):
        self.tools = tools
        self.llm_url = llm_url.rstrip("/")
        self.max_steps = max_steps
        self.temperature = temperature
        self.max_tokens = max_tokens
        self._sessions: dict[str, list[dict]] = {}

    def _build_system_prompt(self) -> str:
        tool_descs = self.tools.get_tool_descriptions()
        formatted = json.dumps(tool_descs, indent=2)
        return SYSTEM_PROMPT.format(
            tool_descriptions=formatted,
            max_steps=self.max_steps,
        )

    def _get_history(self, session_id: str) -> list[dict]:
        if session_id not in self._sessions:
            self._sessions[session_id] = []
        return self._sessions[session_id]

    async def chat_stream(
        self, query: str, session_id: str = "default"
    ) -> AsyncGenerator[dict, None]:
        """Stream agent reasoning as events.

        Yields dicts with 'event' key:
            step_start, token, tool_call, tool_result, answer_token, done, error
        """
        history = self._get_history(session_id)
        messages = [{"role": "system", "content": self._build_system_prompt()}]
        for h in history[-10:]:
            messages.append(h)
        messages.append({"role": "user", "content": query})

        final_answer = ""

        log.info("agent.web.start", query=query[:60], session=session_id)

        for step in range(self.max_steps):
            log.info("agent.web.step", step=step + 1, max=self.max_steps)
            yield {"event": "step_start", "step": step + 1, "max_steps": self.max_steps}

            # Stream LLM response
            full_response = ""
            in_think = False
            async for chunk_text in self._stream_llm(messages):
                # Filter out <think> blocks from Qwen3
                clean = chunk_text.replace("<think>", "").replace("</think>", "")
                full_response += clean
                if clean.strip():
                    yield {"event": "token", "text": clean}

            # Check for tool call
            tool_call = self._extract_tool_call(full_response)

            if tool_call is None:
                # Final answer
                answer_match = re.search(r"Answer:\s*(.*)", full_response, re.DOTALL | re.IGNORECASE)
                final_answer = answer_match.group(1).strip() if answer_match else full_response
                log.info("agent.web.done", steps=step + 1, answer_len=len(final_answer))
                yield {"event": "done", "steps_used": step + 1}
                break

            tool_name, tool_args = tool_call
            log.info("agent.web.tool_call", tool=tool_name, args=tool_args)
            yield {"event": "tool_call", "tool": tool_name, "args": tool_args}

            # Execute tool in thread (synchronous tools)
            try:
                tool_result = await asyncio.to_thread(self.tools.execute, tool_name, tool_args)
            except Exception as e:
                tool_result = f"Error: {e}"

            preview = tool_result[:300] + "..." if len(tool_result) > 300 else tool_result
            log.info("agent.web.tool_result", tool=tool_name, result_len=len(tool_result))
            yield {"event": "tool_result", "tool": tool_name, "preview": preview}

            # Add to messages for next step
            messages.append({"role": "assistant", "content": full_response})
            messages.append({"role": "user", "content": f"Tool result for {tool_name}:\n{tool_result}"})
        else:
            final_answer = full_response
            yield {"event": "done", "steps_used": self.max_steps}

        # Save to session history
        history.append({"role": "user", "content": query})
        history.append({"role": "assistant", "content": final_answer})

    async def _stream_llm(self, messages: list[dict]) -> AsyncGenerator[str, None]:
        """Stream tokens from llama-server using httpx."""
        payload = {
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "stream": True,
        }

        async with httpx.AsyncClient(timeout=httpx.Timeout(600.0)) as client:
            try:
                async with client.stream(
                    "POST",
                    f"{self.llm_url}/v1/chat/completions",
                    json=payload,
                ) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if not line.startswith("data: "):
                            continue
                        data = line[6:].strip()
                        if data == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data)
                            delta = chunk.get("choices", [{}])[0].get("delta", {})
                            content = delta.get("content") or delta.get("reasoning_content") or ""
                            if content:
                                yield content
                        except json.JSONDecodeError:
                            continue
            except httpx.ConnectError:
                yield "Error: Cannot connect to LLM server. Is llama-server running?"
            except Exception as e:
                yield f"Error: {e}"

    def _extract_tool_call(self, text: str) -> Optional[tuple[str, dict]]:
        match = _TOOL_CALL_PATTERN.search(text)
        if not match:
            return None
        try:
            call = json.loads(match.group(1))
            return call.get("tool", ""), call.get("args", {})
        except json.JSONDecodeError:
            return None

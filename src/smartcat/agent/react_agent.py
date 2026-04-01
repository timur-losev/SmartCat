"""ReAct agent loop for email search with reasoning.

Uses llama.cpp server (OpenAI-compatible API) for LLM inference.
"""

from __future__ import annotations

import json
import re
from typing import Optional

import requests
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
10. The email corpus is in English. ALWAYS translate non-English queries to English when calling tools (search_emails, search_by_participant, etc.). ALWAYS respond to the user in their language. If the user writes in Russian, your entire Answer MUST be in Russian. If in English, answer in English.

## Response format
Think out loud, then either call a tool or give your final answer.
Prefix your reasoning with "Thinking:" and your final answer with "Answer:".
"""

_TOOL_CALL_PATTERN = re.compile(
    r"```tool\s*\n?\s*(\{.*?\})\s*\n?\s*```",
    re.DOTALL,
)


class ReactAgent:
    """ReAct-style agent with tool use for email search."""

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
        self._history: list[dict] = []  # conversation history across turns

    def _build_system_prompt(self) -> str:
        tool_descs = self.tools.get_tool_descriptions()
        formatted = json.dumps(tool_descs, indent=2)
        return SYSTEM_PROMPT.format(
            tool_descriptions=formatted,
            max_steps=self.max_steps,
        )

    def _call_llm(self, messages: list[dict]) -> str:
        """Call llama.cpp server (OpenAI-compatible API)."""
        payload = {
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "stop": ["```\n\nUser:", "```\n\nHuman:"],
        }
        try:
            resp = requests.post(
                f"{self.llm_url}/v1/chat/completions",
                json=payload,
                timeout=600,
            )
            resp.raise_for_status()
            data = resp.json()
            msg = data["choices"][0]["message"]
            # Qwen3 with thinking mode: content may be empty, text in reasoning_content
            content = msg.get("content") or ""
            reasoning = msg.get("reasoning_content") or ""
            if not content and reasoning:
                # Extract useful text from thinking block
                content = reasoning
            return content
        except requests.ConnectionError:
            return "Error: Cannot connect to LLM server. Is llama-server running?"
        except Exception as e:
            return f"Error calling LLM: {e}"

    def _extract_tool_call(self, text: str) -> Optional[tuple[str, dict]]:
        """Extract tool call from LLM response."""
        match = _TOOL_CALL_PATTERN.search(text)
        if not match:
            return None
        try:
            call = json.loads(match.group(1))
            tool_name = call.get("tool", "")
            args = call.get("args", {})
            return tool_name, args
        except json.JSONDecodeError:
            return None

    def chat(self, user_query: str, stream_callback=None) -> str:
        """Run the ReAct loop for a user query.

        Args:
            user_query: User's question about the email corpus.
            stream_callback: Optional callback(text) for streaming output.

        Returns:
            Final answer string.
        """
        # Build messages with conversation history for multi-turn context
        messages = [{"role": "system", "content": self._build_system_prompt()}]
        # Add previous turns (keep last 10 to avoid context overflow)
        for h in self._history[-10:]:
            messages.append(h)
        messages.append({"role": "user", "content": user_query})

        full_response = []

        for step in range(self.max_steps):
            log.info("agent.step", step=step + 1, max=self.max_steps)

            # Get LLM response
            response = self._call_llm(messages)
            full_response.append(response)

            if stream_callback:
                stream_callback(f"\n--- Step {step + 1} ---\n{response}\n")

            # Check for tool call
            tool_call = self._extract_tool_call(response)
            if tool_call is None:
                # No tool call → this is the final answer
                break

            tool_name, tool_args = tool_call
            log.info("agent.tool_call", tool=tool_name, args=tool_args)

            # Execute tool
            tool_result = self.tools.execute(tool_name, tool_args)

            if stream_callback:
                stream_callback(f"\n[Tool: {tool_name}]\n{tool_result[:500]}...\n")

            # Add to conversation
            messages.append({"role": "assistant", "content": response})
            messages.append({
                "role": "user",
                "content": f"Tool result for {tool_name}:\n{tool_result}",
            })

        # Extract final answer from the last response
        last = full_response[-1] if full_response else ""
        # Try to find explicit "Answer:" section
        answer_match = re.search(r"Answer:\s*(.*)", last, re.DOTALL | re.IGNORECASE)
        final = answer_match.group(1).strip() if answer_match else last

        # Save to conversation history for multi-turn context
        self._history.append({"role": "user", "content": user_query})
        self._history.append({"role": "assistant", "content": final})

        return final

    def chat_no_llm(self, user_query: str) -> str:
        """Simplified search without LLM — direct tool execution for testing.

        Uses keyword matching to decide which tool to call.
        """
        query_lower = user_query.lower()

        # Direct search
        result = self.tools.execute("search_emails", {"query": user_query, "max_results": 5})
        return f"Search results for: {user_query}\n\n{result}"

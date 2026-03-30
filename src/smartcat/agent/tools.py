"""Agent tools for email search and retrieval.

Each tool is a callable that returns structured results for the ReAct agent.
"""

from __future__ import annotations

from typing import Any, Optional

from smartcat.retrieval.hybrid_search import HybridSearcher
from smartcat.retrieval.reranker import Reranker
from smartcat.storage.sqlite_store import EmailStore


class AgentTools:
    """Collection of tools available to the AI agent."""

    def __init__(
        self,
        searcher: HybridSearcher,
        reranker: Optional[Reranker],
        store: EmailStore,
    ):
        self.searcher = searcher
        self.reranker = reranker
        self.store = store

    def get_tool_descriptions(self) -> list[dict]:
        """Return tool schemas for the agent's system prompt."""
        return [
            {
                "name": "search_emails",
                "description": "Search emails using natural language query. Returns top results ranked by relevance.",
                "parameters": {
                    "query": {"type": "string", "description": "Natural language search query"},
                    "max_results": {"type": "integer", "description": "Max results (default 10)", "default": 10},
                },
                "required": ["query"],
            },
            {
                "name": "search_by_participant",
                "description": "Find emails involving a specific person by name or email address.",
                "parameters": {
                    "name_or_email": {"type": "string", "description": "Person name or email address"},
                    "limit": {"type": "integer", "description": "Max results (default 20)", "default": 20},
                },
                "required": ["name_or_email"],
            },
            {
                "name": "search_by_date_range",
                "description": "Find emails within a date range. Dates in ISO format (YYYY-MM-DD).",
                "parameters": {
                    "start": {"type": "string", "description": "Start date (YYYY-MM-DD)"},
                    "end": {"type": "string", "description": "End date (YYYY-MM-DD)"},
                    "query": {"type": "string", "description": "Optional keyword query", "default": None},
                },
                "required": ["start", "end"],
            },
            {
                "name": "search_entities",
                "description": "Search for emails containing specific entities (monetary amounts, dates, document references, deal IDs).",
                "parameters": {
                    "entity_type": {"type": "string", "enum": ["monetary", "date_ref", "document_ref", "deal_id"]},
                    "value_pattern": {"type": "string", "description": "Value to search for (partial match)"},
                },
                "required": ["entity_type", "value_pattern"],
            },
            {
                "name": "get_email",
                "description": "Get the full content of a specific email by its email_id (integer) or message_id (string).",
                "parameters": {
                    "email_id": {"type": "integer", "description": "Internal email ID (preferred)"},
                    "message_id": {"type": "string", "description": "External Message-ID (fallback)"},
                },
                "required": [],
            },
            {
                "name": "get_thread",
                "description": "Get all emails in a thread, ordered chronologically.",
                "parameters": {
                    "thread_id": {"type": "string", "description": "Thread ID"},
                },
                "required": ["thread_id"],
            },
            {
                "name": "get_email_stats",
                "description": "Get aggregate statistics about emails. Optional filters: from_address, date_start, date_end.",
                "parameters": {
                    "from_address": {"type": "string", "description": "Filter by sender", "default": None},
                    "date_start": {"type": "string", "description": "Start date filter", "default": None},
                    "date_end": {"type": "string", "description": "End date filter", "default": None},
                },
                "required": [],
            },
        ]

    def execute(self, tool_name: str, args: dict[str, Any]) -> str:
        """Execute a tool and return formatted string result."""
        try:
            handler = getattr(self, f"_tool_{tool_name}", None)
            if handler is None:
                return f"Error: Unknown tool '{tool_name}'"
            return handler(**args)
        except Exception as e:
            return f"Error executing {tool_name}: {type(e).__name__}: {e}"

    def _tool_search_emails(self, query: str, max_results: int = 10) -> str:
        results = self.searcher.search(query, top_n=30)
        if self.reranker and results:
            results = self.reranker.rerank(query, results, top_k=max_results)
        else:
            results = results[:max_results]

        if not results:
            return "No emails found matching the query."

        lines = [f"Found {len(results)} relevant emails:\n"]
        for i, r in enumerate(results, 1):
            score = r.get("rerank_score", r.get("rrf_score", 0))
            body_preview = (r.get("body_text", "")[:150] + "...") if r.get("body_text") else ""
            entry = (
                f"{i}. [{r.get('date_sent', 'N/A')[:10]}] "
                f"From: {r.get('from_name') or r.get('from_address', 'Unknown')} | "
                f"Subject: {r.get('subject', '(no subject)')} | "
                f"Score: {score:.3f}\n"
                f"   ID: {r['email_id']} (Message-ID: {r.get('message_id', 'N/A')})\n"
                f"   Thread: {r.get('thread_id', 'N/A')}\n"
                f"   Preview: {body_preview}\n"
            )
            # Auto-enrich QA matches with full email citation
            if r.get("_qa_question"):
                email_detail = self.store.get_email(r["email_id"])
                msg_id = email_detail.get("message_id", "N/A") if email_detail else "N/A"
                date = email_detail.get("date_sent", "N/A")[:10] if email_detail else "N/A"
                sender = (email_detail.get("from_name") or email_detail.get("from_address", "")) if email_detail else ""
                entry += (
                    f"   [QA Match] Q: {r['_qa_question']}\n"
                    f"   [QA Match] A: {r.get('_qa_answer', '')}\n"
                    f"   [Source] Message-ID: {msg_id} | Date: {date} | From: {sender}\n"
                    f"   ** Cite this email in your answer **\n"
                )
            lines.append(entry)
        return "\n".join(lines)

    def _tool_search_by_participant(self, name_or_email: str, limit: int = 20) -> str:
        results = self.store.search_by_participant(name_or_email, limit=limit)
        if not results:
            return f"No emails found involving '{name_or_email}'."

        lines = [f"Found {len(results)} emails involving '{name_or_email}':\n"]
        for i, r in enumerate(results[:limit], 1):
            lines.append(
                f"{i}. [{r.get('date_sent', 'N/A')[:10]}] "
                f"From: {r.get('from_name') or r.get('from_address', 'Unknown')} | "
                f"Subject: {r.get('subject', '(no subject)')}\n"
                f"   ID: {r['email_id']} (Message-ID: {r.get('message_id', 'N/A')})\n"
            )
        return "\n".join(lines)

    def _tool_search_by_date_range(
        self, start: str, end: str, query: Optional[str] = None, limit: int = 20
    ) -> str:
        results = self.store.search_by_date_range(start, end, query=query, limit=limit)
        if not results:
            return f"No emails found between {start} and {end}."

        lines = [f"Found {len(results)} emails between {start} and {end}:\n"]
        for i, r in enumerate(results, 1):
            lines.append(
                f"{i}. [{r.get('date_sent', 'N/A')[:10]}] "
                f"From: {r.get('from_name') or r.get('from_address', 'Unknown')} | "
                f"Subject: {r.get('subject', '(no subject)')}\n"
                f"   ID: {r['email_id']} (Message-ID: {r.get('message_id', 'N/A')})\n"
            )
        return "\n".join(lines)

    def _tool_search_entities(
        self, entity_type: str, value_pattern: str, limit: int = 20
    ) -> str:
        results = self.store.search_entities(entity_type, value_pattern, limit=limit)
        if not results:
            return f"No emails found with {entity_type} matching '{value_pattern}'."

        lines = [f"Found {len(results)} emails with {entity_type} matching '{value_pattern}':\n"]
        for i, r in enumerate(results, 1):
            lines.append(
                f"{i}. [{r.get('date_sent', 'N/A')[:10]}] "
                f"From: {r.get('from_address', 'Unknown')} | "
                f"Subject: {r.get('subject', '(no subject)')}\n"
                f"   Value: {r.get('entity_value', '')}\n"
                f"   Context: {r.get('context', '')[:200]}\n"
                f"   ID: {r['email_id']} (Message-ID: {r.get('message_id', 'N/A')})\n"
            )
        return "\n".join(lines)

    def _tool_get_email(
        self, email_id: Optional[int] = None, message_id: Optional[str] = None
    ) -> str:
        if email_id is not None:
            email_data = self.store.get_email(email_id)
        elif message_id is not None:
            email_data = self.store.get_email_by_message_id(message_id)
        else:
            return "Error: provide either email_id or message_id"

        if not email_data:
            return f"Email not found: email_id={email_id}, message_id={message_id}"

        body = email_data.get("body_text", "")
        if len(body) > 2000:
            body = body[:2000] + "\n... [truncated]"

        confidence = email_data.get("thread_confidence")
        conf_str = f" (confidence: {confidence:.1f})" if confidence is not None else ""

        return (
            f"Email-ID: {email_data['email_id']}\n"
            f"Message-ID: {email_data.get('message_id', 'N/A')}\n"
            f"Date: {email_data.get('date_sent', 'N/A')}\n"
            f"From: {email_data.get('from_name', '')} <{email_data.get('from_address', '')}>\n"
            f"Subject: {email_data.get('subject', '(no subject)')}\n"
            f"Thread: {email_data.get('thread_id', 'N/A')}{conf_str}\n"
            f"Attachments: {'Yes' if email_data.get('has_attachments') else 'No'}\n"
            f"\n--- Body ---\n{body}"
        )

    def _tool_get_thread(self, thread_id: str) -> str:
        emails = self.store.get_thread(thread_id)
        if not emails:
            return f"Thread not found: {thread_id}"

        lines = [f"Thread {thread_id} ({len(emails)} messages):\n"]
        for i, e in enumerate(emails, 1):
            body_preview = (e.get("body_text", "")[:200] + "...") if e.get("body_text") else ""
            conf = e.get("thread_confidence")
            conf_str = f" [confidence: {conf:.1f}]" if conf is not None else ""
            lines.append(
                f"--- Message {i}{conf_str} ---\n"
                f"Date: {e.get('date_sent', 'N/A')}\n"
                f"From: {e.get('from_name', '')} <{e.get('from_address', '')}>\n"
                f"Subject: {e.get('subject', '')}\n"
                f"Email-ID: {e['email_id']}\n"
                f"Preview: {body_preview}\n"
            )
        return "\n".join(lines)

    def _tool_get_email_stats(
        self,
        from_address: Optional[str] = None,
        date_start: Optional[str] = None,
        date_end: Optional[str] = None,
    ) -> str:
        filters = {}
        if from_address:
            filters["from_address"] = from_address
        if date_start:
            filters["date_start"] = date_start
        if date_end:
            filters["date_end"] = date_end

        stats = self.store.get_stats(filters or None)
        return (
            f"Email Statistics:\n"
            f"  Total emails: {stats.get('total_emails', 0):,}\n"
            f"  Date range: {stats.get('earliest', 'N/A')} to {stats.get('latest', 'N/A')}\n"
            f"  Unique senders: {stats.get('unique_senders', 0):,}\n"
            f"  With attachments: {stats.get('with_attachments', 0):,}\n"
            f"  Average length: {stats.get('avg_length', 0):.0f} chars\n"
        )

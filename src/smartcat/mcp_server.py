"""MCP Server for SmartCat — exposes email search tools to Claude Code.

Usage:
    python -m smartcat.mcp_server [--db path/to/smartcat.db]

Configure in Claude Code settings:
    {
        "mcpServers": {
            "smartcat": {
                "command": "python",
                "args": ["-m", "smartcat.mcp_server"],
                "cwd": "/path/to/SmartCat",
                "env": {"PYTHONPATH": "src"}
            }
        }
    }
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from mcp.server.fastmcp import FastMCP

# Ensure src is on path when run as module
src_dir = Path(__file__).resolve().parent.parent
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))

from smartcat.config import SQLITE_DB_PATH, QDRANT_HOST, QDRANT_PORT
from smartcat.embedding.embedder import Embedder
from smartcat.retrieval.hybrid_search import HybridSearcher
from smartcat.retrieval.reranker import Reranker
from smartcat.storage.qdrant_store import QdrantStore
from smartcat.storage.sqlite_store import EmailStore
from smartcat.agent.tools import AgentTools

# --- Initialize backend ---

_tools: AgentTools | None = None


def _get_tools(db_path: Path | None = None) -> AgentTools:
    global _tools
    if _tools is not None:
        return _tools

    db = db_path or SQLITE_DB_PATH
    store = EmailStore(db)
    store.connect()

    embedder = Embedder(device="cpu")
    qdrant = QdrantStore(host=QDRANT_HOST, port=QDRANT_PORT)
    searcher = HybridSearcher(embedder, qdrant, store)
    reranker = Reranker(device="cpu")

    _tools = AgentTools(searcher, reranker, store)
    return _tools


# --- MCP Server ---

mcp = FastMCP(
    "smartcat",
    instructions=(
        "SmartCat — AI email search over the Enron corpus (245K emails). "
        "Use these tools to find emails, people, threads, and statistics. "
        "The corpus is in English. Always translate non-English queries to English when calling tools. "
        "Respond to the user in their language."
    ),
)


@mcp.tool()
def search_emails(query: str, max_results: int = 10) -> str:
    """Search emails using natural language query. Returns top results ranked by relevance.

    Args:
        query: Natural language search query (in English for best results).
        max_results: Maximum number of results to return (default 10).
    """
    return _get_tools().execute("search_emails", {
        "query": query, "max_results": max_results,
    })


@mcp.tool()
def search_by_participant(name_or_email: str, limit: int = 20) -> str:
    """Find emails involving a specific person by name or email address.

    Args:
        name_or_email: Person name (e.g. "Jeff Dasovich") or email address.
        limit: Maximum number of results (default 20).
    """
    return _get_tools().execute("search_by_participant", {
        "name_or_email": name_or_email, "limit": limit,
    })


@mcp.tool()
def search_by_date_range(start: str, end: str, query: str | None = None) -> str:
    """Find emails within a date range. Dates in ISO format (YYYY-MM-DD).

    Args:
        start: Start date (YYYY-MM-DD).
        end: End date (YYYY-MM-DD).
        query: Optional keyword query to filter results.
    """
    return _get_tools().execute("search_by_date_range", {
        "start": start, "end": end, "query": query,
    })


@mcp.tool()
def search_entities(entity_type: str, value_pattern: str) -> str:
    """Search for emails containing specific entities.

    Args:
        entity_type: One of "monetary", "date_ref", "document_ref", "deal_id".
        value_pattern: Value to search for (partial match).
    """
    return _get_tools().execute("search_entities", {
        "entity_type": entity_type, "value_pattern": value_pattern,
    })


@mcp.tool()
def get_email(email_id: int | None = None, message_id: str | None = None) -> str:
    """Get the full content of a specific email by its ID.

    Args:
        email_id: Internal email ID (integer, preferred).
        message_id: External Message-ID string (fallback).
    """
    args = {}
    if email_id is not None:
        args["email_id"] = email_id
    if message_id is not None:
        args["message_id"] = message_id
    return _get_tools().execute("get_email", args)


@mcp.tool()
def get_thread(thread_id: str) -> str:
    """Get all emails in a thread, ordered chronologically.

    Args:
        thread_id: Thread ID string.
    """
    return _get_tools().execute("get_thread", {"thread_id": thread_id})


@mcp.tool()
def get_email_stats(
    from_address: str | None = None,
    date_start: str | None = None,
    date_end: str | None = None,
) -> str:
    """Get aggregate statistics about emails.

    Args:
        from_address: Filter by sender email address.
        date_start: Start date filter (YYYY-MM-DD).
        date_end: End date filter (YYYY-MM-DD).
    """
    args = {}
    if from_address:
        args["from_address"] = from_address
    if date_start:
        args["date_start"] = date_start
    if date_end:
        args["date_end"] = date_end
    return _get_tools().execute("get_email_stats", args)


@mcp.tool()
def get_top_senders(limit: int = 20) -> str:
    """Get the most frequent email senders ranked by count.

    Args:
        limit: Number of top senders to return (default 20).
    """
    return _get_tools().execute("get_top_senders", {"limit": limit})


def main():
    parser = argparse.ArgumentParser(description="SmartCat MCP Server")
    parser.add_argument("--db", type=Path, default=None,
                        help="Path to SQLite database (default from config)")
    args = parser.parse_args()

    if args.db:
        _get_tools(args.db)

    mcp.run()


if __name__ == "__main__":
    main()

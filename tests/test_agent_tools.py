"""Tests for agent tools."""

import pytest
from pathlib import Path
from smartcat.parsing.mime_parser import parse_email_file
from smartcat.storage.sqlite_store import EmailStore
from smartcat.agent.tools import AgentTools
from tests.conftest import skip_no_maildir, MAILDIR


@skip_no_maildir
class TestAgentTools:
    @pytest.fixture
    def tools_with_data(self, tmp_store):
        """Create AgentTools with some test data (no vector search)."""
        # Insert a few emails
        last_email_id = None
        for f in sorted((MAILDIR / "allen-p" / "inbox").iterdir())[:5]:
            parsed = parse_email_file(f)
            email_id, _ = tmp_store.insert_email(parsed)
            last_email_id = email_id

        # Insert entities manually
        conn = tmp_store.connect()
        conn.execute(
            "INSERT INTO entities (email_id, entity_type, entity_value, context) VALUES (?, ?, ?, ?)",
            (last_email_id, "monetary", "$5,000", "The total was $5,000 for the project."),
        )
        conn.commit()

        # Create tools without searcher/reranker (test SQLite-based tools only)
        tools = AgentTools(searcher=None, reranker=None, store=tmp_store)
        return tools

    def test_get_tool_descriptions(self, tools_with_data):
        descs = tools_with_data.get_tool_descriptions()
        assert len(descs) == 7
        names = {d["name"] for d in descs}
        assert "search_emails" in names
        assert "get_email" in names
        assert "get_thread" in names

    def test_search_by_participant(self, tools_with_data):
        result = tools_with_data.execute("search_by_participant", {"name_or_email": "allen"})
        assert "Found" in result or "No emails" in result

    def test_get_email_not_found(self, tools_with_data):
        result = tools_with_data.execute("get_email", {"message_id": "nonexistent"})
        assert "not found" in result.lower()

    def test_get_email_stats(self, tools_with_data):
        result = tools_with_data.execute("get_email_stats", {})
        assert "Total emails:" in result

    def test_search_entities(self, tools_with_data):
        result = tools_with_data.execute(
            "search_entities",
            {"entity_type": "monetary", "value_pattern": "5,000"},
        )
        assert "$5,000" in result or "No emails" in result

    def test_unknown_tool(self, tools_with_data):
        result = tools_with_data.execute("nonexistent_tool", {})
        assert "Error" in result or "Unknown" in result


class TestReactAgentToolParsing:
    def test_extract_tool_call(self):
        from smartcat.agent.react_agent import _TOOL_CALL_PATTERN
        text = '''Thinking: I need to search for emails.
```tool
{"tool": "search_emails", "args": {"query": "gas trading"}}
```'''
        match = _TOOL_CALL_PATTERN.search(text)
        assert match is not None
        import json
        call = json.loads(match.group(1))
        assert call["tool"] == "search_emails"
        assert call["args"]["query"] == "gas trading"

    def test_no_tool_call(self):
        from smartcat.agent.react_agent import _TOOL_CALL_PATTERN
        text = "Answer: The total amount was $5,000."
        match = _TOOL_CALL_PATTERN.search(text)
        assert match is None

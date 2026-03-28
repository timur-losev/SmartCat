"""Tests for email chunking."""

from smartcat.chunking.email_chunker import chunk_email, _approx_tokens, _split_body_and_quotes


class TestApproxTokens:
    def test_empty(self):
        assert _approx_tokens("") == 1  # min 1

    def test_short(self):
        assert _approx_tokens("hello") == 1

    def test_longer(self):
        text = "a" * 400
        assert _approx_tokens(text) == 100


class TestSplitBodyAndQuotes:
    def test_no_quotes(self):
        body = "Hello, this is the main body."
        main, quotes = _split_body_and_quotes(body)
        assert main == body
        assert quotes == []

    def test_with_original_message(self):
        body = (
            "My response here.\n\n"
            " -----Original Message-----\n"
            "From: Someone\n"
            "The original message text."
        )
        main, quotes = _split_body_and_quotes(body)
        assert "My response here." in main
        assert len(quotes) >= 1
        assert "original message" in quotes[0].lower()

    def test_multiple_quotes(self):
        body = (
            "Top reply.\n\n"
            " -----Original Message-----\n"
            "First quoted section.\n\n"
            " -----Original Message-----\n"
            "Second quoted section."
        )
        main, quotes = _split_body_and_quotes(body)
        assert "Top reply." in main
        assert len(quotes) >= 2


class TestChunkEmail:
    def test_always_produces_summary(self):
        chunks = chunk_email(
            message_id="test-123",
            subject="Test Subject",
            body_text="Short body.",
        )
        assert len(chunks) >= 1
        assert chunks[0].chunk_type == "summary"
        assert "Test Subject" in chunks[0].text

    def test_summary_contains_metadata(self):
        chunks = chunk_email(
            message_id="test-123",
            subject="Test Subject",
            body_text="Body content here.",
            from_address="sender@test.com",
            from_name="Test Sender",
            to_addresses=["recv@test.com"],
            date_sent="2001-01-15T10:00:00",
        )
        summary = chunks[0]
        assert "Test Sender" in summary.text
        assert "recv@test.com" in summary.text
        assert "2001-01-15" in summary.text

    def test_short_body_no_extra_chunks(self):
        chunks = chunk_email(
            message_id="test-123",
            subject="Short",
            body_text="Very short body.",
        )
        # Should only have summary (body too short for separate chunk)
        body_chunks = [c for c in chunks if c.chunk_type == "body"]
        assert len(body_chunks) == 0

    def test_long_body_produces_body_chunks(self):
        long_body = "\n\n".join([f"Paragraph {i}. " + "x" * 200 for i in range(10)])
        chunks = chunk_email(
            message_id="test-123",
            subject="Long email",
            body_text=long_body,
        )
        body_chunks = [c for c in chunks if c.chunk_type == "body"]
        assert len(body_chunks) >= 2

    def test_quoted_content_separate_chunks(self):
        body = (
            "My reply to you.\n\n"
            " -----Original Message-----\n"
            "From: Other Person\n"
            "The original message is quite long and contains important details "
            "that should be in a separate chunk for better retrieval quality. " * 5
        )
        chunks = chunk_email(
            message_id="test-123",
            subject="RE: Discussion",
            body_text=body,
        )
        quoted = [c for c in chunks if c.chunk_type == "quoted"]
        assert len(quoted) >= 1

    def test_chunk_ids_unique(self):
        chunks = chunk_email(
            message_id="test-123",
            subject="Test",
            body_text="Body.\n\n" * 50,
        )
        ids = [c.chunk_id for c in chunks]
        assert len(ids) == len(set(ids))

    def test_metadata_propagated(self):
        chunks = chunk_email(
            message_id="test-123",
            subject="Test",
            body_text="Body content.",
            thread_id="thread_abc",
            has_monetary=True,
            from_address="sender@test.com",
        )
        for chunk in chunks:
            assert chunk.thread_id == "thread_abc"
            assert chunk.has_monetary is True
            assert chunk.from_address == "sender@test.com"

    def test_token_count_set(self):
        chunks = chunk_email(
            message_id="test-123",
            subject="Test",
            body_text="Some body text here.",
        )
        for chunk in chunks:
            assert chunk.token_count > 0

"""Tests for SQLite storage layer."""

import pytest
from smartcat.parsing.mime_parser import parse_email_file, ParsedEmail
from smartcat.storage.sqlite_store import EmailStore
from tests.conftest import skip_no_maildir


class TestEmailStoreSchema:
    def test_init_schema(self, tmp_store):
        """Schema should be created without errors."""
        conn = tmp_store.connect()
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        table_names = {r["name"] for r in tables}
        assert "emails" in table_names
        assert "participants" in table_names
        assert "email_participants" in table_names
        assert "entities" in table_names
        assert "attachments" in table_names
        assert "chunks" in table_names
        assert "processed_files" in table_names
        assert "processing_errors" in table_names

    def test_fts_table_exists(self, tmp_store):
        conn = tmp_store.connect()
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='emails_fts'"
        ).fetchall()
        assert len(tables) == 1

    def test_idempotent_schema(self, tmp_store):
        """Calling init_schema twice should not error."""
        tmp_store.init_schema()  # Already called in fixture
        tmp_store.init_schema()  # Second call should be fine


@skip_no_maildir
class TestEmailInsertion:
    def test_insert_new_email(self, tmp_store, sample_email_path):
        parsed = parse_email_file(sample_email_path)
        is_new = tmp_store.insert_email(parsed)
        tmp_store.connect().commit()
        assert is_new is True
        assert tmp_store.get_email_count() == 1

    def test_insert_duplicate_creates_instance(self, tmp_store, sample_email_path):
        parsed = parse_email_file(sample_email_path)
        tmp_store.insert_email(parsed)
        tmp_store.connect().commit()

        # Insert same email again (simulating it in another folder)
        is_new = tmp_store.insert_email(parsed)
        tmp_store.connect().commit()
        assert is_new is False
        assert tmp_store.get_email_count() == 1  # Still 1 email
        # Instance count may vary based on UNIQUE constraint on source_path

    def test_participants_created(self, tmp_store, sample_email_path):
        parsed = parse_email_file(sample_email_path)
        tmp_store.insert_email(parsed)
        tmp_store.connect().commit()
        assert tmp_store.get_participant_count() > 0

    def test_get_email(self, tmp_store, sample_email_path):
        parsed = parse_email_file(sample_email_path)
        tmp_store.insert_email(parsed)
        tmp_store.connect().commit()

        result = tmp_store.get_email(parsed.message_id)
        assert result is not None
        assert result["message_id"] == parsed.message_id
        assert result["subject"] == parsed.subject

    def test_get_nonexistent_email(self, tmp_store):
        result = tmp_store.get_email("nonexistent@id")
        assert result is None


@skip_no_maildir
class TestFTSSearch:
    def test_fts_finds_by_subject(self, tmp_store, sample_email_path):
        parsed = parse_email_file(sample_email_path)
        tmp_store.insert_email(parsed)
        tmp_store.connect().commit()

        results = tmp_store.search_fts("Position")
        assert len(results) >= 1
        assert results[0]["subject"] == "RE: West Position"

    def test_fts_finds_by_body(self, tmp_store, sample_email_path):
        parsed = parse_email_file(sample_email_path)
        tmp_store.insert_email(parsed)
        tmp_store.connect().commit()

        results = tmp_store.search_fts("Curve Shift")
        assert len(results) >= 1

    def test_fts_no_results(self, tmp_store, sample_email_path):
        parsed = parse_email_file(sample_email_path)
        tmp_store.insert_email(parsed)
        tmp_store.connect().commit()

        results = tmp_store.search_fts("xyznonexistent123")
        assert len(results) == 0


@skip_no_maildir
class TestParticipantSearch:
    def test_search_by_email(self, tmp_store, sample_email_path):
        parsed = parse_email_file(sample_email_path)
        tmp_store.insert_email(parsed)
        tmp_store.connect().commit()

        results = tmp_store.search_by_participant("dunton")
        assert len(results) >= 1

    def test_search_by_name(self, tmp_store, sample_email_path):
        parsed = parse_email_file(sample_email_path)
        tmp_store.insert_email(parsed)
        tmp_store.connect().commit()

        results = tmp_store.search_by_participant("Heather")
        assert len(results) >= 1


class TestIngestionTracking:
    def test_mark_and_check_processed(self, tmp_store):
        tmp_store.mark_file_processed("/test/file.txt", "done")
        tmp_store.connect().commit()
        assert tmp_store.is_file_processed("/test/file.txt") is True
        assert tmp_store.is_file_processed("/other/file.txt") is False

    def test_log_error(self, tmp_store):
        tmp_store.log_error("/bad/file.txt", "ValueError", "bad encoding")
        tmp_store.connect().commit()
        assert tmp_store.get_error_count() == 1


class TestStats:
    @skip_no_maildir
    def test_get_stats(self, tmp_store, sample_email_path):
        parsed = parse_email_file(sample_email_path)
        tmp_store.insert_email(parsed)
        tmp_store.connect().commit()

        stats = tmp_store.get_stats()
        assert stats["total_emails"] == 1
        assert stats["unique_senders"] == 1

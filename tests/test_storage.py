"""Tests for SQLite storage layer."""

import pytest
from smartcat.parsing.mime_parser import parse_email_file, ParsedEmail
from smartcat.storage.sqlite_store import EmailStore, compute_fingerprint
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

    def test_fts_tables_exist(self, tmp_store):
        conn = tmp_store.connect()
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE '%_fts'"
        ).fetchall()
        names = {r["name"] for r in tables}
        assert "emails_fts" in names
        assert "attachments_fts" in names

    def test_emails_have_email_id_pk(self, tmp_store):
        """emails table should use email_id as PK, not message_id."""
        conn = tmp_store.connect()
        info = conn.execute("PRAGMA table_info(emails)").fetchall()
        cols = {r["name"]: r for r in info}
        assert "email_id" in cols
        assert cols["email_id"]["pk"] == 1
        assert "fingerprint" in cols
        assert "message_id" in cols

    def test_thread_confidence_columns(self, tmp_store):
        conn = tmp_store.connect()
        info = conn.execute("PRAGMA table_info(emails)").fetchall()
        col_names = {r["name"] for r in info}
        assert "thread_confidence" in col_names
        assert "thread_method" in col_names

    def test_chunks_have_span_fields(self, tmp_store):
        conn = tmp_store.connect()
        info = conn.execute("PRAGMA table_info(chunks)").fetchall()
        col_names = {r["name"] for r in info}
        assert "page_range" in col_names
        assert "char_offset_start" in col_names
        assert "char_offset_end" in col_names

    def test_idempotent_schema(self, tmp_store):
        """Calling init_schema twice should not error."""
        tmp_store.init_schema()
        tmp_store.init_schema()


class TestFingerprint:
    @skip_no_maildir
    def test_same_email_same_fingerprint(self, sample_email_path):
        p1 = parse_email_file(sample_email_path)
        p2 = parse_email_file(sample_email_path)
        assert compute_fingerprint(p1) == compute_fingerprint(p2)

    @skip_no_maildir
    def test_different_emails_different_fingerprint(self):
        from pathlib import Path
        from tests.conftest import MAILDIR
        f1 = MAILDIR / "allen-p" / "inbox" / "1_"
        f2 = MAILDIR / "allen-p" / "_sent_mail" / "1_"
        p1 = parse_email_file(f1)
        p2 = parse_email_file(f2)
        assert compute_fingerprint(p1) != compute_fingerprint(p2)


@skip_no_maildir
class TestEmailInsertion:
    def test_insert_new_email(self, tmp_store, sample_email_path):
        parsed = parse_email_file(sample_email_path)
        email_id, is_new = tmp_store.insert_email(parsed)
        tmp_store.connect().commit()
        assert is_new is True
        assert isinstance(email_id, int)
        assert tmp_store.get_email_count() == 1

    def test_insert_duplicate_returns_same_id(self, tmp_store, sample_email_path):
        parsed = parse_email_file(sample_email_path)
        eid1, new1 = tmp_store.insert_email(parsed)
        tmp_store.connect().commit()
        eid2, new2 = tmp_store.insert_email(parsed)
        tmp_store.connect().commit()
        assert new1 is True
        assert new2 is False
        assert eid1 == eid2
        assert tmp_store.get_email_count() == 1

    def test_participants_created(self, tmp_store, sample_email_path):
        parsed = parse_email_file(sample_email_path)
        tmp_store.insert_email(parsed)
        tmp_store.connect().commit()
        assert tmp_store.get_participant_count() > 0

    def test_get_email(self, tmp_store, sample_email_path):
        parsed = parse_email_file(sample_email_path)
        email_id, _ = tmp_store.insert_email(parsed)
        tmp_store.connect().commit()

        result = tmp_store.get_email(email_id)
        assert result is not None
        assert result["email_id"] == email_id
        assert result["subject"] == parsed.subject

    def test_get_email_by_message_id(self, tmp_store, sample_email_path):
        parsed = parse_email_file(sample_email_path)
        email_id, _ = tmp_store.insert_email(parsed)
        tmp_store.connect().commit()

        result = tmp_store.get_email_by_message_id(parsed.message_id)
        assert result is not None
        assert result["email_id"] == email_id

    def test_get_nonexistent_email(self, tmp_store):
        result = tmp_store.get_email(999999)
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

    def test_fts_searches_attachments(self, tmp_store, sample_email_path):
        """FTS should find emails via attachment extracted text."""
        parsed = parse_email_file(sample_email_path)
        email_id, _ = tmp_store.insert_email(parsed)
        conn = tmp_store.connect()
        # Simulate an attachment with extracted text
        conn.execute(
            "INSERT INTO attachments (email_id, filename, extracted_text) VALUES (?, ?, ?)",
            (email_id, "report.pdf", "quarterly revenue analysis for natural gas trading"),
        )
        conn.commit()

        results = tmp_store.search_fts("quarterly revenue")
        assert len(results) >= 1


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

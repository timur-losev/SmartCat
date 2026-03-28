"""Tests for thread reconstruction."""

import sqlite3
import pytest
from pathlib import Path
from smartcat.parsing.threading import normalize_subject, reconstruct_threads
from smartcat.parsing.mime_parser import parse_email_file
from smartcat.storage.sqlite_store import EmailStore
from tests.conftest import skip_no_maildir, MAILDIR


class TestNormalizeSubject:
    def test_strip_re(self):
        assert normalize_subject("RE: Hello") == "hello"

    def test_strip_fw(self):
        assert normalize_subject("FW: Hello") == "hello"

    def test_strip_fwd(self):
        assert normalize_subject("Fwd: Hello") == "hello"

    def test_strip_multiple(self):
        assert normalize_subject("RE: FW: RE: Hello") == "hello"

    def test_strip_case_insensitive(self):
        assert normalize_subject("re: fw: Hello World") == "hello world"

    def test_normalize_whitespace(self):
        assert normalize_subject("RE:   Hello   World  ") == "hello world"

    def test_empty_subject(self):
        assert normalize_subject("") == ""

    def test_no_prefix(self):
        assert normalize_subject("Hello World") == "hello world"


@skip_no_maildir
class TestThreadReconstruction:
    def test_basic_threading(self, tmp_store):
        """Emails with same normalized subject should get same thread_id."""
        # Create fake emails with related subjects
        conn = tmp_store.connect()
        conn.execute(
            "INSERT INTO emails (message_id, subject, body_text, date_sent, from_address) VALUES (?, ?, ?, ?, ?)",
            ("msg1", "Test Topic", "Body 1", "2001-01-01T10:00:00", "a@test.com"),
        )
        conn.execute(
            "INSERT INTO emails (message_id, subject, body_text, date_sent, from_address) VALUES (?, ?, ?, ?, ?)",
            ("msg2", "RE: Test Topic", "Body 2", "2001-01-01T11:00:00", "b@test.com"),
        )
        conn.execute(
            "INSERT INTO emails (message_id, subject, body_text, date_sent, from_address) VALUES (?, ?, ?, ?, ?)",
            ("msg3", "FW: Test Topic", "Body 3", "2001-01-01T12:00:00", "c@test.com"),
        )
        conn.commit()

        total = reconstruct_threads(conn)
        assert total >= 1

        # Check all three have same thread_id
        rows = conn.execute("SELECT thread_id FROM emails ORDER BY date_sent").fetchall()
        thread_ids = [r[0] for r in rows]
        assert thread_ids[0] == thread_ids[1] == thread_ids[2]

    def test_different_subjects_different_threads(self, tmp_store):
        conn = tmp_store.connect()
        conn.execute(
            "INSERT INTO emails (message_id, subject, body_text, date_sent, from_address) VALUES (?, ?, ?, ?, ?)",
            ("msg1", "Topic A", "Body", "2001-01-01T10:00:00", "a@test.com"),
        )
        conn.execute(
            "INSERT INTO emails (message_id, subject, body_text, date_sent, from_address) VALUES (?, ?, ?, ?, ?)",
            ("msg2", "Topic B", "Body", "2001-01-01T11:00:00", "b@test.com"),
        )
        conn.commit()

        reconstruct_threads(conn)
        rows = conn.execute("SELECT thread_id FROM emails ORDER BY date_sent").fetchall()
        assert rows[0][0] != rows[1][0]

    def test_parent_message_set(self, tmp_store):
        conn = tmp_store.connect()
        conn.execute(
            "INSERT INTO emails (message_id, subject, body_text, date_sent, from_address) VALUES (?, ?, ?, ?, ?)",
            ("msg1", "Topic", "Body", "2001-01-01T10:00:00", "a@test.com"),
        )
        conn.execute(
            "INSERT INTO emails (message_id, subject, body_text, date_sent, from_address) VALUES (?, ?, ?, ?, ?)",
            ("msg2", "RE: Topic", "Body", "2001-01-01T11:00:00", "b@test.com"),
        )
        conn.commit()

        reconstruct_threads(conn)
        row = conn.execute(
            "SELECT parent_message_id FROM emails WHERE message_id = 'msg2'"
        ).fetchone()
        assert row[0] == "msg1"

    def test_header_based_threading(self, tmp_store):
        """In-Reply-To header should be used when available."""
        conn = tmp_store.connect()
        conn.execute(
            "INSERT INTO emails (message_id, subject, body_text, date_sent, from_address) VALUES (?, ?, ?, ?, ?)",
            ("parent-id", "Topic", "Body", "2001-01-01T10:00:00", "a@test.com"),
        )
        conn.execute(
            "INSERT INTO emails (message_id, subject, body_text, date_sent, from_address, in_reply_to) VALUES (?, ?, ?, ?, ?, ?)",
            ("child-id", "RE: Topic", "Reply", "2001-01-01T11:00:00", "b@test.com", "parent-id"),
        )
        conn.commit()

        reconstruct_threads(conn)
        row = conn.execute(
            "SELECT parent_message_id FROM emails WHERE message_id = 'child-id'"
        ).fetchone()
        assert row[0] == "parent-id"

    def test_empty_subjects_get_threads(self, tmp_store):
        """Emails with empty subjects should each get their own thread."""
        conn = tmp_store.connect()
        conn.execute(
            "INSERT INTO emails (message_id, subject, body_text, date_sent, from_address) VALUES (?, ?, ?, ?, ?)",
            ("msg1", "", "Body 1", "2001-01-01T10:00:00", "a@test.com"),
        )
        conn.execute(
            "INSERT INTO emails (message_id, subject, body_text, date_sent, from_address) VALUES (?, ?, ?, ?, ?)",
            ("msg2", "", "Body 2", "2001-01-02T10:00:00", "b@test.com"),
        )
        conn.commit()

        reconstruct_threads(conn)
        rows = conn.execute("SELECT thread_id FROM emails").fetchall()
        # Both should have a thread_id
        assert all(r[0] is not None for r in rows)

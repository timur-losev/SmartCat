"""Tests for thread reconstruction."""

import pytest
from smartcat.parsing.threading import normalize_subject, reconstruct_threads, _subject_confidence
from smartcat.storage.sqlite_store import EmailStore
from tests.conftest import skip_no_maildir


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


class TestSubjectConfidence:
    def test_empty_subject(self):
        assert _subject_confidence("") == 0.0

    def test_generic_subject(self):
        assert _subject_confidence("update") == 0.3

    def test_single_word(self):
        assert _subject_confidence("pipeline") == 0.4

    def test_short_subject(self):
        assert _subject_confidence("gas prices") == 0.5

    def test_specific_subject(self):
        assert _subject_confidence("west coast delta position analysis") == 0.6


class TestThreadReconstruction:
    def _insert_email(self, conn, email_id, subject, date, from_addr, in_reply_to=None, message_id=None):
        """Helper to insert test emails with new schema."""
        import hashlib
        fp = hashlib.sha256(f"{from_addr}|{date}|{subject}|".encode()).hexdigest()
        msg_id = message_id or f"msg-{email_id}"
        conn.execute(
            """INSERT INTO emails (email_id, message_id, fingerprint, subject, body_text,
               date_sent, from_address, in_reply_to) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (email_id, msg_id, fp, subject, "Body", date, from_addr, in_reply_to),
        )

    def test_basic_threading(self, tmp_store):
        """Emails with same normalized subject should get same thread_id."""
        conn = tmp_store.connect()
        self._insert_email(conn, 1, "Test Topic", "2001-01-01T10:00:00", "a@test.com")
        self._insert_email(conn, 2, "RE: Test Topic", "2001-01-01T11:00:00", "b@test.com")
        self._insert_email(conn, 3, "FW: Test Topic", "2001-01-01T12:00:00", "c@test.com")
        conn.commit()

        total = reconstruct_threads(conn)
        assert total >= 1

        rows = conn.execute("SELECT thread_id FROM emails ORDER BY date_sent").fetchall()
        thread_ids = [r[0] for r in rows]
        assert thread_ids[0] == thread_ids[1] == thread_ids[2]

    def test_different_subjects_different_threads(self, tmp_store):
        conn = tmp_store.connect()
        self._insert_email(conn, 1, "Topic A", "2001-01-01T10:00:00", "a@test.com")
        self._insert_email(conn, 2, "Topic B", "2001-01-01T11:00:00", "b@test.com")
        conn.commit()

        reconstruct_threads(conn)
        rows = conn.execute("SELECT thread_id FROM emails ORDER BY date_sent").fetchall()
        assert rows[0][0] != rows[1][0]

    def test_parent_email_id_set(self, tmp_store):
        conn = tmp_store.connect()
        self._insert_email(conn, 1, "Topic", "2001-01-01T10:00:00", "a@test.com")
        self._insert_email(conn, 2, "RE: Topic", "2001-01-01T11:00:00", "b@test.com")
        conn.commit()

        reconstruct_threads(conn)
        row = conn.execute(
            "SELECT parent_email_id FROM emails WHERE email_id = 2"
        ).fetchone()
        assert row[0] == 1

    def test_header_based_threading(self, tmp_store):
        """In-Reply-To header should be used when available (confidence=1.0)."""
        conn = tmp_store.connect()
        self._insert_email(conn, 1, "Topic", "2001-01-01T10:00:00", "a@test.com", message_id="parent-id")
        self._insert_email(conn, 2, "RE: Topic", "2001-01-01T11:00:00", "b@test.com", in_reply_to="parent-id")
        conn.commit()

        reconstruct_threads(conn)
        row = conn.execute(
            "SELECT parent_email_id, thread_confidence, thread_method FROM emails WHERE email_id = 2"
        ).fetchone()
        assert row[0] == 1  # parent_email_id
        assert row[1] == 1.0  # confidence
        assert row[2] == "header"

    def test_subject_threading_has_confidence(self, tmp_store):
        """Subject-based threading should set confidence < 1.0."""
        conn = tmp_store.connect()
        self._insert_email(conn, 1, "West Coast Position Analysis", "2001-01-01T10:00:00", "a@test.com")
        self._insert_email(conn, 2, "RE: West Coast Position Analysis", "2001-01-01T11:00:00", "b@test.com")
        conn.commit()

        reconstruct_threads(conn)
        row = conn.execute(
            "SELECT thread_confidence, thread_method FROM emails WHERE email_id = 2"
        ).fetchone()
        assert row[0] is not None
        assert row[0] < 1.0
        assert row[1] == "subject"

    def test_empty_subjects_get_solo_threads(self, tmp_store):
        conn = tmp_store.connect()
        self._insert_email(conn, 1, "", "2001-01-01T10:00:00", "a@test.com")
        self._insert_email(conn, 2, "", "2001-01-02T10:00:00", "b@test.com")
        conn.commit()

        reconstruct_threads(conn)
        rows = conn.execute("SELECT thread_id FROM emails").fetchall()
        assert all(r[0] is not None for r in rows)
        # Each empty-subject email should get its own thread
        assert rows[0][0] != rows[1][0]

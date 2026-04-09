"""Tests for mbox file parsing."""

from __future__ import annotations

import mailbox
import tempfile
from pathlib import Path

import pytest

from smartcat.parsing.mbox_parser import iter_mbox, parse_mbox_message, discover_mbox_files


# --- Fixtures: build synthetic mbox files ---

SAMPLE_EMAIL_RAW = """\
From sender@example.com Mon Mar 15 10:00:00 2024
Message-ID: <test001@example.com>
Date: Fri, 15 Mar 2024 10:00:00 +0000
From: John Smith <john@example.com>
To: Jane Doe <jane@example.com>
Cc: Bob <bob@example.com>
Subject: Invoice #2024-001
In-Reply-To: <parent001@example.com>
References: <root001@example.com> <parent001@example.com>
Content-Type: text/plain; charset=utf-8

Dear Jane,

Please find the invoice attached.
Total: $45,230.00

Best regards,
John
"""

SAMPLE_EMAIL_RUSSIAN = """\
From sender@example.com Mon Mar 15 11:00:00 2024
Message-ID: <test002@example.com>
Date: Fri, 15 Mar 2024 11:00:00 +0300
From: =?utf-8?b?0JjQstCw0L0g0J/QtdGC0YDQvtCy?= <ivan@example.com>
To: =?utf-8?b?0JDQvdC90LAg0KHQuNC00L7RgNC+0LLQsA==?= <anna@example.com>
Subject: =?utf-8?b?0KHRh9C10YIt0YTQsNC60YLRg9GA0LAg4oSWMjAyNC0wMDE=?=
Content-Type: text/plain; charset=utf-8

Уважаемая Анна,

Направляем счет-фактуру на сумму $45 230,00.

С уважением,
Иван Петров
"""

SAMPLE_EMAIL_WITH_ATTACHMENT = """\
From sender@example.com Mon Mar 15 12:00:00 2024
Message-ID: <test003@example.com>
Date: Fri, 15 Mar 2024 12:00:00 +0000
From: John Smith <john@example.com>
To: Jane Doe <jane@example.com>
Subject: Document attached
MIME-Version: 1.0
Content-Type: multipart/mixed; boundary="boundary123"

--boundary123
Content-Type: text/plain; charset=utf-8

See attached document.

--boundary123
Content-Type: application/pdf; name="report.pdf"
Content-Disposition: attachment; filename="report.pdf"
Content-Transfer-Encoding: base64

JVBERi0xLjQKMSAwIG9iago8PC9UeXBlIC9DYXRhbG9nPj4KZW5kb2JqCg==
--boundary123--
"""

SAMPLE_EMAIL_FORWARDED = """\
From sender@example.com Mon Mar 15 13:00:00 2024
Message-ID: <test004@example.com>
Date: Fri, 15 Mar 2024 13:00:00 +0000
From: Bob <bob@example.com>
To: Alice <alice@example.com>
Subject: Fwd: Original subject
Content-Type: text/plain; charset=utf-8

FYI see below.

---------------------- Forwarded by Bob on 03/15/2024 ----------------------

From: John Smith
To: Bob
Subject: Original subject

Here is the original message content.
"""


def _write_raw_mbox(path: Path, raw_messages: list[str]) -> None:
    """Write raw email strings directly to an mbox file.

    Avoids Python mailbox.mbox encoding issues with non-ASCII content
    by writing the raw bytes directly in mbox format.
    """
    with open(path, "wb") as f:
        for raw in raw_messages:
            raw_bytes = raw.encode("utf-8")
            # Ensure each message starts with "From " line
            if not raw_bytes.startswith(b"From "):
                raw_bytes = b"From sender@example.com Mon Jan 01 00:00:00 2024\n" + raw_bytes
            f.write(raw_bytes)
            # Ensure blank line at end of each message
            if not raw_bytes.endswith(b"\n\n"):
                if raw_bytes.endswith(b"\n"):
                    f.write(b"\n")
                else:
                    f.write(b"\n\n")


@pytest.fixture
def sample_mbox(tmp_path):
    """Create a temporary mbox file with multiple test emails."""
    mbox_path = tmp_path / "test.mbox"
    _write_raw_mbox(mbox_path, [
        SAMPLE_EMAIL_RAW, SAMPLE_EMAIL_RUSSIAN,
        SAMPLE_EMAIL_WITH_ATTACHMENT, SAMPLE_EMAIL_FORWARDED,
    ])
    return mbox_path


@pytest.fixture
def empty_mbox(tmp_path):
    """Create an empty mbox file."""
    mbox_path = tmp_path / "empty.mbox"
    mbox_path.write_bytes(b"")
    return mbox_path


# --- Tests ---

class TestIterMbox:
    """Tests for iterating over mbox files."""

    def test_iter_basic(self, sample_mbox):
        """Should yield all 4 messages from the sample mbox."""
        emails = list(iter_mbox(sample_mbox))
        assert len(emails) == 4

    def test_iter_empty(self, empty_mbox):
        """Empty mbox should yield nothing."""
        emails = list(iter_mbox(empty_mbox))
        assert len(emails) == 0

    def test_message_ids(self, sample_mbox):
        """Each message should have a unique message_id."""
        emails = list(iter_mbox(sample_mbox))
        ids = [e.message_id for e in emails]
        assert len(set(ids)) == 4

    def test_source_path(self, sample_mbox):
        """All messages should reference the mbox file as source."""
        emails = list(iter_mbox(sample_mbox))
        for e in emails:
            assert str(sample_mbox) in e.source_path


class TestParseEnglishEmail:
    """Tests for parsing a standard English email from mbox."""

    def test_headers(self, sample_mbox):
        emails = list(iter_mbox(sample_mbox))
        e = emails[0]

        assert e.message_id == "test001@example.com"
        assert e.from_address == "john@example.com"
        assert e.from_name == "John Smith"
        assert e.subject == "Invoice #2024-001"
        assert "jane@example.com" in e.to_addresses
        assert "bob@example.com" in e.cc_addresses

    def test_body(self, sample_mbox):
        emails = list(iter_mbox(sample_mbox))
        e = emails[0]

        assert "invoice" in e.body_text.lower()
        assert "$45,230.00" in e.body_text

    def test_threading_headers(self, sample_mbox):
        emails = list(iter_mbox(sample_mbox))
        e = emails[0]

        assert e.in_reply_to == "parent001@example.com"
        assert "root001@example.com" in e.references
        assert "parent001@example.com" in e.references

    def test_date(self, sample_mbox):
        emails = list(iter_mbox(sample_mbox))
        e = emails[0]

        assert e.date_sent is not None
        assert e.date_sent.year == 2024
        assert e.date_sent.month == 3


class TestParseRussianEmail:
    """Tests for parsing a Russian email (encoded headers)."""

    def test_from_address(self, sample_mbox):
        emails = list(iter_mbox(sample_mbox))
        e = emails[1]

        assert e.from_address == "ivan@example.com"
        assert e.message_id == "test002@example.com"

    def test_russian_body(self, sample_mbox):
        emails = list(iter_mbox(sample_mbox))
        e = emails[1]

        assert "счет-фактуру" in e.body_text.lower()
        assert "$45 230,00" in e.body_text


class TestParseAttachment:
    """Tests for parsing email with MIME attachment."""

    def test_has_attachment(self, sample_mbox):
        emails = list(iter_mbox(sample_mbox))
        e = emails[2]

        assert e.has_attachments is True
        assert len(e.attachments) == 1

    def test_attachment_metadata(self, sample_mbox):
        emails = list(iter_mbox(sample_mbox))
        att = emails[2].attachments[0]

        assert att.filename == "report.pdf"
        assert att.content_type == "application/pdf"
        assert len(att.data) > 0


class TestParseForwarded:
    """Tests for detecting forwarded content."""

    def test_forwarded_detection(self, sample_mbox):
        emails = list(iter_mbox(sample_mbox))
        e = emails[3]

        assert e.has_forwarded_content is True
        assert "Fwd:" in e.subject or "Original subject" in e.subject


class TestDiscoverMbox:
    """Tests for mbox file discovery."""

    def test_discover(self, tmp_path):
        (tmp_path / "inbox.mbox").write_text("")
        (tmp_path / "sent.mbox").write_text("")
        (tmp_path / "readme.txt").write_text("")

        found = discover_mbox_files(tmp_path)
        assert len(found) == 2
        assert all(p.suffix == ".mbox" for p in found)

    def test_discover_empty(self, tmp_path):
        found = discover_mbox_files(tmp_path)
        assert len(found) == 0

"""Tests for MIME email parser."""

import pytest
from pathlib import Path
from smartcat.parsing.mime_parser import parse_email_file, ParsedEmail
from tests.conftest import skip_no_maildir, MAILDIR


@skip_no_maildir
class TestParseEmailFile:
    def test_basic_parse(self, sample_email_path):
        result = parse_email_file(sample_email_path)
        assert isinstance(result, ParsedEmail)
        assert result.message_id
        assert result.date_sent is not None
        assert result.from_address

    def test_message_id_normalized(self, sample_email_path):
        result = parse_email_file(sample_email_path)
        assert "<" not in result.message_id
        assert ">" not in result.message_id

    def test_from_address_parsed(self, sample_email_path):
        result = parse_email_file(sample_email_path)
        assert "@" in result.from_address
        assert result.from_address == "heather.dunton@enron.com"

    def test_from_name_from_x_header(self, sample_email_path):
        result = parse_email_file(sample_email_path)
        # X-From contains "Dunton, Heather" with LDAP path
        assert "Dunton" in result.from_name
        assert "Heather" in result.from_name

    def test_to_addresses(self, sample_email_path):
        result = parse_email_file(sample_email_path)
        assert len(result.to_addresses) >= 1
        assert any("allen" in addr for addr in result.to_addresses)

    def test_subject(self, sample_email_path):
        result = parse_email_file(sample_email_path)
        assert "West Position" in result.subject

    def test_body_text_not_empty(self, sample_email_path):
        result = parse_email_file(sample_email_path)
        assert len(result.body_text) > 0

    def test_reply_detection(self, sample_email_path):
        result = parse_email_file(sample_email_path)
        assert result.has_reply_content is True

    def test_file_reference_extraction(self, sample_email_path):
        result = parse_email_file(sample_email_path)
        assert "west_delta_pos.xls" in result.referenced_files

    def test_has_attachments_from_file_refs(self, sample_email_path):
        result = parse_email_file(sample_email_path)
        assert result.has_attachments is True

    def test_x_folder(self, sample_email_path):
        result = parse_email_file(sample_email_path)
        assert result.x_folder != ""

    def test_source_path_set(self, sample_email_path):
        result = parse_email_file(sample_email_path)
        assert str(sample_email_path) in result.source_path


@skip_no_maildir
class TestMultipleEmails:
    """Test parser against a variety of emails to ensure robustness."""

    def _get_test_files(self, count=20):
        """Get a diverse set of test files from different users."""
        files = []
        users = sorted(MAILDIR.iterdir())[:5]
        for user in users:
            if not user.is_dir():
                continue
            for folder in sorted(user.iterdir())[:2]:
                if not folder.is_dir():
                    continue
                for email_file in sorted(folder.iterdir())[:count // 5]:
                    if email_file.is_file():
                        files.append(email_file)
        return files[:count]

    def test_no_crashes(self):
        """Parser should not crash on any email file."""
        files = self._get_test_files(50)
        assert len(files) > 0, "No test files found"
        for f in files:
            result = parse_email_file(f)
            assert result.message_id, f"No message_id for {f}"

    def test_all_have_from_address(self):
        """Every email should have a from address."""
        files = self._get_test_files(50)
        for f in files:
            result = parse_email_file(f)
            assert result.from_address, f"No from_address for {f}"

    def test_date_parsing(self):
        """Dates should be parseable."""
        files = self._get_test_files(50)
        parsed_count = sum(1 for f in files if parse_email_file(f).date_sent is not None)
        # Allow some emails to have no date, but most should
        assert parsed_count >= len(files) * 0.9

"""MIME email parser for maildir files."""

from __future__ import annotations

import email
import email.utils
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.message import Message
from pathlib import Path
from typing import Optional

from dateutil import parser as dateutil_parser


@dataclass
class ParsedEmail:
    """Structured representation of a parsed email."""

    message_id: str
    date_sent: Optional[datetime]
    subject: str
    body_text: str
    content_type: str

    from_address: str
    from_name: str
    to_addresses: list[str] = field(default_factory=list)
    to_names: list[str] = field(default_factory=list)
    cc_addresses: list[str] = field(default_factory=list)
    cc_names: list[str] = field(default_factory=list)
    bcc_addresses: list[str] = field(default_factory=list)

    x_folder: str = ""
    x_origin: str = ""
    source_path: str = ""

    has_forwarded_content: bool = False
    has_reply_content: bool = False
    has_attachments: bool = False

    # For production: attachment filenames referenced in body
    referenced_files: list[str] = field(default_factory=list)

    # Raw headers for thread reconstruction
    in_reply_to: str = ""
    references: list[str] = field(default_factory=list)


# Patterns for detecting forwarded/reply content
_ORIGINAL_MSG_PATTERN = re.compile(r"-{3,}\s*Original Message\s*-{3,}", re.IGNORECASE)
_FORWARDED_PATTERN = re.compile(
    r"(?:-{3,}\s*Forwarded\s+by\s+.+?-{3,}|"
    r"Begin forwarded message:|"
    r"Forwarded message from)",
    re.IGNORECASE,
)
# Pattern for file references in body
_FILE_REF_PATTERN = re.compile(
    r"<<\s*File:\s*(.+?)\s*>>|"  # << File: name.xls >>
    r"(?:attached|attachment)[\s:]+(\S+\.(?:xls|xlsx|doc|docx|pdf|csv|txt|ppt|pptx))",
    re.IGNORECASE,
)


def _parse_address_header(msg: Message, header: str) -> list[tuple[str, str]]:
    """Parse an address header into list of (name, email) tuples."""
    raw = msg.get(header, "")
    if not raw:
        return []
    return email.utils.getaddresses([raw])


def _parse_x_address_header(msg: Message, header: str) -> list[str]:
    """Parse X-From/X-To/X-cc style headers into display names.

    Format: "Last, First </O=ENRON/...>, Last2, First2 </O=ENRON/...>"
    We split on '>' boundaries (not commas) to preserve "Last, First" names.
    """
    raw = msg.get(header, "")
    if not raw or not raw.strip():
        return []

    # First strip all LDAP paths, then split on the separator between entries
    # Entries are separated by ">, " pattern (after LDAP path)
    # Split on LDAP path endings followed by comma
    entries = re.split(r">\s*,\s*", raw)
    names = []
    for entry in entries:
        # Strip remaining LDAP path (for last entry which has no trailing comma)
        clean = re.sub(r"\s*<[^>]*>?\s*$", "", entry).strip()
        if clean:
            names.append(clean)
    return names


def _parse_date(msg: Message) -> Optional[datetime]:
    """Parse email Date header into datetime."""
    raw = msg.get("Date", "")
    if not raw:
        return None
    try:
        return dateutil_parser.parse(raw)
    except (ValueError, OverflowError):
        return None


def _extract_body(msg: Message) -> tuple[str, str]:
    """Extract body text and content type from email message."""
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            if ct == "text/plain":
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    try:
                        return payload.decode(charset, errors="replace"), ct
                    except (LookupError, UnicodeDecodeError):
                        return payload.decode("utf-8", errors="replace"), ct
            elif ct == "text/html":
                # Fall back to HTML if no plain text
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    try:
                        return payload.decode(charset, errors="replace"), ct
                    except (LookupError, UnicodeDecodeError):
                        return payload.decode("utf-8", errors="replace"), ct
        return "", "text/plain"
    else:
        payload = msg.get_payload(decode=True)
        ct = msg.get_content_type()
        if payload:
            charset = msg.get_content_charset() or "utf-8"
            try:
                return payload.decode(charset, errors="replace"), ct
            except (LookupError, UnicodeDecodeError):
                return payload.decode("utf-8", errors="replace"), ct
        return "", ct


def parse_email_file(file_path: Path) -> ParsedEmail:
    """Parse a single maildir email file into a ParsedEmail.

    Args:
        file_path: Path to the email file.

    Returns:
        ParsedEmail with all extracted fields.

    Raises:
        ValueError: If the file cannot be parsed as email.
    """
    raw_bytes = file_path.read_bytes()

    # Try to parse as email
    try:
        raw_text = raw_bytes.decode("utf-8", errors="replace")
    except Exception:
        raw_text = raw_bytes.decode("latin-1", errors="replace")

    msg = email.message_from_string(raw_text)

    # Message-ID
    message_id = msg.get("Message-ID", "").strip()
    if message_id:
        # Normalize: strip angle brackets
        message_id = message_id.strip("<>")

    if not message_id:
        # Generate a synthetic ID from file path
        message_id = f"synthetic:{file_path}"

    # Date
    date_sent = _parse_date(msg)

    # Subject
    subject = msg.get("Subject", "") or ""

    # From
    from_pairs = _parse_address_header(msg, "From")
    from_name = from_pairs[0][0] if from_pairs else ""
    from_address = from_pairs[0][1] if from_pairs else ""

    # Prefer X-From for display name
    x_from_names = _parse_x_address_header(msg, "X-From")
    if x_from_names and x_from_names[0]:
        from_name = x_from_names[0]

    # To
    to_pairs = _parse_address_header(msg, "To")
    to_addresses = [addr for _, addr in to_pairs if addr]
    to_names = _parse_x_address_header(msg, "X-To") or [name for name, _ in to_pairs]

    # CC
    cc_pairs = _parse_address_header(msg, "Cc")
    cc_addresses = [addr for _, addr in cc_pairs if addr]
    cc_names = _parse_x_address_header(msg, "X-cc") or [name for name, _ in cc_pairs]

    # BCC
    bcc_raw = msg.get("X-bcc", "")
    bcc_addresses = [addr.strip() for addr in bcc_raw.split(",") if addr.strip()] if bcc_raw else []

    # Body
    body_text, content_type = _extract_body(msg)

    # Detect forwarded/reply content
    has_forwarded = bool(_FORWARDED_PATTERN.search(body_text))
    has_reply = bool(_ORIGINAL_MSG_PATTERN.search(body_text))

    # Detect file references
    file_refs = _FILE_REF_PATTERN.findall(body_text)
    referenced_files = [ref for group in file_refs for ref in group if ref]

    # Check for MIME attachments
    has_attachments = False
    if msg.is_multipart():
        for part in msg.walk():
            disp = str(part.get("Content-Disposition", ""))
            if "attachment" in disp:
                has_attachments = True
                break

    # Also treat file references as attachment indicators
    if referenced_files:
        has_attachments = True

    # X-headers
    x_folder = msg.get("X-Folder", "") or ""
    x_origin = msg.get("X-Origin", "") or ""

    # Threading headers (present in production, absent in Enron)
    in_reply_to = (msg.get("In-Reply-To", "") or "").strip().strip("<>")
    references_raw = msg.get("References", "") or ""
    references = [r.strip("<>") for r in references_raw.split() if r.strip()]

    return ParsedEmail(
        message_id=message_id,
        date_sent=date_sent,
        subject=subject,
        body_text=body_text,
        content_type=content_type,
        from_address=from_address,
        from_name=from_name,
        to_addresses=to_addresses,
        to_names=to_names,
        cc_addresses=cc_addresses,
        cc_names=cc_names,
        bcc_addresses=bcc_addresses,
        x_folder=x_folder,
        x_origin=x_origin,
        source_path=str(file_path),
        has_forwarded_content=has_forwarded,
        has_reply_content=has_reply,
        has_attachments=has_attachments,
        referenced_files=referenced_files,
        in_reply_to=in_reply_to,
        references=references,
    )

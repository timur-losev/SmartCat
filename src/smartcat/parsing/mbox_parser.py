"""Mbox file parser — iterates messages and delegates to mime_parser.

Supports .mbox files exported from The Bat!, Thunderbird, Gmail Takeout, etc.
Uses Python stdlib `mailbox.mbox` for safe iteration over concatenated messages.
"""

from __future__ import annotations

import email
import logging
import mailbox
from pathlib import Path
from typing import Iterator

from smartcat.parsing.mime_parser import (
    Attachment,
    ParsedEmail,
    _decode_payload,
    _extract_body,
    _parse_address_header,
    _parse_date,
    _parse_x_address_header,
    _FILE_REF_PATTERN,
    _FORWARDED_PATTERN,
    _ORIGINAL_MSG_PATTERN,
)

log = logging.getLogger(__name__)


def parse_mbox_message(msg: mailbox.mboxMessage, source_path: str = "",
                       index: int = 0) -> ParsedEmail:
    """Parse a single mbox message into ParsedEmail.

    Reuses the same extraction logic as mime_parser but works with
    an already-parsed email.message.Message object from the mbox.
    """
    # Message-ID
    message_id = msg.get("Message-ID", "").strip()
    if message_id:
        message_id = message_id.strip("<>")
    if not message_id:
        message_id = f"synthetic:mbox:{source_path}:{index}"

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
    bcc_raw = msg.get("X-bcc", "") or msg.get("Bcc", "")
    bcc_addresses = [addr.strip() for addr in bcc_raw.split(",") if addr.strip()] if bcc_raw else []

    # Body
    body_text, content_type, body_html = _extract_body(msg)

    # Detect forwarded/reply content
    has_forwarded = bool(_FORWARDED_PATTERN.search(body_text))
    has_reply = bool(_ORIGINAL_MSG_PATTERN.search(body_text))

    # Detect file references
    file_refs = _FILE_REF_PATTERN.findall(body_text)
    referenced_files = [ref for group in file_refs for ref in group if ref]

    # Extract MIME attachments
    has_attachments = False
    mime_attachments: list[Attachment] = []
    if msg.is_multipart():
        for part in msg.walk():
            disp = str(part.get("Content-Disposition", ""))
            ct = part.get_content_type()

            if ct in ("text/plain", "text/html") and "attachment" not in disp:
                continue
            if part.get_content_maintype() == "multipart":
                continue

            if "attachment" in disp or ct not in ("text/plain", "text/html"):
                payload = part.get_payload(decode=True)
                if payload and len(payload) > 0:
                    filename = part.get_filename() or ""
                    mime_attachments.append(Attachment(
                        filename=filename,
                        content_type=ct,
                        data=payload,
                    ))
                    has_attachments = True

    if referenced_files:
        has_attachments = True

    # X-headers
    x_folder = msg.get("X-Folder", "") or ""
    x_origin = msg.get("X-Origin", "") or ""

    # Threading headers
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
        body_html=body_html,
        x_folder=x_folder,
        x_origin=x_origin,
        source_path=source_path,
        has_forwarded_content=has_forwarded,
        has_reply_content=has_reply,
        has_attachments=has_attachments,
        referenced_files=referenced_files,
        attachments=mime_attachments,
        in_reply_to=in_reply_to,
        references=references,
    )


def iter_mbox(mbox_path: Path) -> Iterator[ParsedEmail]:
    """Iterate over all messages in an mbox file, yielding ParsedEmail objects.

    Args:
        mbox_path: Path to .mbox file.

    Yields:
        ParsedEmail for each message in the mbox.
    """
    mbox = mailbox.mbox(str(mbox_path))
    source = str(mbox_path)

    for i, msg in enumerate(mbox):
        try:
            yield parse_mbox_message(msg, source_path=source, index=i)
        except Exception as e:
            log.warning("mbox.parse_failed: index=%d err=%s", i, e)
            continue

    mbox.close()


def discover_mbox_files(directory: Path) -> list[Path]:
    """Find all .mbox files in a directory (non-recursive)."""
    return sorted(p for p in directory.iterdir() if p.suffix.lower() == ".mbox" and p.is_file())

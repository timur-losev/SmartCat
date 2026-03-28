"""Hierarchical email-aware chunking.

Chunk levels:
  L1 Summary:    Subject + From/To/Date + first N chars of body
  L2 Body:       Paragraph-based chunks of main body
  L3 Quoted:     Embedded forwarded/replied content as separate chunks
  L4 Attachment: (handled separately by Docling pipeline)
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field


@dataclass
class EmailChunk:
    chunk_id: str
    message_id: str
    chunk_type: str  # 'summary', 'body', 'quoted', 'attachment'
    chunk_index: int
    text: str
    token_count: int  # approximate

    # Metadata for Qdrant payload
    date_sent: str = ""
    from_address: str = ""
    to_addresses: list[str] = field(default_factory=list)
    subject: str = ""
    thread_id: str = ""
    has_monetary: bool = False
    has_attachment: bool = False


# Approximate token count: ~4 chars per token for English
def _approx_tokens(text: str) -> int:
    return max(1, len(text) // 4)


# Pattern to split quoted/forwarded sections
_QUOTE_SPLIT = re.compile(
    r"((?:-{3,}\s*(?:Original Message|Forwarded\s+by)\s*-{3,}.*?)(?=-{3,}\s*(?:Original Message|Forwarded\s+by)\s*-{3,}|\Z))",
    re.DOTALL | re.IGNORECASE,
)

_QUOTE_HEADER = re.compile(
    r"-{3,}\s*(?:Original Message|Forwarded\s+by).*?-{3,}",
    re.IGNORECASE,
)

# Also match the simpler pattern used in some emails
_SIMPLE_QUOTE = re.compile(
    r"(-{5,}\s*Original Message\s*-{5,})",
    re.IGNORECASE,
)


def _split_body_and_quotes(body: str) -> tuple[str, list[str]]:
    """Split email body into main content and quoted sections."""
    # Find the first quote marker
    match = _SIMPLE_QUOTE.search(body)
    if not match:
        match = _QUOTE_HEADER.search(body)

    if not match:
        return body.strip(), []

    main_body = body[:match.start()].strip()
    quoted_text = body[match.start():]

    # Split multiple quoted sections
    sections = _SIMPLE_QUOTE.split(quoted_text)
    quoted_chunks = []
    current = ""

    for section in sections:
        if _SIMPLE_QUOTE.match(section):
            if current.strip():
                quoted_chunks.append(current.strip())
            current = section
        else:
            current += section

    if current.strip():
        quoted_chunks.append(current.strip())

    return main_body, quoted_chunks


def _chunk_text_by_paragraphs(
    text: str,
    max_tokens: int = 512,
    overlap_tokens: int = 50,
) -> list[str]:
    """Split text into chunks at paragraph boundaries."""
    if _approx_tokens(text) <= max_tokens:
        return [text] if text.strip() else []

    # Split on double newlines (paragraphs)
    paragraphs = re.split(r"\n\s*\n", text)
    paragraphs = [p.strip() for p in paragraphs if p.strip()]

    if not paragraphs:
        return [text] if text.strip() else []

    chunks = []
    current_chunk = ""

    for para in paragraphs:
        candidate = (current_chunk + "\n\n" + para).strip() if current_chunk else para
        if _approx_tokens(candidate) <= max_tokens:
            current_chunk = candidate
        else:
            if current_chunk:
                chunks.append(current_chunk)
            # Start new chunk, with overlap from end of previous
            if chunks and overlap_tokens > 0:
                prev_text = chunks[-1]
                overlap_chars = overlap_tokens * 4
                overlap = prev_text[-overlap_chars:] if len(prev_text) > overlap_chars else ""
                current_chunk = (overlap + "\n\n" + para).strip() if overlap else para
            else:
                current_chunk = para

            # Handle very long paragraphs that exceed max_tokens
            if _approx_tokens(current_chunk) > max_tokens:
                # Force split by sentences
                sentences = re.split(r"(?<=[.!?])\s+", current_chunk)
                current_chunk = ""
                for sent in sentences:
                    candidate = (current_chunk + " " + sent).strip() if current_chunk else sent
                    if _approx_tokens(candidate) <= max_tokens:
                        current_chunk = candidate
                    else:
                        if current_chunk:
                            chunks.append(current_chunk)
                        current_chunk = sent

    if current_chunk:
        chunks.append(current_chunk)

    return chunks


def chunk_email(
    message_id: str,
    subject: str,
    body_text: str,
    from_address: str = "",
    from_name: str = "",
    to_addresses: list[str] | None = None,
    date_sent: str = "",
    thread_id: str = "",
    has_monetary: bool = False,
    has_attachment: bool = False,
    summary_max_chars: int = 200,
    chunk_max_tokens: int = 512,
    chunk_overlap_tokens: int = 50,
) -> list[EmailChunk]:
    """Generate hierarchical chunks for a single email.

    Returns:
        List of EmailChunk objects ready for embedding and Qdrant indexing.
    """
    chunks: list[EmailChunk] = []
    to_addrs = to_addresses or []
    chunk_idx = 0

    def _make_id() -> str:
        return f"{message_id[:40]}_{chunk_idx}_{uuid.uuid4().hex[:8]}"

    # Common metadata for all chunks
    meta = dict(
        date_sent=date_sent,
        from_address=from_address,
        to_addresses=to_addrs,
        subject=subject,
        thread_id=thread_id,
        has_monetary=has_monetary,
        has_attachment=has_attachment,
    )

    # L1: Summary chunk (always created)
    header_parts = []
    if subject:
        header_parts.append(f"Subject: {subject}")
    if from_name or from_address:
        header_parts.append(f"From: {from_name or from_address}")
    if to_addrs:
        to_display = ", ".join(to_addrs[:5])
        if len(to_addrs) > 5:
            to_display += f" (+{len(to_addrs) - 5} more)"
        header_parts.append(f"To: {to_display}")
    if date_sent:
        header_parts.append(f"Date: {date_sent}")

    body_preview = body_text[:summary_max_chars].strip()
    if len(body_text) > summary_max_chars:
        body_preview += "..."

    summary_text = "\n".join(header_parts) + "\n\n" + body_preview
    chunks.append(EmailChunk(
        chunk_id=_make_id(),
        message_id=message_id,
        chunk_type="summary",
        chunk_index=chunk_idx,
        text=summary_text,
        token_count=_approx_tokens(summary_text),
        **meta,
    ))
    chunk_idx += 1

    # Split body into main content and quoted sections
    main_body, quoted_sections = _split_body_and_quotes(body_text)

    # L2: Body chunks (only if main body is substantial)
    if main_body and _approx_tokens(main_body) > 50:
        body_chunks = _chunk_text_by_paragraphs(
            main_body,
            max_tokens=chunk_max_tokens,
            overlap_tokens=chunk_overlap_tokens,
        )
        for text in body_chunks:
            chunks.append(EmailChunk(
                chunk_id=_make_id(),
                message_id=message_id,
                chunk_type="body",
                chunk_index=chunk_idx,
                text=text,
                token_count=_approx_tokens(text),
                **meta,
            ))
            chunk_idx += 1

    # L3: Quoted/forwarded chunks
    for quoted in quoted_sections:
        if _approx_tokens(quoted) < 20:
            continue

        # If the quoted section is long, chunk it too
        if _approx_tokens(quoted) > chunk_max_tokens:
            sub_chunks = _chunk_text_by_paragraphs(quoted, max_tokens=chunk_max_tokens)
            for text in sub_chunks:
                chunks.append(EmailChunk(
                    chunk_id=_make_id(),
                    message_id=message_id,
                    chunk_type="quoted",
                    chunk_index=chunk_idx,
                    text=text,
                    token_count=_approx_tokens(text),
                    **meta,
                ))
                chunk_idx += 1
        else:
            chunks.append(EmailChunk(
                chunk_id=_make_id(),
                message_id=message_id,
                chunk_type="quoted",
                chunk_index=chunk_idx,
                text=quoted,
                token_count=_approx_tokens(quoted),
                **meta,
            ))
            chunk_idx += 1

    return chunks

"""Email thread reconstruction.

Strategy:
1. Use In-Reply-To / References headers when available (production)
2. Fallback: subject normalization + participant overlap (Enron)
3. Parse embedded forwarded/reply chains from body text
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
from collections import defaultdict

import structlog

log = structlog.get_logger()

# Patterns for stripping reply/forward prefixes
_SUBJECT_PREFIX = re.compile(
    r"^(?:\s*(?:RE|FW|FWD|Fwd)\s*:\s*)+",
    re.IGNORECASE,
)


def normalize_subject(subject: str) -> str:
    """Strip RE:/FW:/Fwd: prefixes and normalize whitespace."""
    cleaned = _SUBJECT_PREFIX.sub("", subject)
    cleaned = re.sub(r"\s+", " ", cleaned).strip().lower()
    return cleaned


def _make_thread_id(canonical_subject: str) -> str:
    """Generate a deterministic thread ID from canonical subject."""
    h = hashlib.md5(canonical_subject.encode("utf-8")).hexdigest()[:12]
    return f"thread_{h}"


def reconstruct_threads(conn: sqlite3.Connection, batch_size: int = 10000):
    """Reconstruct email threads and update thread_id/parent_message_id.

    Phase 1: Use In-Reply-To headers (for production data).
    Phase 2: Subject-based clustering with participant overlap (fallback).
    """
    cursor = conn.cursor()

    # --- Phase 1: Header-based threading ---
    # Find emails with In-Reply-To header
    header_linked = cursor.execute(
        "SELECT message_id, in_reply_to FROM emails WHERE in_reply_to IS NOT NULL AND in_reply_to != ''"
    ).fetchall()

    if header_linked:
        log.info("threading.header_based", count=len(header_linked))
        for msg_id, in_reply_to in header_linked:
            # Check if parent exists
            parent = cursor.execute(
                "SELECT message_id FROM emails WHERE message_id = ?",
                (in_reply_to,),
            ).fetchone()
            if parent:
                cursor.execute(
                    "UPDATE emails SET parent_message_id = ? WHERE message_id = ?",
                    (in_reply_to, msg_id),
                )

    # --- Phase 2: Subject-based clustering ---
    log.info("threading.subject_clustering.start")

    # Build subject → message_id mapping
    offset = 0
    subject_groups: dict[str, list[tuple[str, str]]] = defaultdict(list)  # norm_subject -> [(msg_id, date)]

    while True:
        rows = cursor.execute(
            "SELECT message_id, subject, date_sent FROM emails ORDER BY date_sent LIMIT ? OFFSET ?",
            (batch_size, offset),
        ).fetchall()
        if not rows:
            break

        for msg_id, subject, date_sent in rows:
            norm = normalize_subject(subject or "")
            if norm:  # Skip empty subjects
                subject_groups[norm].append((msg_id, date_sent or ""))

        offset += batch_size

    # Assign thread IDs
    threads_assigned = 0
    for norm_subject, messages in subject_groups.items():
        if len(messages) < 1:
            continue

        thread_id = _make_thread_id(norm_subject)
        # Sort by date
        messages.sort(key=lambda x: x[1])

        for i, (msg_id, _) in enumerate(messages):
            parent_id = messages[i - 1][0] if i > 0 else None
            cursor.execute(
                "UPDATE emails SET thread_id = ?, parent_message_id = COALESCE(parent_message_id, ?) WHERE message_id = ?",
                (thread_id, parent_id, msg_id),
            )
        threads_assigned += 1

    # Handle emails with empty/no subject — each is its own thread
    cursor.execute(
        "UPDATE emails SET thread_id = 'thread_' || SUBSTR(message_id, 1, 12) WHERE thread_id IS NULL"
    )

    conn.commit()

    total_threads = cursor.execute("SELECT COUNT(DISTINCT thread_id) FROM emails").fetchone()[0]
    log.info("threading.done", threads=total_threads, subject_groups=threads_assigned)
    return total_threads

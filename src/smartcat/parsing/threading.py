"""Email thread reconstruction.

Strategy:
1. Use In-Reply-To / References headers when available (production)
   → confidence=1.0, method='header'
2. Fallback: subject normalization + participant overlap (Enron)
   → confidence=0.5-0.7 depending on subject specificity, method='subject'
3. Parse embedded forwarded/reply chains from body text
   → confidence=0.8, method='body_marker'

Threading stores confidence and method so downstream can filter ambiguous links.
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

# Subjects that are too generic to thread reliably
_GENERIC_SUBJECTS = frozenset([
    "update", "re:", "fw:", "fwd:", "hello", "hi", "thanks", "thank you",
    "question", "info", "information", "meeting", "call", "follow up",
    "followup", "follow-up", "reminder", "urgent", "important", "fyi",
    "test", "help", "request", "status", "report", "schedule",
])


def normalize_subject(subject: str) -> str:
    """Strip RE:/FW:/Fwd: prefixes and normalize whitespace."""
    cleaned = _SUBJECT_PREFIX.sub("", subject)
    cleaned = re.sub(r"\s+", " ", cleaned).strip().lower()
    return cleaned


def _subject_confidence(norm_subject: str) -> float:
    """Estimate threading confidence based on subject specificity."""
    if not norm_subject:
        return 0.0
    if norm_subject in _GENERIC_SUBJECTS:
        return 0.3  # Too generic, likely false thread
    words = norm_subject.split()
    if len(words) <= 1:
        return 0.4  # Single-word subjects are risky
    if len(words) <= 3:
        return 0.5
    return 0.6  # Longer, more specific subjects


def _make_thread_id(canonical_subject: str) -> str:
    """Generate a deterministic thread ID from canonical subject."""
    h = hashlib.md5(canonical_subject.encode("utf-8")).hexdigest()[:12]
    return f"thread_{h}"


def reconstruct_threads(conn: sqlite3.Connection, batch_size: int = 10000):
    """Reconstruct email threads and update thread_id/parent_email_id.

    Phase 1: Use In-Reply-To headers (confidence=1.0).
    Phase 2: Subject-based clustering (confidence=0.3-0.6).
    """
    cursor = conn.cursor()

    # --- Phase 1: Header-based threading ---
    header_linked = cursor.execute(
        """SELECT e1.email_id, e1.in_reply_to, e2.email_id as parent_eid
           FROM emails e1
           JOIN emails e2 ON e2.message_id = e1.in_reply_to
           WHERE e1.in_reply_to IS NOT NULL AND e1.in_reply_to != ''"""
    ).fetchall()

    if header_linked:
        log.info("threading.header_based", count=len(header_linked))
        for email_id, _, parent_eid in header_linked:
            cursor.execute(
                """UPDATE emails SET parent_email_id = ?, thread_confidence = 1.0,
                   thread_method = 'header' WHERE email_id = ?""",
                (parent_eid, email_id),
            )

    # --- Phase 2: Subject-based clustering ---
    log.info("threading.subject_clustering.start")

    offset = 0
    subject_groups: dict[str, list[tuple[int, str]]] = defaultdict(list)

    while True:
        rows = cursor.execute(
            "SELECT email_id, subject, date_sent FROM emails ORDER BY date_sent LIMIT ? OFFSET ?",
            (batch_size, offset),
        ).fetchall()
        if not rows:
            break

        for eid, subject, date_sent in rows:
            norm = normalize_subject(subject or "")
            if norm:
                subject_groups[norm].append((eid, date_sent or ""))

        offset += batch_size

    threads_assigned = 0
    for norm_subject, messages in subject_groups.items():
        if len(messages) < 1:
            continue

        thread_id = _make_thread_id(norm_subject)
        confidence = _subject_confidence(norm_subject)
        messages.sort(key=lambda x: x[1])

        for i, (eid, _) in enumerate(messages):
            parent_eid = messages[i - 1][0] if i > 0 else None
            cursor.execute(
                """UPDATE emails SET thread_id = ?,
                   parent_email_id = COALESCE(parent_email_id, ?),
                   thread_confidence = COALESCE(thread_confidence, ?),
                   thread_method = COALESCE(thread_method, 'subject')
                   WHERE email_id = ?""",
                (thread_id, parent_eid, confidence, eid),
            )
        threads_assigned += 1

    # Handle emails with empty/no subject — each is its own thread
    cursor.execute(
        """UPDATE emails SET thread_id = 'thread_solo_' || CAST(email_id AS TEXT)
           WHERE thread_id IS NULL"""
    )

    conn.commit()

    total_threads = cursor.execute("SELECT COUNT(DISTINCT thread_id) FROM emails").fetchone()[0]
    log.info("threading.done", threads=total_threads, subject_groups=threads_assigned)
    return total_threads

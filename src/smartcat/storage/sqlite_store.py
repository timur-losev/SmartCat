"""SQLite storage layer with FTS5 for SmartCat."""

from __future__ import annotations

import hashlib
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from smartcat.config import SQLITE_DB_PATH
from smartcat.parsing.mime_parser import Attachment, ParsedEmail


def compute_fingerprint(parsed: ParsedEmail) -> str:
    """Compute a stable fingerprint for deduplication.

    Uses sha256 of (from_address + date + subject + body_prefix).
    Two emails with the same fingerprint are considered the same message,
    even if their Message-ID differs or is missing.
    """
    parts = [
        parsed.from_address.lower(),
        parsed.date_sent.isoformat() if parsed.date_sent else "",
        parsed.subject,
        parsed.body_text[:500],
    ]
    raw = "|".join(parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()

_SCHEMA = """
-- Core email table: internal email_id as PK, message_id as external attribute.
-- Dedup by fingerprint (hash of from+date+subject+body_prefix), NOT by message_id alone,
-- because real-world message_ids can be empty, duplicated, or malformed.
CREATE TABLE IF NOT EXISTS emails (
    email_id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id TEXT NOT NULL DEFAULT '',  -- external, may be empty/non-unique
    fingerprint TEXT NOT NULL UNIQUE,     -- sha256(from+date+subject+body[:500])
    date_sent TEXT,  -- ISO 8601
    subject TEXT NOT NULL DEFAULT '',
    body_text TEXT NOT NULL DEFAULT '',
    body_html TEXT,  -- preserved raw HTML for production re-processing
    content_type TEXT NOT NULL DEFAULT 'text/plain',
    from_address TEXT NOT NULL DEFAULT '',
    from_name TEXT NOT NULL DEFAULT '',
    thread_id TEXT,
    parent_email_id INTEGER REFERENCES emails(email_id),
    thread_confidence REAL,  -- 0.0-1.0, NULL=unset; 1.0=header-based, <0.7=subject-heuristic
    thread_method TEXT,      -- 'header', 'subject', 'body_marker', NULL
    has_attachments INTEGER NOT NULL DEFAULT 0,
    has_forwarded_content INTEGER NOT NULL DEFAULT 0,
    has_reply_content INTEGER NOT NULL DEFAULT 0,
    in_reply_to TEXT,
    char_count INTEGER NOT NULL DEFAULT 0
);

-- Multiple instances of same email (sent_items + inbox, etc.)
CREATE TABLE IF NOT EXISTS email_instances (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email_id INTEGER NOT NULL REFERENCES emails(email_id),
    source_path TEXT NOT NULL UNIQUE,
    x_folder TEXT NOT NULL DEFAULT '',
    folder_owner TEXT NOT NULL DEFAULT '',
    x_origin TEXT NOT NULL DEFAULT ''
);

-- Normalized participants
CREATE TABLE IF NOT EXISTS participants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT UNIQUE NOT NULL,
    canonical_name TEXT NOT NULL DEFAULT ''
);

-- Email-participant relationships
CREATE TABLE IF NOT EXISTS email_participants (
    email_id INTEGER NOT NULL REFERENCES emails(email_id),
    participant_id INTEGER NOT NULL REFERENCES participants(id),
    role TEXT NOT NULL CHECK(role IN ('from', 'to', 'cc', 'bcc')),
    PRIMARY KEY (email_id, participant_id, role)
);

-- Extracted entities (dates, amounts, document refs)
CREATE TABLE IF NOT EXISTS entities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email_id INTEGER NOT NULL REFERENCES emails(email_id),
    entity_type TEXT NOT NULL,  -- 'monetary', 'date_ref', 'document_ref', 'deal_id'
    entity_value TEXT NOT NULL,
    context TEXT NOT NULL DEFAULT ''  -- surrounding sentence
);

-- Attachment metadata and binary data
CREATE TABLE IF NOT EXISTS attachments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email_id INTEGER NOT NULL REFERENCES emails(email_id),
    filename TEXT NOT NULL,
    content_type TEXT NOT NULL DEFAULT '',
    file_hash TEXT,
    extracted_text TEXT,
    page_count INTEGER,
    data BLOB  -- raw binary payload from MIME
);

-- Chunks for embedding — carries full citation provenance
CREATE TABLE IF NOT EXISTS chunks (
    chunk_id TEXT PRIMARY KEY,
    email_id INTEGER NOT NULL REFERENCES emails(email_id),
    attachment_id INTEGER REFERENCES attachments(id),
    chunk_type TEXT NOT NULL,  -- 'summary', 'body', 'quoted', 'attachment'
    chunk_index INTEGER NOT NULL DEFAULT 0,
    text TEXT NOT NULL,
    token_count INTEGER NOT NULL DEFAULT 0,
    page_range TEXT,       -- for attachment chunks: "3-5"
    char_offset_start INTEGER,  -- span in source body_text
    char_offset_end INTEGER
);

-- Ingestion tracking
CREATE TABLE IF NOT EXISTS processed_files (
    file_path TEXT PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'done',  -- 'done', 'error'
    processed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS processing_errors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_path TEXT NOT NULL,
    error_type TEXT NOT NULL,
    error_msg TEXT NOT NULL,
    timestamp TEXT NOT NULL
);

-- Full-text search: covers emails AND attachment extracted text
CREATE VIRTUAL TABLE IF NOT EXISTS emails_fts USING fts5(
    subject,
    body_text,
    content='emails',
    content_rowid='email_id'
);

-- Separate FTS for attachment content
CREATE VIRTUAL TABLE IF NOT EXISTS attachments_fts USING fts5(
    filename,
    extracted_text,
    content='attachments',
    content_rowid='id'
);

-- Triggers to keep email FTS in sync
CREATE TRIGGER IF NOT EXISTS emails_ai AFTER INSERT ON emails BEGIN
    INSERT INTO emails_fts(rowid, subject, body_text) VALUES (new.email_id, new.subject, new.body_text);
END;

CREATE TRIGGER IF NOT EXISTS emails_ad AFTER DELETE ON emails BEGIN
    INSERT INTO emails_fts(emails_fts, rowid, subject, body_text) VALUES('delete', old.email_id, old.subject, old.body_text);
END;

CREATE TRIGGER IF NOT EXISTS emails_au AFTER UPDATE ON emails BEGIN
    INSERT INTO emails_fts(emails_fts, rowid, subject, body_text) VALUES('delete', old.email_id, old.subject, old.body_text);
    INSERT INTO emails_fts(rowid, subject, body_text) VALUES (new.email_id, new.subject, new.body_text);
END;

-- Triggers to keep attachment FTS in sync
CREATE TRIGGER IF NOT EXISTS attach_ai AFTER INSERT ON attachments
WHEN new.extracted_text IS NOT NULL BEGIN
    INSERT INTO attachments_fts(rowid, filename, extracted_text) VALUES (new.id, new.filename, new.extracted_text);
END;

CREATE TRIGGER IF NOT EXISTS attach_au AFTER UPDATE ON attachments
WHEN new.extracted_text IS NOT NULL BEGIN
    INSERT INTO attachments_fts(attachments_fts, rowid, filename, extracted_text) VALUES('delete', old.id, old.filename, COALESCE(old.extracted_text, ''));
    INSERT INTO attachments_fts(rowid, filename, extracted_text) VALUES (new.id, new.filename, new.extracted_text);
END;

-- Indexes
CREATE INDEX IF NOT EXISTS idx_emails_date ON emails(date_sent);
CREATE INDEX IF NOT EXISTS idx_emails_thread ON emails(thread_id);
CREATE INDEX IF NOT EXISTS idx_emails_from ON emails(from_address);
CREATE INDEX IF NOT EXISTS idx_emails_msgid ON emails(message_id);
CREATE INDEX IF NOT EXISTS idx_emails_fp ON emails(fingerprint);
CREATE INDEX IF NOT EXISTS idx_instances_eid ON email_instances(email_id);
CREATE INDEX IF NOT EXISTS idx_participants_email ON participants(email);
CREATE INDEX IF NOT EXISTS idx_ep_eid ON email_participants(email_id);
CREATE INDEX IF NOT EXISTS idx_ep_pid ON email_participants(participant_id);
CREATE INDEX IF NOT EXISTS idx_entities_type ON entities(entity_type, email_id);
CREATE INDEX IF NOT EXISTS idx_chunks_eid ON chunks(email_id);
CREATE INDEX IF NOT EXISTS idx_attach_eid ON attachments(email_id);
CREATE INDEX IF NOT EXISTS idx_processed ON processed_files(status);
"""


class EmailStore:
    """SQLite-backed storage for parsed emails and metadata."""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or SQLITE_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: Optional[sqlite3.Connection] = None

    def connect(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(str(self.db_path), timeout=30)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.execute("PRAGMA cache_size=-64000")  # 64MB cache
            self._conn.execute("PRAGMA foreign_keys=ON")
        return self._conn

    def init_schema(self):
        """Create all tables and indexes."""
        conn = self.connect()
        conn.executescript(_SCHEMA)
        conn.commit()

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None

    @contextmanager
    def transaction(self):
        conn = self.connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    # -- Participant management --

    def get_or_create_participant(self, email_addr: str, name: str = "") -> int:
        """Get or create a participant, returning their ID."""
        conn = self.connect()
        row = conn.execute(
            "SELECT id, canonical_name FROM participants WHERE email = ?",
            (email_addr.lower(),),
        ).fetchone()
        if row:
            # Update name if we have a better one
            if name and (not row["canonical_name"] or len(name) > len(row["canonical_name"])):
                conn.execute(
                    "UPDATE participants SET canonical_name = ? WHERE id = ?",
                    (name, row["id"]),
                )
            return row["id"]
        cursor = conn.execute(
            "INSERT INTO participants (email, canonical_name) VALUES (?, ?)",
            (email_addr.lower(), name),
        )
        return cursor.lastrowid

    # -- Email insertion --

    def insert_email(self, parsed: ParsedEmail) -> tuple[int, bool]:
        """Insert a parsed email. Returns (email_id, is_new).

        Deduplicates by fingerprint (not message_id).
        For duplicates, still inserts an email_instance record.
        """
        conn = self.connect()
        fp = compute_fingerprint(parsed)

        # Check if email already exists by fingerprint
        existing = conn.execute(
            "SELECT email_id FROM emails WHERE fingerprint = ?", (fp,)
        ).fetchone()

        email_id: int
        is_new = existing is None

        if is_new:
            date_str = parsed.date_sent.isoformat() if parsed.date_sent else None
            cursor = conn.execute(
                """INSERT INTO emails (
                    message_id, fingerprint, date_sent, subject, body_text,
                    body_html, content_type, from_address, from_name,
                    has_attachments, has_forwarded_content, has_reply_content,
                    in_reply_to, char_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    parsed.message_id,
                    fp,
                    date_str,
                    parsed.subject,
                    parsed.body_text,
                    parsed.body_html if hasattr(parsed, "body_html") else None,
                    parsed.content_type,
                    parsed.from_address.lower(),
                    parsed.from_name,
                    int(parsed.has_attachments),
                    int(parsed.has_forwarded_content),
                    int(parsed.has_reply_content),
                    parsed.in_reply_to or None,
                    len(parsed.body_text),
                ),
            )
            email_id = cursor.lastrowid

            # Insert participants
            if parsed.from_address:
                pid = self.get_or_create_participant(parsed.from_address, parsed.from_name)
                conn.execute(
                    "INSERT OR IGNORE INTO email_participants VALUES (?, ?, 'from')",
                    (email_id, pid),
                )

            for addr, name in zip(parsed.to_addresses, parsed.to_names or parsed.to_addresses):
                if addr:
                    pid = self.get_or_create_participant(addr, name)
                    conn.execute(
                        "INSERT OR IGNORE INTO email_participants VALUES (?, ?, 'to')",
                        (email_id, pid),
                    )

            for addr, name in zip(parsed.cc_addresses, parsed.cc_names or parsed.cc_addresses):
                if addr:
                    pid = self.get_or_create_participant(addr, name)
                    conn.execute(
                        "INSERT OR IGNORE INTO email_participants VALUES (?, ?, 'cc')",
                        (email_id, pid),
                    )

            for addr in parsed.bcc_addresses:
                if addr:
                    pid = self.get_or_create_participant(addr)
                    conn.execute(
                        "INSERT OR IGNORE INTO email_participants VALUES (?, ?, 'bcc')",
                        (email_id, pid),
                    )

            # Insert binary MIME attachments
            for att in getattr(parsed, "attachments", []):
                file_hash = hashlib.sha256(att.data).hexdigest() if att.data else None
                conn.execute(
                    """INSERT INTO attachments (email_id, filename, content_type, file_hash, data)
                       VALUES (?, ?, ?, ?, ?)""",
                    (email_id, att.filename, att.content_type, file_hash, att.data),
                )

            # Insert attachment references from body (text-only refs like << File: ... >>)
            for filename in parsed.referenced_files:
                conn.execute(
                    "INSERT INTO attachments (email_id, filename) VALUES (?, ?)",
                    (email_id, filename),
                )
        else:
            email_id = existing["email_id"]

        # Always insert the instance (tracks which folder/file this came from)
        folder_owner = ""
        path_parts = Path(parsed.source_path).parts
        try:
            maildir_idx = list(path_parts).index("maildir")
            if maildir_idx + 1 < len(path_parts):
                folder_owner = path_parts[maildir_idx + 1]
        except ValueError:
            pass

        conn.execute(
            """INSERT OR IGNORE INTO email_instances
               (email_id, source_path, x_folder, folder_owner, x_origin)
               VALUES (?, ?, ?, ?, ?)""",
            (
                email_id,
                parsed.source_path,
                parsed.x_folder,
                folder_owner,
                parsed.x_origin,
            ),
        )

        return email_id, is_new

    # -- Ingestion tracking --

    def is_file_processed(self, file_path: str) -> bool:
        conn = self.connect()
        row = conn.execute(
            "SELECT status FROM processed_files WHERE file_path = ?", (file_path,)
        ).fetchone()
        return row is not None

    def mark_file_processed(self, file_path: str, status: str = "done"):
        conn = self.connect()
        conn.execute(
            "INSERT OR REPLACE INTO processed_files VALUES (?, ?, ?)",
            (file_path, status, datetime.now().isoformat()),
        )

    def log_error(self, file_path: str, error_type: str, error_msg: str):
        conn = self.connect()
        conn.execute(
            "INSERT INTO processing_errors (file_path, error_type, error_msg, timestamp) VALUES (?, ?, ?, ?)",
            (file_path, error_type, error_msg, datetime.now().isoformat()),
        )

    # -- Query methods --

    def get_email(self, email_id: int) -> Optional[dict]:
        conn = self.connect()
        row = conn.execute("SELECT * FROM emails WHERE email_id = ?", (email_id,)).fetchone()
        return dict(row) if row else None

    def get_email_by_message_id(self, message_id: str) -> Optional[dict]:
        """Lookup by external message_id (may return first match if non-unique)."""
        conn = self.connect()
        row = conn.execute(
            "SELECT * FROM emails WHERE message_id = ? LIMIT 1", (message_id,)
        ).fetchone()
        return dict(row) if row else None

    def get_thread(self, thread_id: str) -> list[dict]:
        conn = self.connect()
        rows = conn.execute(
            "SELECT * FROM emails WHERE thread_id = ? ORDER BY date_sent",
            (thread_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def search_fts(self, query: str, limit: int = 60) -> list[dict]:
        """Full-text search using FTS5 BM25 ranking.

        Searches both email body/subject AND attachment extracted text,
        then merges results by email_id.
        """
        conn = self.connect()
        # Search emails
        email_rows = conn.execute(
            """SELECT emails.*, rank
               FROM emails_fts
               JOIN emails ON emails.email_id = emails_fts.rowid
               WHERE emails_fts MATCH ?
               ORDER BY rank
               LIMIT ?""",
            (query, limit),
        ).fetchall()

        # Search attachments
        attach_rows = conn.execute(
            """SELECT a.email_id, a.filename, e.*, a.id as match_attachment_id
               FROM attachments_fts afts
               JOIN attachments a ON a.id = afts.rowid
               JOIN emails e ON e.email_id = a.email_id
               WHERE attachments_fts MATCH ?
               LIMIT ?""",
            (query, limit),
        ).fetchall()

        # Merge: email results first, then attachment matches (dedup by email_id)
        seen_ids: set[int] = set()
        results = []
        for row in email_rows:
            d = dict(row)
            eid = d["email_id"]
            if eid not in seen_ids:
                seen_ids.add(eid)
                d["_match_source"] = "email"
                results.append(d)
        for row in attach_rows:
            d = dict(row)
            eid = d["email_id"]
            if eid not in seen_ids:
                seen_ids.add(eid)
                d["_match_source"] = "attachment"
                results.append(d)

        return results[:limit]

    def search_by_participant(self, name_or_email: str, limit: int = 100) -> list[dict]:
        """Find emails involving a participant."""
        conn = self.connect()
        pattern = f"%{name_or_email.lower()}%"
        rows = conn.execute(
            """SELECT DISTINCT e.*
               FROM emails e
               JOIN email_participants ep ON e.email_id = ep.email_id
               JOIN participants p ON ep.participant_id = p.id
               WHERE p.email LIKE ? OR LOWER(p.canonical_name) LIKE ?
               ORDER BY e.date_sent DESC
               LIMIT ?""",
            (pattern, pattern, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def search_by_date_range(
        self, start: str, end: str, query: Optional[str] = None, limit: int = 100
    ) -> list[dict]:
        """Find emails within a date range, optionally with FTS query."""
        conn = self.connect()
        if query:
            rows = conn.execute(
                """SELECT e.*, fts.rank
                   FROM emails_fts fts
                   JOIN emails e ON e.email_id = fts.rowid
                   WHERE fts MATCH ?
                     AND e.date_sent >= ? AND e.date_sent <= ?
                   ORDER BY fts.rank
                   LIMIT ?""",
                (query, start, end, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT * FROM emails
                   WHERE date_sent >= ? AND date_sent <= ?
                   ORDER BY date_sent DESC
                   LIMIT ?""",
                (start, end, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def search_entities(
        self, entity_type: str, value_pattern: str, limit: int = 100
    ) -> list[dict]:
        """Find emails containing specific entity types/values."""
        conn = self.connect()
        rows = conn.execute(
            """SELECT DISTINCT e.*, ent.entity_value, ent.context
               FROM entities ent
               JOIN emails e ON ent.email_id = e.email_id
               WHERE ent.entity_type = ? AND ent.entity_value LIKE ?
               ORDER BY e.date_sent DESC
               LIMIT ?""",
            (entity_type, f"%{value_pattern}%", limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_stats(self, filters: Optional[dict] = None) -> dict:
        """Get aggregate statistics about the email corpus."""
        conn = self.connect()
        where_clauses = []
        params = []
        if filters:
            if "from_address" in filters:
                where_clauses.append("from_address LIKE ?")
                params.append(f"%{filters['from_address']}%")
            if "date_start" in filters:
                where_clauses.append("date_sent >= ?")
                params.append(filters["date_start"])
            if "date_end" in filters:
                where_clauses.append("date_sent <= ?")
                params.append(filters["date_end"])

        where = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

        row = conn.execute(
            f"""SELECT
                COUNT(*) as total_emails,
                MIN(date_sent) as earliest,
                MAX(date_sent) as latest,
                COUNT(DISTINCT from_address) as unique_senders,
                SUM(has_attachments) as with_attachments,
                AVG(char_count) as avg_length
            FROM emails {where}""",
            params,
        ).fetchone()
        return dict(row) if row else {}

    def get_participant_count(self) -> int:
        conn = self.connect()
        row = conn.execute("SELECT COUNT(*) as cnt FROM participants").fetchone()
        return row["cnt"]

    def get_email_count(self) -> int:
        conn = self.connect()
        row = conn.execute("SELECT COUNT(*) as cnt FROM emails").fetchone()
        return row["cnt"]

    def get_instance_count(self) -> int:
        conn = self.connect()
        row = conn.execute("SELECT COUNT(*) as cnt FROM email_instances").fetchone()
        return row["cnt"]

    def get_error_count(self) -> int:
        conn = self.connect()
        row = conn.execute("SELECT COUNT(*) as cnt FROM processing_errors").fetchone()
        return row["cnt"]

    def get_chunk_count(self) -> int:
        conn = self.connect()
        row = conn.execute("SELECT COUNT(*) as cnt FROM chunks").fetchone()
        return row["cnt"]

    # -- Chunk methods --

    def insert_chunks(self, chunks: list[dict]) -> int:
        """Batch insert chunks. Each dict must have: chunk_id, email_id, chunk_type,
        chunk_index, text, token_count. Optional: attachment_id, page_range,
        char_offset_start, char_offset_end.

        Returns number of chunks inserted.
        """
        if not chunks:
            return 0
        conn = self.connect()
        conn.executemany(
            """INSERT OR IGNORE INTO chunks
               (chunk_id, email_id, attachment_id, chunk_type, chunk_index,
                text, token_count, page_range, char_offset_start, char_offset_end)
               VALUES (:chunk_id, :email_id, :attachment_id, :chunk_type, :chunk_index,
                       :text, :token_count, :page_range, :char_offset_start, :char_offset_end)""",
            [
                {
                    "chunk_id": c["chunk_id"],
                    "email_id": c["email_id"],
                    "attachment_id": c.get("attachment_id"),
                    "chunk_type": c["chunk_type"],
                    "chunk_index": c["chunk_index"],
                    "text": c["text"],
                    "token_count": c["token_count"],
                    "page_range": c.get("page_range"),
                    "char_offset_start": c.get("char_offset_start"),
                    "char_offset_end": c.get("char_offset_end"),
                }
                for c in chunks
            ],
        )
        return len(chunks)

    def get_emails_without_chunks(self, limit: int = 10000) -> list[dict]:
        """Get emails that haven't been chunked yet."""
        conn = self.connect()
        rows = conn.execute(
            """SELECT e.email_id, e.message_id, e.subject, e.body_text,
                      e.from_address, e.from_name, e.date_sent,
                      e.thread_id, e.has_attachments
               FROM emails e
               LEFT JOIN chunks c ON e.email_id = c.email_id
               WHERE c.chunk_id IS NULL
               LIMIT ?""",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    # -- Docling conversion methods --

    def get_html_emails_for_conversion(self, limit: int = 10000) -> list[dict]:
        """Get emails with HTML content that need conversion to clean text."""
        conn = self.connect()
        rows = conn.execute(
            """SELECT email_id, body_text, body_html, content_type
               FROM emails
               WHERE content_type = 'text/html'
                  OR body_html IS NOT NULL
               LIMIT ?""",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def update_email_body(self, email_id: int, body_text: str):
        """Update email body_text after HTML→markdown conversion."""
        conn = self.connect()
        conn.execute(
            "UPDATE emails SET body_text = ?, char_count = ? WHERE email_id = ?",
            (body_text, len(body_text), email_id),
        )

    def get_attachments_without_text(self, limit: int = 10000) -> list[dict]:
        """Get attachments that have binary data but no extracted text."""
        conn = self.connect()
        rows = conn.execute(
            """SELECT id, email_id, filename, content_type, data
               FROM attachments
               WHERE extracted_text IS NULL AND data IS NOT NULL
               LIMIT ?""",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def update_attachment_text(self, attachment_id: int, extracted_text: str, page_count: int = 0):
        """Update attachment with extracted text from Docling."""
        conn = self.connect()
        conn.execute(
            "UPDATE attachments SET extracted_text = ?, page_count = ? WHERE id = ?",
            (extracted_text, page_count, attachment_id),
        )

    def get_attachment_count(self) -> dict:
        """Get attachment stats."""
        conn = self.connect()
        row = conn.execute(
            """SELECT
                COUNT(*) as total,
                SUM(CASE WHEN data IS NOT NULL THEN 1 ELSE 0 END) as with_data,
                SUM(CASE WHEN extracted_text IS NOT NULL THEN 1 ELSE 0 END) as with_text
               FROM attachments"""
        ).fetchone()
        return dict(row) if row else {}

    def insert_attachment(self, email_id: int, filename: str, content_type: str,
                          data: bytes, file_hash: str = "") -> int:
        """Insert a single attachment with binary data. Returns attachment id."""
        conn = self.connect()
        cursor = conn.execute(
            """INSERT INTO attachments (email_id, filename, content_type, file_hash, data)
               VALUES (?, ?, ?, ?, ?)""",
            (email_id, filename, content_type, file_hash, data),
        )
        return cursor.lastrowid

    def get_chunks_for_embedding(self, limit: int = 10000, offset: int = 0) -> list[dict]:
        """Get chunks that need embedding (for batch embedding pipeline)."""
        conn = self.connect()
        rows = conn.execute(
            """SELECT c.chunk_id, c.email_id, c.chunk_type, c.chunk_index, c.text,
                      c.token_count, e.message_id, e.date_sent, e.from_address,
                      e.thread_id, e.has_attachments
               FROM chunks c
               JOIN emails e ON c.email_id = e.email_id
               ORDER BY c.email_id, c.chunk_index
               LIMIT ? OFFSET ?""",
            (limit, offset),
        ).fetchall()
        return [dict(r) for r in rows]

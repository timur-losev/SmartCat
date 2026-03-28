"""SQLite storage layer with FTS5 for SmartCat."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from smartcat.config import SQLITE_DB_PATH
from smartcat.parsing.mime_parser import ParsedEmail

_SCHEMA = """
-- Core email table (one row per unique Message-ID)
CREATE TABLE IF NOT EXISTS emails (
    message_id TEXT PRIMARY KEY,
    date_sent TEXT,  -- ISO 8601
    subject TEXT NOT NULL DEFAULT '',
    body_text TEXT NOT NULL DEFAULT '',
    content_type TEXT NOT NULL DEFAULT 'text/plain',
    from_address TEXT NOT NULL DEFAULT '',
    from_name TEXT NOT NULL DEFAULT '',
    thread_id TEXT,
    parent_message_id TEXT,
    has_attachments INTEGER NOT NULL DEFAULT 0,
    has_forwarded_content INTEGER NOT NULL DEFAULT 0,
    has_reply_content INTEGER NOT NULL DEFAULT 0,
    in_reply_to TEXT,
    char_count INTEGER NOT NULL DEFAULT 0
);

-- Multiple instances of same email (sent_items + inbox, etc.)
CREATE TABLE IF NOT EXISTS email_instances (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id TEXT NOT NULL REFERENCES emails(message_id),
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
    message_id TEXT NOT NULL REFERENCES emails(message_id),
    participant_id INTEGER NOT NULL REFERENCES participants(id),
    role TEXT NOT NULL CHECK(role IN ('from', 'to', 'cc', 'bcc')),
    PRIMARY KEY (message_id, participant_id, role)
);

-- Extracted entities (dates, amounts, document refs)
CREATE TABLE IF NOT EXISTS entities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id TEXT NOT NULL REFERENCES emails(message_id),
    entity_type TEXT NOT NULL,  -- 'monetary', 'date_ref', 'document_ref', 'deal_id'
    entity_value TEXT NOT NULL,
    context TEXT NOT NULL DEFAULT ''  -- surrounding sentence
);

-- Attachment metadata
CREATE TABLE IF NOT EXISTS attachments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id TEXT NOT NULL REFERENCES emails(message_id),
    filename TEXT NOT NULL,
    content_type TEXT NOT NULL DEFAULT '',
    file_hash TEXT,
    extracted_text TEXT,
    page_count INTEGER
);

-- Chunks for embedding
CREATE TABLE IF NOT EXISTS chunks (
    chunk_id TEXT PRIMARY KEY,
    message_id TEXT NOT NULL REFERENCES emails(message_id),
    attachment_id INTEGER REFERENCES attachments(id),
    chunk_type TEXT NOT NULL,  -- 'summary', 'body', 'quoted', 'attachment'
    chunk_index INTEGER NOT NULL DEFAULT 0,
    text TEXT NOT NULL,
    token_count INTEGER NOT NULL DEFAULT 0
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

-- Full-text search index
CREATE VIRTUAL TABLE IF NOT EXISTS emails_fts USING fts5(
    subject,
    body_text,
    content='emails',
    content_rowid='rowid'
);

-- Triggers to keep FTS in sync
CREATE TRIGGER IF NOT EXISTS emails_ai AFTER INSERT ON emails BEGIN
    INSERT INTO emails_fts(rowid, subject, body_text) VALUES (new.rowid, new.subject, new.body_text);
END;

CREATE TRIGGER IF NOT EXISTS emails_ad AFTER DELETE ON emails BEGIN
    INSERT INTO emails_fts(emails_fts, rowid, subject, body_text) VALUES('delete', old.rowid, old.subject, old.body_text);
END;

CREATE TRIGGER IF NOT EXISTS emails_au AFTER UPDATE ON emails BEGIN
    INSERT INTO emails_fts(emails_fts, rowid, subject, body_text) VALUES('delete', old.rowid, old.subject, old.body_text);
    INSERT INTO emails_fts(rowid, subject, body_text) VALUES (new.rowid, new.subject, new.body_text);
END;

-- Indexes
CREATE INDEX IF NOT EXISTS idx_emails_date ON emails(date_sent);
CREATE INDEX IF NOT EXISTS idx_emails_thread ON emails(thread_id);
CREATE INDEX IF NOT EXISTS idx_emails_from ON emails(from_address);
CREATE INDEX IF NOT EXISTS idx_instances_msgid ON email_instances(message_id);
CREATE INDEX IF NOT EXISTS idx_participants_email ON participants(email);
CREATE INDEX IF NOT EXISTS idx_ep_msgid ON email_participants(message_id);
CREATE INDEX IF NOT EXISTS idx_ep_pid ON email_participants(participant_id);
CREATE INDEX IF NOT EXISTS idx_entities_type ON entities(entity_type, message_id);
CREATE INDEX IF NOT EXISTS idx_chunks_msgid ON chunks(message_id);
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

    def insert_email(self, parsed: ParsedEmail) -> bool:
        """Insert a parsed email. Returns True if new email, False if duplicate.

        For duplicates, still inserts an email_instance record.
        """
        conn = self.connect()

        # Check if email already exists
        existing = conn.execute(
            "SELECT message_id FROM emails WHERE message_id = ?",
            (parsed.message_id,),
        ).fetchone()

        if not existing:
            date_str = parsed.date_sent.isoformat() if parsed.date_sent else None
            conn.execute(
                """INSERT INTO emails (
                    message_id, date_sent, subject, body_text, content_type,
                    from_address, from_name, has_attachments,
                    has_forwarded_content, has_reply_content,
                    in_reply_to, char_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    parsed.message_id,
                    date_str,
                    parsed.subject,
                    parsed.body_text,
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

            # Insert participants
            if parsed.from_address:
                pid = self.get_or_create_participant(parsed.from_address, parsed.from_name)
                conn.execute(
                    "INSERT OR IGNORE INTO email_participants VALUES (?, ?, 'from')",
                    (parsed.message_id, pid),
                )

            for addr, name in zip(parsed.to_addresses, parsed.to_names or parsed.to_addresses):
                if addr:
                    pid = self.get_or_create_participant(addr, name)
                    conn.execute(
                        "INSERT OR IGNORE INTO email_participants VALUES (?, ?, 'to')",
                        (parsed.message_id, pid),
                    )

            for addr, name in zip(parsed.cc_addresses, parsed.cc_names or parsed.cc_addresses):
                if addr:
                    pid = self.get_or_create_participant(addr, name)
                    conn.execute(
                        "INSERT OR IGNORE INTO email_participants VALUES (?, ?, 'cc')",
                        (parsed.message_id, pid),
                    )

            for addr in parsed.bcc_addresses:
                if addr:
                    pid = self.get_or_create_participant(addr)
                    conn.execute(
                        "INSERT OR IGNORE INTO email_participants VALUES (?, ?, 'bcc')",
                        (parsed.message_id, pid),
                    )

            # Insert attachment references from body
            for filename in parsed.referenced_files:
                conn.execute(
                    "INSERT INTO attachments (message_id, filename) VALUES (?, ?)",
                    (parsed.message_id, filename),
                )

        # Always insert the instance (tracks which folder/file this came from)
        # Derive folder_owner from path: maildir/{owner}/...
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
               (message_id, source_path, x_folder, folder_owner, x_origin)
               VALUES (?, ?, ?, ?, ?)""",
            (
                parsed.message_id,
                parsed.source_path,
                parsed.x_folder,
                folder_owner,
                parsed.x_origin,
            ),
        )

        return not existing

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

    def get_email(self, message_id: str) -> Optional[dict]:
        conn = self.connect()
        row = conn.execute("SELECT * FROM emails WHERE message_id = ?", (message_id,)).fetchone()
        return dict(row) if row else None

    def get_thread(self, thread_id: str) -> list[dict]:
        conn = self.connect()
        rows = conn.execute(
            "SELECT * FROM emails WHERE thread_id = ? ORDER BY date_sent",
            (thread_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def search_fts(self, query: str, limit: int = 60) -> list[dict]:
        """Full-text search using FTS5 BM25 ranking."""
        conn = self.connect()
        rows = conn.execute(
            """SELECT emails.*, rank
               FROM emails_fts
               JOIN emails ON emails.rowid = emails_fts.rowid
               WHERE emails_fts MATCH ?
               ORDER BY rank
               LIMIT ?""",
            (query, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def search_by_participant(self, name_or_email: str, limit: int = 100) -> list[dict]:
        """Find emails involving a participant."""
        conn = self.connect()
        pattern = f"%{name_or_email.lower()}%"
        rows = conn.execute(
            """SELECT DISTINCT e.*
               FROM emails e
               JOIN email_participants ep ON e.message_id = ep.message_id
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
                   JOIN emails e ON e.rowid = fts.rowid
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
               JOIN emails e ON ent.message_id = e.message_id
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

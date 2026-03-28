"""One-time migration: extract binary MIME attachments from maildir into SQLite.

Re-reads .eml files for emails with has_attachments=1 and inserts binary
payloads into the attachments table. Skips emails that already have
attachment data. Resumable via processed tracking.

Usage:
    python scripts/extract_attachments.py --db data/smartcat.db --maildir maildir/
"""

from __future__ import annotations

import argparse
import email
import hashlib
import sys
from pathlib import Path

from tqdm import tqdm

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from smartcat.storage.sqlite_store import EmailStore


def extract_mime_attachments(file_path: Path) -> list[dict]:
    """Extract binary attachments from an email file.

    Returns list of dicts with: filename, content_type, data, file_hash.
    """
    raw_bytes = file_path.read_bytes()
    try:
        raw_text = raw_bytes.decode("utf-8", errors="replace")
    except Exception:
        raw_text = raw_bytes.decode("latin-1", errors="replace")

    msg = email.message_from_string(raw_text)
    attachments = []

    if not msg.is_multipart():
        return attachments

    for part in msg.walk():
        disp = str(part.get("Content-Disposition", ""))
        ct = part.get_content_type()

        # Skip text body parts
        if ct in ("text/plain", "text/html") and "attachment" not in disp:
            continue
        if part.get_content_maintype() == "multipart":
            continue

        if "attachment" in disp or ct not in ("text/plain", "text/html"):
            payload = part.get_payload(decode=True)
            if payload and len(payload) > 0:
                filename = part.get_filename() or ""
                file_hash = hashlib.sha256(payload).hexdigest()
                attachments.append({
                    "filename": filename,
                    "content_type": ct,
                    "data": payload,
                    "file_hash": file_hash,
                })

    return attachments


def main():
    parser = argparse.ArgumentParser(description="Extract MIME attachments from maildir into SQLite")
    parser.add_argument("--db", required=True, help="Path to SQLite database")
    parser.add_argument("--maildir", required=True, help="Path to maildir root")
    parser.add_argument("--batch-size", type=int, default=1000, help="Commit every N emails")
    args = parser.parse_args()

    store = EmailStore(Path(args.db))
    conn = store.connect()

    # Migrate schema: add data column if not present
    try:
        conn.execute("SELECT data FROM attachments LIMIT 0")
    except Exception:
        conn.execute("ALTER TABLE attachments ADD COLUMN data BLOB")
        conn.commit()
        print("Migrated: added 'data' BLOB column to attachments table")

    # Get emails with attachments that need extraction
    # Find emails where has_attachments=1 but no attachment rows have data
    rows = conn.execute("""
        SELECT DISTINCT e.email_id, ei.source_path
        FROM emails e
        JOIN email_instances ei ON e.email_id = ei.email_id
        WHERE e.has_attachments = 1
          AND NOT EXISTS (
              SELECT 1 FROM attachments a
              WHERE a.email_id = e.email_id AND a.data IS NOT NULL
          )
    """).fetchall()

    print(f"Found {len(rows)} emails with attachments to process")

    total_attachments = 0
    errors = 0
    pbar = tqdm(rows, desc="Extracting attachments", unit="email")

    for i, row in enumerate(pbar):
        email_id = row["email_id"]
        source_path = Path(row["source_path"])

        if not source_path.exists():
            # Try relative to maildir
            source_path = Path(args.maildir) / source_path
            if not source_path.exists():
                errors += 1
                continue

        try:
            attachments = extract_mime_attachments(source_path)
            for att in attachments:
                # Check if this exact attachment already exists (by hash)
                existing = conn.execute(
                    "SELECT id FROM attachments WHERE email_id = ? AND file_hash = ?",
                    (email_id, att["file_hash"]),
                ).fetchone()

                if existing:
                    # Update existing row with binary data
                    conn.execute(
                        "UPDATE attachments SET data = ?, content_type = ? WHERE id = ?",
                        (att["data"], att["content_type"], existing["id"]),
                    )
                else:
                    conn.execute(
                        """INSERT INTO attachments (email_id, filename, content_type, file_hash, data)
                           VALUES (?, ?, ?, ?, ?)""",
                        (email_id, att["filename"], att["content_type"], att["file_hash"], att["data"]),
                    )
                total_attachments += 1
        except Exception as e:
            errors += 1
            pbar.set_postfix(err=str(e)[:40])
            continue

        if (i + 1) % args.batch_size == 0:
            conn.commit()
            pbar.set_postfix(attachments=total_attachments, errors=errors)

    conn.commit()
    store.close()

    print(f"\nDone: {total_attachments} attachments extracted, {errors} errors")
    print(f"Verify: sqlite3 {args.db} \"SELECT COUNT(*) FROM attachments WHERE data IS NOT NULL\"")


if __name__ == "__main__":
    main()

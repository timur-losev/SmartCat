"""Batch ingest all emails from maildir into SQLite.

Usage:
    python scripts/ingest_maildir.py [--maildir PATH] [--db PATH] [--workers N]

Features:
- Resumable: skips already-processed files
- Error-tolerant: logs errors and continues
- Progress tracking with tqdm
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Add src to path for direct script execution
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from tqdm import tqdm

import structlog
from smartcat.config import MAILDIR_PATH, SQLITE_DB_PATH, INGESTION_WORKERS
from smartcat.parsing.metadata import extract_entities
from smartcat.parsing.mime_parser import parse_email_file
from smartcat.parsing.threading import reconstruct_threads
from smartcat.storage.sqlite_store import EmailStore

log = structlog.get_logger()


def discover_email_files(maildir: Path) -> list[Path]:
    """Find all email files in the maildir structure."""
    files = []
    for user_dir in sorted(maildir.iterdir()):
        if not user_dir.is_dir():
            continue
        for folder_dir in sorted(user_dir.iterdir()):
            if not folder_dir.is_dir():
                continue
            for email_file in sorted(folder_dir.iterdir()):
                if email_file.is_file():
                    files.append(email_file)
    return files


def ingest_single(store: EmailStore, file_path: Path) -> bool:
    """Ingest a single email file. Returns True if successful."""
    str_path = str(file_path)

    if store.is_file_processed(str_path):
        return True

    try:
        parsed = parse_email_file(file_path)
        email_id, is_new = store.insert_email(parsed)

        # Extract entities only for new emails (not duplicates)
        if is_new:
            entities = extract_entities(parsed.body_text)
            conn = store.connect()
            for ent in entities:
                conn.execute(
                    "INSERT INTO entities (email_id, entity_type, entity_value, context) VALUES (?, ?, ?, ?)",
                    (email_id, ent.entity_type, ent.entity_value, ent.context),
                )

        store.mark_file_processed(str_path, "done")
        return True

    except Exception as e:
        error_type = type(e).__name__
        error_msg = str(e)[:500]
        store.log_error(str_path, error_type, error_msg)
        store.mark_file_processed(str_path, "error")
        return False


def main():
    parser = argparse.ArgumentParser(description="Ingest maildir into SQLite")
    parser.add_argument("--maildir", type=Path, default=MAILDIR_PATH)
    parser.add_argument("--db", type=Path, default=SQLITE_DB_PATH)
    parser.add_argument("--skip-threading", action="store_true", help="Skip thread reconstruction")
    parser.add_argument("--limit", type=int, default=0, help="Limit number of files (0=all)")
    args = parser.parse_args()

    log.info("ingestion.start", maildir=str(args.maildir), db=str(args.db))

    # Init store
    store = EmailStore(args.db)
    store.init_schema()

    # Discover files
    print(f"Discovering email files in {args.maildir}...")
    files = discover_email_files(args.maildir)
    if args.limit > 0:
        files = files[:args.limit]
    print(f"Found {len(files):,} email files")

    # Ingest with progress bar
    success = 0
    errors = 0
    skipped = 0
    commit_every = 500

    with tqdm(total=len(files), desc="Ingesting", unit="email") as pbar:
        for i, file_path in enumerate(files):
            if store.is_file_processed(str(file_path)):
                skipped += 1
                pbar.update(1)
                continue

            ok = ingest_single(store, file_path)
            if ok:
                success += 1
            else:
                errors += 1
            pbar.update(1)

            # Commit periodically
            if (i + 1) % commit_every == 0:
                store.connect().commit()

    # Final commit
    store.connect().commit()

    print(f"\nIngestion complete:")
    print(f"  New:     {success:,}")
    print(f"  Skipped: {skipped:,}")
    print(f"  Errors:  {errors:,}")
    print(f"  Total emails in DB: {store.get_email_count():,}")
    print(f"  Total instances:    {store.get_instance_count():,}")
    print(f"  Total participants: {store.get_participant_count():,}")
    print(f"  Total errors:       {store.get_error_count():,}")

    # Thread reconstruction
    if not args.skip_threading:
        print("\nReconstructing email threads...")
        conn = store.connect()
        total_threads = reconstruct_threads(conn)
        print(f"  Threads: {total_threads:,}")

    store.close()
    log.info("ingestion.done")


if __name__ == "__main__":
    main()

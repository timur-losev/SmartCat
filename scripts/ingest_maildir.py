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
from smartcat.parsing.mbox_parser import iter_mbox, discover_mbox_files
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


def ingest_mbox(store: EmailStore, mbox_path: Path, limit: int = 0) -> tuple[int, int, int]:
    """Ingest all messages from a single mbox file.

    Returns:
        (success_count, error_count, skipped_count)
    """
    success = 0
    errors = 0
    skipped = 0
    commit_every = 500

    for i, parsed in enumerate(iter_mbox(mbox_path)):
        if limit > 0 and (success + errors + skipped) >= limit:
            break

        source_key = f"mbox:{mbox_path}:{parsed.message_id}"
        if store.is_file_processed(source_key):
            skipped += 1
            continue

        try:
            email_id, is_new = store.insert_email(parsed)
            if is_new:
                entities = extract_entities(parsed.body_text)
                conn = store.connect()
                for ent in entities:
                    conn.execute(
                        "INSERT INTO entities (email_id, entity_type, entity_value, context) VALUES (?, ?, ?, ?)",
                        (email_id, ent.entity_type, ent.entity_value, ent.context),
                    )
            store.mark_file_processed(source_key, "done")
            success += 1
        except Exception as e:
            store.log_error(source_key, type(e).__name__, str(e)[:500])
            store.mark_file_processed(source_key, "error")
            errors += 1

        if (i + 1) % commit_every == 0:
            store.connect().commit()

    store.connect().commit()
    return success, errors, skipped


def main():
    parser = argparse.ArgumentParser(description="Ingest emails into SQLite")
    parser.add_argument("--maildir", type=Path, default=MAILDIR_PATH)
    parser.add_argument("--db", type=Path, default=SQLITE_DB_PATH)
    parser.add_argument("--skip-threading", action="store_true", help="Skip thread reconstruction")
    parser.add_argument("--limit", type=int, default=0, help="Limit number of files (0=all)")
    parser.add_argument("--format", choices=["maildir", "mbox"], default="maildir",
                        help="Input format: maildir (default) or mbox")
    args = parser.parse_args()

    log.info("ingestion.start", source=str(args.maildir), db=str(args.db), format=args.format)

    # Init store
    store = EmailStore(args.db)
    store.init_schema()

    if args.format == "mbox":
        # Mbox mode: find .mbox files and iterate messages
        source = args.maildir
        if source.is_file() and source.suffix.lower() == ".mbox":
            mbox_files = [source]
        elif source.is_dir():
            mbox_files = discover_mbox_files(source)
        else:
            print(f"ERROR: {source} is not an mbox file or directory")
            return

        print(f"Found {len(mbox_files)} mbox file(s)")
        total_success = 0
        total_errors = 0
        total_skipped = 0

        for mbox_path in mbox_files:
            print(f"Ingesting {mbox_path.name}...")
            s, e, sk = ingest_mbox(store, mbox_path, args.limit)
            total_success += s
            total_errors += e
            total_skipped += sk

        print(f"\nIngestion complete (mbox):")
        print(f"  New:     {total_success:,}")
        print(f"  Skipped: {total_skipped:,}")
        print(f"  Errors:  {total_errors:,}")

    else:
        # Maildir mode (original behavior)
        print(f"Discovering email files in {args.maildir}...")
        files = discover_email_files(args.maildir)
        if args.limit > 0:
            files = files[:args.limit]
        print(f"Found {len(files):,} email files")

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

                if (i + 1) % commit_every == 0:
                    store.connect().commit()

        store.connect().commit()

        print(f"\nIngestion complete (maildir):")
        print(f"  New:     {success:,}")
        print(f"  Skipped: {skipped:,}")
        print(f"  Errors:  {errors:,}")

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

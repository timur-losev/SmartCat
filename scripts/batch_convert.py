"""Batch Docling conversion: HTML email bodies → markdown, attachments → text.

Usage:
    python scripts/batch_convert.py --db data/smartcat.db --phase html
    python scripts/batch_convert.py --db data/smartcat.db --phase attach
    python scripts/batch_convert.py --db data/smartcat.db --phase all
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from tqdm import tqdm

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from smartcat.conversion.docling_converter import DoclingConverter
from smartcat.storage.sqlite_store import EmailStore


def convert_html_bodies(store: EmailStore, converter: DoclingConverter, batch_size: int = 500):
    """Convert HTML email bodies to clean markdown."""
    conn = store.connect()

    # Get emails with HTML content
    rows = store.get_html_emails_for_conversion(limit=1_000_000)
    if not rows:
        print("No HTML emails to convert")
        return

    print(f"Converting {len(rows)} HTML email bodies...")
    converted = 0
    errors = 0
    pbar = tqdm(rows, desc="HTML→markdown", unit="email")

    for i, row in enumerate(pbar):
        html = row.get("body_html") or row.get("body_text", "")
        if not html or "<" not in html:
            continue

        try:
            markdown = converter.convert_html(html)
            if markdown and markdown != html:
                store.update_email_body(row["email_id"], markdown)
                converted += 1
        except Exception as e:
            errors += 1
            pbar.set_postfix(err=str(e)[:30])

        if (i + 1) % batch_size == 0:
            conn.commit()
            pbar.set_postfix(converted=converted, errors=errors)

    conn.commit()
    # Update FTS index for converted emails
    print(f"HTML conversion done: {converted} converted, {errors} errors")


def convert_attachments(store: EmailStore, converter: DoclingConverter, batch_size: int = 100):
    """Convert binary attachments to extracted text."""
    total_converted = 0
    total_errors = 0
    total_skipped = 0

    while True:
        rows = store.get_attachments_without_text(limit=batch_size)
        if not rows:
            break

        conn = store.connect()
        pbar = tqdm(rows, desc="Attachments→text", unit="file", leave=False)

        for row in pbar:
            filename = row["filename"]
            content_type = row["content_type"]
            data = row["data"]

            if not converter.is_supported(filename, content_type):
                total_skipped += 1
                # Mark as processed with empty text so we don't re-try
                store.update_attachment_text(row["id"], "", 0)
                continue

            try:
                text, page_count = converter.convert_attachment(data, filename, content_type)
                store.update_attachment_text(row["id"], text, page_count)
                if text:
                    total_converted += 1
                else:
                    total_skipped += 1
            except Exception as e:
                total_errors += 1
                # Mark as processed with empty text
                store.update_attachment_text(row["id"], "", 0)
                pbar.set_postfix(err=str(e)[:30])

        conn.commit()
        pbar.set_postfix(converted=total_converted, skip=total_skipped, err=total_errors)

    print(f"Attachment conversion done: {total_converted} converted, "
          f"{total_skipped} skipped, {total_errors} errors")


def main():
    parser = argparse.ArgumentParser(description="Batch Docling conversion")
    parser.add_argument("--db", required=True, help="Path to SQLite database")
    parser.add_argument("--phase", required=True, choices=["html", "attach", "all"],
                        help="Conversion phase: html, attach, or all")
    parser.add_argument("--batch-size", type=int, default=500,
                        help="Commit every N items")
    args = parser.parse_args()

    store = EmailStore(Path(args.db))
    store.connect()
    converter = DoclingConverter()

    t0 = time.time()

    if args.phase in ("html", "all"):
        convert_html_bodies(store, converter, args.batch_size)

    if args.phase in ("attach", "all"):
        convert_attachments(store, converter, min(args.batch_size, 100))

    elapsed = time.time() - t0
    print(f"\nTotal time: {elapsed:.1f}s")

    # Print stats
    att_stats = store.get_attachment_count()
    print(f"Attachments: {att_stats.get('total', 0)} total, "
          f"{att_stats.get('with_data', 0)} with data, "
          f"{att_stats.get('with_text', 0)} with extracted text")

    store.close()


if __name__ == "__main__":
    main()

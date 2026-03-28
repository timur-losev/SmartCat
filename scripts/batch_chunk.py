"""Batch chunking: generate chunks for all emails in SQLite.

Usage:
    python scripts/batch_chunk.py [--db data/smartcat.db] [--batch-size 5000]
"""

from __future__ import annotations

import argparse
import sys
import time
import uuid
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import structlog
from tqdm import tqdm

from smartcat.chunking.email_chunker import EmailChunk, chunk_email, _chunk_text_by_paragraphs, _approx_tokens
from smartcat.config import SQLITE_DB_PATH
from smartcat.storage.sqlite_store import EmailStore

log = structlog.get_logger()


def batch_chunk_emails(
    store: EmailStore,
    batch_size: int = 5000,
    commit_every: int = 5000,
) -> int:
    """Generate chunks for all un-chunked emails.

    Returns total chunks created.
    """
    total_chunks = 0
    total_emails = 0

    conn = store.connect()

    # Check if there are entities for has_monetary detection
    monetary_email_ids: set[int] = set()
    rows = conn.execute(
        "SELECT DISTINCT email_id FROM entities WHERE entity_type = 'monetary'"
    ).fetchall()
    monetary_email_ids = {r[0] for r in rows}
    log.info("chunk.monetary_emails", count=len(monetary_email_ids))

    # Pre-load attachment text for L4 chunks
    attachment_texts: dict[int, list[dict]] = {}
    try:
        att_rows = conn.execute(
            """SELECT id, email_id, filename, extracted_text, page_count
               FROM attachments
               WHERE extracted_text IS NOT NULL AND extracted_text != ''"""
        ).fetchall()
        for r in att_rows:
            eid = r["email_id"]
            if eid not in attachment_texts:
                attachment_texts[eid] = []
            attachment_texts[eid].append(dict(r))
        log.info("chunk.attachments_with_text", count=len(att_rows))
    except Exception:
        log.info("chunk.no_attachment_text_column")

    while True:
        emails = store.get_emails_without_chunks(limit=batch_size)
        if not emails:
            break

        chunk_batch: list[dict] = []

        for email in tqdm(emails, desc="Chunking", unit="email"):
            email_id = email["email_id"]
            chunks = chunk_email(
                message_id=email.get("message_id", ""),
                subject=email.get("subject", ""),
                body_text=email.get("body_text", ""),
                from_address=email.get("from_address", ""),
                from_name=email.get("from_name", ""),
                date_sent=email.get("date_sent", ""),
                thread_id=email.get("thread_id", ""),
                has_monetary=email_id in monetary_email_ids,
                has_attachment=bool(email.get("has_attachments")),
                email_id=email_id,
            )

            for c in chunks:
                chunk_batch.append({
                    "chunk_id": c.chunk_id,
                    "email_id": email_id,
                    "chunk_type": c.chunk_type,
                    "chunk_index": c.chunk_index,
                    "text": c.text,
                    "token_count": c.token_count,
                })

            # L4: Attachment chunks from Docling-extracted text
            if email_id in attachment_texts:
                chunk_idx = len(chunks)
                for att in attachment_texts[email_id]:
                    att_text = att["extracted_text"]
                    if not att_text or _approx_tokens(att_text) < 20:
                        continue

                    # Prefix with filename for context
                    prefix = f"[Attachment: {att['filename']}]\n\n" if att.get("filename") else ""

                    if _approx_tokens(att_text) > 512:
                        sub_chunks = _chunk_text_by_paragraphs(att_text, max_tokens=512)
                    else:
                        sub_chunks = [att_text]

                    for sc_text in sub_chunks:
                        full_text = prefix + sc_text if chunk_idx == len(chunks) else sc_text
                        cid = f"{email.get('message_id', '')[:40]}_{chunk_idx}_{uuid.uuid4().hex[:8]}"
                        chunk_batch.append({
                            "chunk_id": cid,
                            "email_id": email_id,
                            "attachment_id": att["id"],
                            "chunk_type": "attachment",
                            "chunk_index": chunk_idx,
                            "text": full_text,
                            "token_count": _approx_tokens(full_text),
                        })
                        chunk_idx += 1

            total_emails += 1

            if len(chunk_batch) >= commit_every:
                store.insert_chunks(chunk_batch)
                conn.commit()
                total_chunks += len(chunk_batch)
                chunk_batch = []

        # Commit remaining
        if chunk_batch:
            store.insert_chunks(chunk_batch)
            conn.commit()
            total_chunks += len(chunk_batch)

        log.info("chunk.batch_done", emails=total_emails, chunks=total_chunks)

    return total_chunks


def main():
    parser = argparse.ArgumentParser(description="Batch chunking for email corpus")
    parser.add_argument("--db", type=str, default=str(SQLITE_DB_PATH))
    parser.add_argument("--batch-size", type=int, default=5000)
    args = parser.parse_args()

    store = EmailStore(Path(args.db))
    store.init_schema()

    email_count = store.get_email_count()
    existing_chunks = store.get_chunk_count()
    log.info("chunk.start", emails=email_count, existing_chunks=existing_chunks)

    t0 = time.time()
    total = batch_chunk_emails(store, batch_size=args.batch_size)
    elapsed = time.time() - t0

    final_chunks = store.get_chunk_count()
    log.info(
        "chunk.done",
        new_chunks=total,
        total_chunks=final_chunks,
        elapsed_sec=f"{elapsed:.1f}",
        rate=f"{total / max(elapsed, 1):.0f} chunks/s",
    )
    store.close()


if __name__ == "__main__":
    main()

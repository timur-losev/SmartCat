"""Parse The Bat! TBK/TBB backup files into mbox format.

TBK files contain zlib-compressed blocks. Each block may contain
one or more RFC 5322 email messages concatenated together.
This script decompresses all blocks, splits into individual emails,
and writes to standard mbox format.

Usage:
    python scripts/parse_tbk.py input.TBK output.mbox [--verbose]
"""

from __future__ import annotations

import argparse
import email
import email.utils
import re
import sys
import zlib
from pathlib import Path


def find_zlib_streams(data: bytes) -> list[int]:
    """Find offsets of potential zlib streams."""
    return [m.start() for m in re.finditer(b'\x78[\x01\x5e\x9c\xda]', data)]


def decompress_all(data: bytes, offsets: list[int]) -> list[bytes]:
    """Decompress all valid zlib streams."""
    blocks = []
    for pos in offsets:
        try:
            d = zlib.decompress(data[pos:pos + 5_000_000])
            if len(d) > 50:  # skip tiny fragments
                blocks.append(d)
        except zlib.error:
            pass
    return blocks


def split_emails(block: bytes) -> list[bytes]:
    """Split a decompressed block into individual email messages.

    Emails in TBB are concatenated. We split on common first-header patterns
    that indicate the start of a new message.
    """
    # Patterns that start a new email (at beginning of line)
    # Return-Path, Received, DKIM-Signature, From (as first header)
    split_pattern = re.compile(
        rb'(?=(?:^|\r?\n)(?:Return-Path:\s|Received:\s.*?;\r?\n|DKIM-Signature:))',
        re.MULTILINE
    )

    parts = split_pattern.split(block)
    emails = []

    for part in parts:
        part = part.strip()
        if len(part) < 50:
            continue

        # Must have at least From: and one other header
        has_from = b'From:' in part[:5000]
        has_other = any(h in part[:5000] for h in
                        [b'Subject:', b'Date:', b'To:', b'Message-Id', b'MIME-Version:'])
        if has_from and has_other:
            # Clean: find actual start of headers
            for start_pat in [b'Return-Path:', b'Received:', b'DKIM-Signature:',
                              b'From:', b'Date:', b'MIME-Version:']:
                idx = part.find(start_pat)
                if idx != -1 and idx < 500:
                    part = part[idx:]
                    break
            emails.append(part)

    return emails


def main():
    parser = argparse.ArgumentParser(description="Parse The Bat! TBK/TBB to mbox")
    parser.add_argument("input", type=Path, help="Input .TBK or .TBB file")
    parser.add_argument("output", type=Path, help="Output .mbox file")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    if not args.input.exists():
        print(f"Error: {args.input} not found")
        sys.exit(1)

    size_mb = args.input.stat().st_size / 1024 / 1024
    print(f"Reading {args.input} ({size_mb:.1f} MB)...")
    data = args.input.read_bytes()

    # Step 1: Find zlib streams
    offsets = find_zlib_streams(data)
    print(f"Found {len(offsets)} potential zlib streams")

    # Step 2: Decompress
    blocks = decompress_all(data, offsets)
    total_decompressed = sum(len(b) for b in blocks)
    print(f"Decompressed {len(blocks)} blocks ({total_decompressed / 1024 / 1024:.1f} MB)")

    # Step 3: Split into individual emails
    all_emails = []
    for block in blocks:
        found = split_emails(block)
        all_emails.extend(found)

    print(f"Found {len(all_emails)} email messages")

    if not all_emails:
        print("No emails found. The file may use a different format.")
        sys.exit(1)

    # Step 4: Validate and write mbox
    valid = 0
    errors = 0

    print(f"Writing to {args.output}...")
    with open(args.output, "wb") as f:
        for i, raw in enumerate(all_emails):
            try:
                msg = email.message_from_bytes(raw)
                frm = msg.get("From", "unknown")
                subj = msg.get("Subject", "(no subject)")
                date = msg.get("Date", "")

                if args.verbose:
                    print(f"  [{valid+1}] {frm[:40]} | {subj[:50]}")

                # mbox "From " envelope line
                addr = email.utils.parseaddr(frm)[1] or "unknown@unknown"
                f.write(f"From {addr} Mon Jan 01 00:00:00 2024\n".encode())
                f.write(raw)
                if not raw.endswith(b"\n"):
                    f.write(b"\n")
                f.write(b"\n")
                valid += 1
            except Exception as e:
                errors += 1
                if args.verbose:
                    print(f"  [error] {e}")

    print(f"\nDone!")
    print(f"  Valid emails: {valid}")
    print(f"  Errors: {errors}")
    print(f"  Output: {args.output} ({args.output.stat().st_size / 1024 / 1024:.1f} MB)")
    print(f"\nNext step:")
    print(f"  python scripts/ingest_maildir.py --format mbox --maildir {args.output} --db data/smartcat.db")


if __name__ == "__main__":
    main()

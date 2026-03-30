"""Batch QA extraction: generate question-answer pairs from email threads using LLM.

Tiers:
  --tier 1: threads with 5+ emails (highest value, ~6K threads)
  --tier 2: threads with 3-4 emails (~12K threads)
  --tier 3: threads with 2 emails (~20K threads)

Pause/Resume: progress saved in qa_progress table after each thread.
Just Ctrl+C and re-run — already processed threads are skipped.

Usage:
    python scripts/batch_qa_extract.py --db data/smartcat.db --tier 1
    python scripts/batch_qa_extract.py --db data/smartcat.db --tier 2
    python scripts/batch_qa_extract.py --db data/smartcat.db --tier 3
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import requests
from tqdm import tqdm

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from smartcat.storage.sqlite_store import EmailStore

TIER_MIN_EMAILS = {1: 5, 2: 3, 3: 2}

QA_PROMPT = """/no_think
You are extracting question-answer pairs from an email thread. Each QA pair should capture a factual piece of information from the conversation.

Rules:
- Generate 2-5 QA pairs per thread (more for longer threads)
- Questions should be natural, as a user would ask
- Answers should be concise and factual (1-2 sentences max)
- Include names, dates, amounts, decisions when available
- Focus ONLY on Enron business content: deals, people, meetings, decisions, projects
- SKIP forwarded news articles, CNN/Reuters headlines, spam, newsletters, mailing lists
- SKIP generic greetings, pleasantries, and auto-generated messages
- If the thread contains only news forwards or spam, output empty array: []
- Output ONLY valid JSON array, nothing else

Email thread:
{thread_text}

Output format (JSON array only, no markdown):
[
  {{"q": "question text", "a": "answer text", "email_idx": 0}},
  {{"q": "question text", "a": "answer text", "email_idx": 1}}
]
"""


def format_thread_for_prompt(emails: list[dict]) -> str:
    """Format thread emails into a compact text for the LLM prompt."""
    # Limit to 7 emails max to keep prompt manageable
    emails = emails[:7]
    parts = []
    for i, e in enumerate(emails):
        date = (e.get("date_sent") or "")[:10]
        sender = e.get("from_name") or e.get("from_address", "")
        subject = e.get("subject", "")
        body = (e.get("body_preview") or "")[:300]
        parts.append(f"[{i}] From: {sender} | Date: {date} | Subject: {subject}\n{body}")
    return "\n---\n".join(parts)


def call_llm(prompt: str, llm_url: str, timeout: int = 120) -> str:
    """Call llama.cpp server."""
    resp = requests.post(
        f"{llm_url}/v1/chat/completions",
        json={
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.3,
            "max_tokens": 500,
        },
        timeout=timeout,
    )
    resp.raise_for_status()
    msg = resp.json()["choices"][0]["message"]
    return msg.get("content") or msg.get("reasoning_content") or ""


def parse_qa_response(response: str) -> list[dict]:
    """Parse LLM response into QA pairs."""
    # Try to extract JSON array from response
    response = response.strip()

    # Remove markdown code fences if present
    response = re.sub(r'^```(?:json)?\s*\n?', '', response)
    response = re.sub(r'\n?```\s*$', '', response)

    # Find JSON array
    match = re.search(r'\[.*\]', response, re.DOTALL)
    if not match:
        return []

    try:
        pairs = json.loads(match.group(0))
        if not isinstance(pairs, list):
            return []
        # Validate structure
        valid = []
        for p in pairs:
            if isinstance(p, dict) and "q" in p and "a" in p:
                valid.append({
                    "question": p["q"].strip(),
                    "answer": p["a"].strip(),
                    "email_idx": p.get("email_idx", 0),
                })
        return valid
    except (json.JSONDecodeError, TypeError):
        return []


def main():
    parser = argparse.ArgumentParser(description="Batch QA extraction from email threads")
    parser.add_argument("--db", default="data/smartcat.db")
    parser.add_argument("--tier", type=int, default=1, choices=[1, 2, 3],
                        help="Thread tier: 1=5+ emails, 2=3-4, 3=2")
    parser.add_argument("--llm-url", default="http://127.0.0.1:8080")
    parser.add_argument("--batch-size", type=int, default=500,
                        help="Fetch N threads at a time")
    parser.add_argument("--commit-every", type=int, default=50,
                        help="Commit to DB every N threads")
    parser.add_argument("--timeout", type=int, default=300,
                        help="LLM timeout in seconds")
    args = parser.parse_args()

    min_emails = TIER_MIN_EMAILS[args.tier]

    store = EmailStore(Path(args.db))
    store.init_schema()  # creates qa_pairs and qa_progress tables
    conn = store.connect()

    # Check LLM health
    try:
        r = requests.get(f"{args.llm_url}/health", timeout=5)
        if r.json().get("status") != "ok":
            print("ERROR: llama-server not healthy")
            return
    except Exception as e:
        print(f"ERROR: Cannot connect to llama-server at {args.llm_url}: {e}")
        return

    # Get QA stats
    qa_stats = store.get_qa_stats()
    print(f"Existing QA: {qa_stats.get('total_pairs', 0)} pairs, "
          f"{qa_stats.get('threads_done', 0)} threads done")

    total_qa = 0
    total_threads = 0
    total_errors = 0
    total_skipped = 0

    while True:
        threads = store.get_threads_for_qa(min_emails=min_emails, limit=args.batch_size)
        if not threads:
            break

        print(f"\nBatch: {len(threads)} threads (tier {args.tier}, min {min_emails} emails)")
        pbar = tqdm(threads, desc=f"QA tier-{args.tier}", unit="thread")

        for thread in pbar:
            thread_id = thread["thread_id"]
            email_count = thread["email_count"]

            # Get thread emails
            emails = store.get_thread_emails_for_qa(thread_id)
            if not emails:
                store.mark_thread_qa_done(thread_id, 0, "skip")
                total_skipped += 1
                continue

            # Format prompt
            thread_text = format_thread_for_prompt(emails)
            prompt = QA_PROMPT.format(thread_text=thread_text)

            # Call LLM
            try:
                response = call_llm(prompt, args.llm_url, args.timeout)
                qa_pairs = parse_qa_response(response)
            except requests.exceptions.Timeout:
                store.mark_thread_qa_done(thread_id, 0, "error")
                total_errors += 1
                pbar.set_postfix(qa=total_qa, err=total_errors)
                continue
            except Exception as e:
                store.mark_thread_qa_done(thread_id, 0, "error")
                total_errors += 1
                pbar.set_postfix(qa=total_qa, err=total_errors)
                continue

            if not qa_pairs:
                store.mark_thread_qa_done(thread_id, 0, "skip")
                total_skipped += 1
                continue

            # Map email_idx to actual email_id
            now = datetime.now().isoformat()
            db_pairs = []
            for qp in qa_pairs:
                idx = min(qp.get("email_idx", 0), len(emails) - 1)
                db_pairs.append({
                    "thread_id": thread_id,
                    "email_id": emails[idx]["email_id"],
                    "question": qp["question"],
                    "answer": qp["answer"],
                    "source_context": emails[idx].get("body_preview", "")[:300],
                    "created_at": now,
                })

            store.insert_qa_pairs(db_pairs)
            store.mark_thread_qa_done(thread_id, len(db_pairs))
            total_qa += len(db_pairs)
            total_threads += 1

            pbar.set_postfix(qa=total_qa, err=total_errors, skip=total_skipped)

            # Periodic commit
            if total_threads % args.commit_every == 0:
                conn.commit()

        conn.commit()

    conn.commit()
    store.close()

    print(f"\n{'='*50}")
    print(f"QA Extraction Complete (Tier {args.tier})")
    print(f"{'='*50}")
    print(f"Threads processed: {total_threads}")
    print(f"QA pairs generated: {total_qa}")
    print(f"Errors: {total_errors}")
    print(f"Skipped: {total_skipped}")
    print(f"Avg QA/thread: {total_qa / max(total_threads, 1):.1f}")


if __name__ == "__main__":
    main()

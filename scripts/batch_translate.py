"""Batch translation: translate non-English email bodies to English via LLM.

For multilingual corpora (Montenegrin, Russian, English mix).
Already-English emails get body_text copied to body_text_en as-is.
Non-English emails are translated via LLM.

Pause/Resume: tracks progress via body_text_en IS NULL.
Ctrl+C safe — commits every N emails.

Usage:
    python scripts/batch_translate.py --db data/smartcat.db
    python scripts/batch_translate.py --db data/smartcat.db --detect-only
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

import requests
from tqdm import tqdm

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from smartcat.storage.sqlite_store import EmailStore

TRANSLATE_PROMPT = """/no_think
You are a translator. Determine if the text below is in English.

If it IS English: respond with exactly: ENGLISH
If it is NOT English: translate it to English. Output ONLY the translation, nothing else.

Text:
{text}
"""

DETECT_PROMPT = """/no_think
What language is this text written in? Reply with ONLY the language name in English (e.g. "English", "Russian", "Serbian", "Montenegrin", "Spanish"). If mixed, state the primary language.

Text:
{text}
"""


def call_llm(prompt: str, llm_url: str, timeout: int = 120) -> str:
    """Call llama.cpp server."""
    resp = requests.post(
        f"{llm_url}/v1/chat/completions",
        json={
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.1,
            "max_tokens": 1500,
        },
        timeout=timeout,
    )
    resp.raise_for_status()
    msg = resp.json()["choices"][0]["message"]
    return msg.get("content") or msg.get("reasoning_content") or ""


def main():
    parser = argparse.ArgumentParser(description="Batch translate emails to English")
    parser.add_argument("--db", default="data/smartcat.db")
    parser.add_argument("--llm-url", default="http://127.0.0.1:8080")
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--commit-every", type=int, default=50)
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--detect-only", action="store_true",
                        help="Only detect languages, don't translate")
    args = parser.parse_args()

    store = EmailStore(Path(args.db))
    conn = store.connect()

    # Migrate schema if needed
    try:
        conn.execute("SELECT body_text_en FROM emails LIMIT 0")
    except Exception:
        conn.execute("ALTER TABLE emails ADD COLUMN body_text_en TEXT")
        conn.commit()
        print("Migrated: added body_text_en column")

    # Check LLM health
    try:
        r = requests.get(f"{args.llm_url}/health", timeout=5)
        if r.json().get("status") != "ok":
            print("ERROR: llama-server not healthy")
            return
    except Exception as e:
        print(f"ERROR: Cannot connect to llama-server: {e}")
        return

    stats = store.get_translation_stats()
    print(f"Translation status: {stats.get('translated', 0)} done, {stats.get('pending', 0)} pending")

    if args.detect_only:
        # Just detect languages on a sample
        emails = store.get_emails_for_translation(limit=50)
        lang_counts: dict[str, int] = {}
        for e in tqdm(emails, desc="Detecting languages"):
            text = (e.get("subject", "") + " " + e.get("body_text", ""))[:300]
            prompt = DETECT_PROMPT.format(text=text)
            try:
                lang = call_llm(prompt, args.llm_url, args.timeout).strip()
                lang_counts[lang] = lang_counts.get(lang, 0) + 1
            except Exception:
                lang_counts["error"] = lang_counts.get("error", 0) + 1
        print("\nLanguage distribution (sample of 50):")
        for lang, count in sorted(lang_counts.items(), key=lambda x: -x[1]):
            print(f"  {lang}: {count}")
        return

    # Main translation loop
    total_translated = 0
    total_english = 0
    total_errors = 0

    while True:
        emails = store.get_emails_for_translation(limit=args.batch_size)
        if not emails:
            break

        pbar = tqdm(emails, desc="Translating", unit="email")

        for i, e in enumerate(pbar):
            text = (e.get("subject", "") + "\n\n" + e.get("body_text", "")).strip()
            if not text:
                store.update_email_translation(e["email_id"], "")
                continue

            prompt = TRANSLATE_PROMPT.format(text=text[:1500])

            try:
                result = call_llm(prompt, args.llm_url, args.timeout).strip()

                if result == "ENGLISH" or result.startswith("ENGLISH"):
                    # Already English — copy original
                    store.update_email_translation(e["email_id"], e.get("body_text", ""))
                    total_english += 1
                else:
                    # Got translation
                    store.update_email_translation(e["email_id"], result)
                    total_translated += 1
            except Exception as ex:
                # On error, mark as original to avoid re-processing
                store.update_email_translation(e["email_id"], e.get("body_text", ""))
                total_errors += 1

            pbar.set_postfix(en=total_english, tr=total_translated, err=total_errors)

            if (i + 1) % args.commit_every == 0:
                conn.commit()

        conn.commit()

    conn.commit()
    store.close()

    print(f"\n{'='*50}")
    print(f"Translation Complete")
    print(f"{'='*50}")
    print(f"Already English: {total_english}")
    print(f"Translated: {total_translated}")
    print(f"Errors: {total_errors}")


if __name__ == "__main__":
    main()

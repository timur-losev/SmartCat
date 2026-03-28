# Docling Integration Plan

## Context

Текущий пайплайн парсит email body как plain text, но:
1. **HTML body** — когда email только HTML (`content_type=text/html`), `body_text` содержит сырой HTML с тегами. Chunker получает грязный текст.
2. **MIME-аттачи** — parser детектит наличие аттачей (`has_attachments=True`), но **не извлекает их байты**. Реальные PDF/DOC в Enron maildir игнорируются.
3. **`<< File: ... >>` ссылки** — сохраняются как `referenced_files`, файлов нет — чанкить нечего.

Docling решает обе проблемы: конвертит HTML → clean markdown и PDF/DOC → текст.

## Pipeline After Changes

```
maildir → mime_parser (+ extract attachment bytes) → SQLite emails + attachments
                                                         ↓
                                              docling_converter.py
                                              ├── HTML body → clean markdown → UPDATE emails.body_text
                                              └── attachment bytes → text → UPDATE attachments.extracted_text
                                                         ↓
                                              batch_chunk.py (re-chunk all, incl. L4 attachment chunks)
                                                         ↓
                                              batch_embed.py (re-embed all)
```

## Changes

### 1. `src/smartcat/parsing/mime_parser.py` — Extract attachment bytes

- Add `Attachment` dataclass and `attachments: list[Attachment]` to `ParsedEmail`:
  ```python
  @dataclass
  class Attachment:
      filename: str
      content_type: str
      data: bytes
  ```
- In `parse_email_file()`, during `msg.walk()`, for parts with `Content-Disposition: attachment` → extract `get_payload(decode=True)` → append to `attachments`
- Also capture non-text inline parts (embedded docs)

### 2. `src/smartcat/storage/sqlite_store.py` — Store attachment data

- Migration: `ALTER TABLE attachments ADD COLUMN data BLOB`
- Update `insert_email()`: insert `parsed.attachments` with binary `data`
- New methods:
  - `get_attachments_without_text(limit)` — `WHERE extracted_text IS NULL AND data IS NOT NULL`
  - `update_attachment_text(attachment_id, extracted_text, page_count)`
  - `update_email_body(email_id, body_text)` — for HTML→markdown results
  - `get_html_emails_for_conversion(limit)` — `WHERE content_type='text/html'` or body contains `<html` tags

### 3. NEW `src/smartcat/conversion/__init__.py` + `docling_converter.py`

```python
class DoclingConverter:
    def __init__(self):
        self._converter = DocumentConverter()

    def convert_html(self, html: str) -> str:
        """HTML string → clean markdown via Docling."""

    def convert_attachment(self, data: bytes, filename: str) -> tuple[str, int]:
        """Binary attachment → (extracted_text, page_count).
        Supported: PDF, DOC, DOCX, PPTX, XLSX, HTML, images (OCR).
        Unsupported → empty string."""
```

- Temp file approach: write bytes to tempfile → Docling convert → read result → cleanup
- 60s timeout per file
- Error: log and skip

### 4. NEW `scripts/extract_attachments.py` — One-time migration

DB already has emails but no attachment binary data. This script:
- Queries `email_instances.source_path` for emails with `has_attachments=1`
- Re-reads each .eml file, extracts MIME attachment bytes
- Inserts into `attachments` table with `data` BLOB
- Skips emails that already have attachment data
- Resumable, progress bar

### 5. NEW `scripts/batch_convert.py` — Batch Docling conversion

Two phases:
- `--phase html`: emails with HTML body → `DoclingConverter.convert_html()` → update `emails.body_text`
- `--phase attach`: attachments without text → `DoclingConverter.convert_attachment()` → update `attachments.extracted_text`
- `--phase all`: both

### 6. `scripts/batch_chunk.py` — Add L4 attachment chunks

After creating L1-L3 chunks per email, query `attachments WHERE extracted_text IS NOT NULL AND email_id = ?`:
- Create `chunk_type='attachment'` chunks with `attachment_id` FK
- Chunk long text same as body (paragraph-based, 512 tokens max)
- Set `page_range` if available

### 7. Full re-run sequence

```bash
# 0. Install
pip install docling

# 1. Migrate DB schema
sqlite3 data/smartcat.db "ALTER TABLE attachments ADD COLUMN data BLOB;"

# 2. Extract attachment bytes from maildir
python scripts/extract_attachments.py --db data/smartcat.db --maildir maildir/

# 3. Convert HTML bodies + attachments → clean text
python scripts/batch_convert.py --db data/smartcat.db --phase all

# 4. Clear old chunks, re-chunk
sqlite3 data/smartcat.db "DELETE FROM chunks;"
python scripts/batch_chunk.py --db data/smartcat.db

# 5. Re-embed
python scripts/batch_embed.py --db data/smartcat.db --device cuda --batch-size 16 --recreate
```

## Files Summary

| File | Action |
|------|--------|
| `src/smartcat/parsing/mime_parser.py` | Modify: add Attachment dataclass, extract binary payloads |
| `src/smartcat/storage/sqlite_store.py` | Modify: add BLOB column, new query/update methods |
| `src/smartcat/conversion/__init__.py` | Create: new package |
| `src/smartcat/conversion/docling_converter.py` | Create: Docling wrapper |
| `scripts/extract_attachments.py` | Create: one-time attachment extraction from maildir |
| `scripts/batch_convert.py` | Create: batch Docling conversion |
| `scripts/batch_chunk.py` | Modify: add L4 attachment chunk generation |

## Verification

1. `pip install docling` succeeds
2. After `extract_attachments.py`: `SELECT COUNT(*) FROM attachments WHERE data IS NOT NULL` > 0
3. After `batch_convert.py --phase html`: spot-check `body_text` for HTML emails — clean markdown, no tags
4. After `batch_convert.py --phase attach`: `SELECT filename, LENGTH(extracted_text) FROM attachments WHERE extracted_text IS NOT NULL LIMIT 10`
5. After re-chunk: chunk count increased (new attachment chunks)
6. After re-embed: Qdrant vector count matches chunk count
7. Search query about attachment content returns relevant results

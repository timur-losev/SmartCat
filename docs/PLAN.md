# SmartCat — RAG-система для поиска по email-переписке

## Context

Нужно построить RAG-систему для полнотекстового AI-поиска по корпусу email-переписки (тестовый: Enron 517K писем, продакшен: 30GB+ HTML с PDF/JPG вложениями). Система должна извлекать участников, темы, даты, суммы, документы, цепочки переписки и давать AI-ответы с рассуждением.

**Hardware**: RTX 3090/4090 24GB VRAM, local inference.
**UI**: CLI-first, API позже.

---

## 1. Выбор датасета: `maildir/` (основной)

**Берём `maildir/`**, а не `emails.csv`:
- **Продакшен-совместимость** — продакшен будет в виде файлов, а не CSV. Pipeline на файлах переносится напрямую
- **Параллельная обработка** — каждый файл независим, можно чекпоинтить прогресс по файлам
- **Вложения** — в CSV вложения отсутствуют, maildir-архитектура естественно расширяется на аттачменты
- **`emails.csv`** используем только для валидации (сверка количества, быстрое прототипирование парсинга)

---

## 2. Архитектура Data Pipeline

### 2.1 Парсинг email (Python `email` stdlib)
```
maildir/{user}/{folder}/{id}_ → Structured dict:
  message_id, date, from/to/cc/bcc (addr+name),
  subject, body_text, content_type, x_folder,
  source_path, has_forwarded_content, has_reply_content
```

**Error handling**: таблица `processing_errors(file_path, error_type, error_msg, timestamp)` в SQLite. Skip-and-log для битых файлов, нечитаемых кодировок, пустых файлов. Resumable ingestion через таблицу `processed_files(file_path, status, processed_at)`.

### 2.2 Реконструкция цепочек (threads)
**Приоритетный путь** (для продакшена, когда заголовки есть):
1. `In-Reply-To` / `References` headers → прямое построение дерева

**Fallback** (для Enron, где этих заголовков нет):
2. Нормализация subject (strip RE:/FW:/Fwd:)
3. Группировка по canonical subject + пересечение участников
4. Парсинг `"Forwarded by"` и `"-----Original Message-----"` из тела
5. Результат: `thread_id` + `parent_message_id` на каждом письме

### 2.3 Извлечение метаданных
- **Участники**: канонические имена (X-From/X-To → единая таблица персон)
- **Даты**: `dateutil.parser` из заголовков + regex из тела
- **Суммы**: regex `\$[\d,]+\.?\d*`, "million", "MMBtu" и т.д.
- **Ссылки на документы**: `*.xls`, `*.doc`, `*.pdf`, номера контрактов
- **Без spaCy** — для email достаточно regex + structured headers. NER не нужен, т.к. участники уже в From/To.

### 2.4 Docling (для продакшена)
```
MIME attachment → определение типа:
  PDF      → Docling PDF pipeline (layout + OCR)
  image/*  → Docling OCR
  HTML     → BeautifulSoup + readability-lxml
  .doc/xls → Docling / python-docx / openpyxl
→ Текст привязывается к parent email по message_id + attachment_id
```

**Большие вложения**: PDF 100+ стр → чанки с `attachment_id + page_range` для точного цитирования.
**Дедупликация вложений**: hash файла → если тот же attachment forwarded в другом email, ссылка вместо повторной обработки.

### 2.5 HTML-обработка (для продакшена)
- Извлечение highlighted text (`<mark>`, `<b>`, `<strong>`, inline `background-color`)
- Таблицы → markdown
- Удаление boilerplate (подписи, disclaimers, tracking pixels)
- Сохранение quoted reply секций

### 2.6 Дедупликация email
- Одно письмо может быть в `sent_items/` отправителя и `inbox/` получателя
- **Стратегия**: хранить все экземпляры в SQLite (разный folder = разный контекст), но **один набор векторов в Qdrant** per unique Message-ID
- Связь: `email_instances(message_id, source_path, x_folder, folder_owner)` — N:1 к основной записи email

---

## 3. Storage & Indexing

### 3.1 Векторная БД: **Qdrant** (Docker, single-node)
- Rust, payload filtering (фильтрация по дате/участнику на уровне ANN), disk-backed
- ChromaDB не тянет >1M векторов, Milvus избыточен для single-machine
- 517K unique emails × ~3 чанка = ~1.5M векторов → ~4.5GB (RAM)
- Продакшен ~10M векторов → ~30GB (disk-backed mode, 64GB+ RAM)

### 3.2 Метаданные: **SQLite + FTS5**
- FTS5 даёт BM25 keyword search из коробки
- Zero-ops, один файл

```sql
emails (message_id PK, date_sent, subject, body_text, from_address,
        from_name, thread_id, parent_message_id, has_attachments, ...)
email_instances (id PK, message_id FK, source_path, x_folder, folder_owner)
participants (id PK, email UNIQUE, canonical_name)
email_participants (message_id, participant_id, role)
entities (message_id, entity_type, entity_value, context)
attachments (id PK, message_id FK, filename, content_type, file_hash,
             extracted_text, page_count)
chunks (chunk_id PK, message_id FK, attachment_id FK NULL,
        chunk_type, chunk_index, text, token_count)
processed_files (file_path PK, status, processed_at)
processing_errors (file_path, error_type, error_msg, timestamp)
emails_fts USING fts5(subject, body_text)
```

### 3.3 Chunking: иерархический, email-aware
| Уровень | Содержимое | Размер |
|---------|-----------|--------|
| L1 Summary | Subject + From/To/Date + первые 200 chars | 100-300 tokens |
| L2 Body | Параграфы тела, merge мелких до ~400 tokens | 300-512 tokens |
| L3 Quoted | Каждое вложенное FW/RE как отдельный чанк | варьируется |
| L4 Attachment | Docling-текст вложений (с page_range) | 300-500 tokens |

Каждый чанк несёт payload в Qdrant: `message_id, chunk_type, date_sent, from_address, to_addresses, thread_id, has_monetary, has_attachment`

### 3.4 Embedding: выбрать из 2-3 моделей
Кандидаты (все локальные, <1GB):
- `nomic-embed-text-v1.5` — 768d, 137M, Matryoshka, 8K context
- `bge-large-en-v1.5` — 1024d, 335M, сильный на retrieval
- `e5-large-v2` — 1024d, 335M, instruction-tuned

**Шаг**: перед batch embedding всего корпуса — **сравнить на 20 тестовых запросах** (Recall@10). Выбрать лучшую.

Через `sentence-transformers` (не Ollama — быстрее батчами). ~1000 chunks/sec GPU.

---

## 4. RAG Architecture

### 4.1 Hybrid Search (3 канала → RRF fusion)
```
Query → [параллельно]
  ├─ Vector Search (Qdrant, top-60, metadata pre-filters)
  ├─ Keyword Search (SQLite FTS5 BM25, top-60)
  └─ Structured Filters (агент формирует через tools, не NLU-парсинг)
      ↓
  RRF fusion (k=60) → top-30
```

**Structured Query**: НЕ пытаемся парсить NL-запрос регексами. Вместо этого агент сам решает, когда вызвать `search_by_participant()` или `search_by_date_range()`. Это надёжнее и расширяемее.

### 4.2 Re-ranking
- Cross-encoder `bge-reranker-v2-m3` на top-30 → top-10
- **Работает на CPU** (~300ms на 30 пар) — освобождает GPU для LLM
- Metadata boost: полнота треда, свежесть, совпадение участников

### 4.3 VRAM Lifecycle (24GB constraint)
```
Ingestion mode:  embedding model (GPU, ~0.5GB) — batch processing
Serving mode:    LLM (GPU, ~20GB) + reranker (CPU) + embedding (CPU, для query)
```
- При ingestion LLM не нужен → вся VRAM для embedding
- При serving embedding одного query на CPU — ~50ms, приемлемо
- Reranker всегда на CPU
- **Не загружать все модели одновременно на GPU**

### 4.4 Context Assembly
- Дедупликация по message_id
- Для каждого email: full record из SQLite + thread context
- До 24K tokens контекста (Qwen3 32B, 32K context window)

### 4.5 AI Agent (ReAct loop)
Инструменты агента:
- `search_emails(query, filters)` — гибридный поиск (vector + BM25)
- `search_by_participant(name_or_email)` — structured поиск по участнику
- `search_by_date_range(start, end, query?)` — фильтр по дате
- `search_entities(type, value)` — поиск сумм, дат, документов
- `get_email(message_id)` — полное письмо
- `get_thread(thread_id)` — вся цепочка в хронологическом порядке
- `get_email_stats(filters)` — агрегатная статистика

Макс 5 шагов рассуждения → финальный ответ с цитатами (message_id + дата + участники).

---

## 5. Tech Stack

| Компонент | Выбор |
|-----------|-------|
| Язык | Python 3.11+ |
| Парсинг email | `email` stdlib |
| HTML | `readability-lxml` + `beautifulsoup4` |
| OCR/Документы | Docling |
| Метаданные | `dateutil`, regex (без spaCy) |
| Embedding | winner из nomic/bge/e5 (sentence-transformers) |
| Vector DB | Qdrant (Docker) |
| Metadata DB | SQLite + FTS5 |
| Re-ranker | `bge-reranker-v2-m3` (CPU) |
| LLM | Qwen3 32B Q4_K_M GGUF (~20GB VRAM) |
| LLM serving | `llama.cpp` (llama-server) |
| Agent | Custom ReAct loop (~100 строк, без LangChain) |
| UI (dev) | CLI (`rich` + `click`), API позже |
| Logging | `structlog` + `tqdm` для pipeline progress |
| Параллелизм | `multiprocessing` |

---

## 6. Порядок реализации

### Phase 1: Фундамент
1. **Структура проекта + venv** — `python -m venv .venv`, pyproject.toml, `pip install -e ".[dev]"`, src/smartcat/...
2. **MIME-парсер** — `parsing/mime_parser.py` (Python `email` module), error handling, тест на 100 файлах
3. **SQLite storage** — полная схема (включая email_instances, chunks, processing_errors), CRUD, FTS5

### Phase 2: Извлечение и Threading
4. **Metadata extraction** — участники (canonical names), даты, суммы, документы
5. **Thread reconstruction** — In-Reply-To первый, subject fallback второй
6. **Batch ingestion** — обработка всех 517K файлов в SQLite, resumable, progress bar (~15-30 мин)

### Phase 3: Embedding и Indexing
7. **Chunking** — иерархический email-aware chunker, сохранение в SQLite
8. **Embedding model eval** — сравнить 3 модели на 20 запросах, выбрать лучшую
9. **Batch embedding + Qdrant** — batch embed, Docker Qdrant, payload indexes, upsert (~25 мин GPU)

### Phase 4: Retrieval
10. **Hybrid search** — vector + FTS5 → RRF fusion
11. **Re-ranker** — cross-encoder на CPU + metadata boost
12. **CLI search** — `smartcat search "query"` для тестирования retrieval

### Phase 5: Agent и LLM
13. **LLM setup** — Qwen3 32B GGUF + llama-server (VRAM lifecycle: выгрузить embedding, загрузить LLM)
14. **Agent tools** — реализация 7 инструментов
15. **ReAct loop + CLI chat** — `smartcat chat` с streaming ответами

### Phase 6: Production Readiness
16. **Docling integration** — attachment pipeline для продакшен-данных
17. **Evaluation** — 50 test queries, precision/recall, prompt tuning
18. **FastAPI** — REST endpoints когда CLI стабилен

---

## Ключевые файлы

- `G:/Proj/SmartCat/maildir/` — основной источник (517K файлов, 150 пользователей)
- `G:/Proj/SmartCat/maildir/allen-p/` — интеграционный тест (3034 файла)
- `G:/Proj/SmartCat/emails.csv` — валидация и прототипирование

## Структура проекта (целевая)
```
smartcat/
  pyproject.toml
  src/smartcat/
    __init__.py
    config.py
    parsing/
      mime_parser.py
      metadata.py
      threading.py
    storage/
      sqlite_store.py
      qdrant_store.py
    chunking/
      email_chunker.py
    embedding/
      embedder.py
    retrieval/
      hybrid_search.py
      reranker.py
    agent/
      tools.py
      react_agent.py
    cli/
      main.py        # click CLI: search, chat, ingest
  scripts/
    ingest_maildir.py
    eval_embeddings.py
  tests/
```

## Верификация
- Unit-тесты парсера на 100 email из разных пользователей
- Сверка количества с emails.csv
- Embedding model comparison на 20 запросах (Recall@10)
- 20 ручных запросов для hybrid search quality
- 50 test queries с ожидаемыми ответами для end-to-end evaluation
- Проверка thread reconstruction на известных цепочках (allen-p)
- VRAM monitoring при смене режимов ingestion/serving

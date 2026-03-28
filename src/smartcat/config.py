"""Configuration and paths for SmartCat."""

from pathlib import Path

# Project root (where pyproject.toml lives)
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# Data paths
MAILDIR_PATH = PROJECT_ROOT / "maildir"
EMAILS_CSV_PATH = PROJECT_ROOT / "emails.csv"
DATA_DIR = PROJECT_ROOT / "data"

# SQLite database
SQLITE_DB_PATH = DATA_DIR / "smartcat.db"

# Qdrant
QDRANT_HOST = "localhost"
QDRANT_PORT = 6333
QDRANT_COLLECTION = "emails"

# Embedding
EMBEDDING_CANDIDATES = [
    "nomic-ai/nomic-embed-text-v1.5",
    "BAAI/bge-large-en-v1.5",
    "intfloat/e5-large-v2",
]
EMBEDDING_MODEL = EMBEDDING_CANDIDATES[0]  # default, override after eval
EMBEDDING_DIM = 768  # for nomic; 1024 for bge/e5
EMBEDDING_BATCH_SIZE = 256

# Chunking
CHUNK_MAX_TOKENS = 512
CHUNK_OVERLAP_TOKENS = 50
SUMMARY_CHUNK_MAX_CHARS = 200

# LLM
LLM_SERVER_URL = "http://localhost:8080"
LLM_MAX_CONTEXT = 32768
LLM_MAX_RETRIEVED_TOKENS = 24000

# Re-ranker
RERANKER_MODEL = "BAAI/bge-reranker-v2-m3"
RERANKER_TOP_K = 10
RERANKER_DEVICE = "cpu"

# Search
SEARCH_TOP_K_PER_CHANNEL = 60
RRF_K = 60
RERANK_CANDIDATES = 30

# Agent
AGENT_MAX_STEPS = 5

# Ingestion
INGESTION_WORKERS = 4  # for multiprocessing

"""Shared fixtures for SmartCat tests."""

import pytest
from pathlib import Path
from smartcat.storage.sqlite_store import EmailStore


MAILDIR = Path(__file__).resolve().parent.parent / "maildir"
SAMPLE_EMAIL = MAILDIR / "allen-p" / "inbox" / "1_"


def maildir_available() -> bool:
    return MAILDIR.exists() and SAMPLE_EMAIL.exists()


skip_no_maildir = pytest.mark.skipif(
    not maildir_available(),
    reason="maildir test data not available",
)


@pytest.fixture
def tmp_store(tmp_path):
    """Create a temporary EmailStore with schema initialized."""
    db = tmp_path / "test.db"
    store = EmailStore(db)
    store.init_schema()
    yield store
    store.close()


@pytest.fixture
def sample_email_path():
    """Path to a sample email for testing."""
    return SAMPLE_EMAIL

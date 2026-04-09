"""Language detection wrapper using langdetect."""

from __future__ import annotations

import logging

from langdetect import detect, DetectorFactory

log = logging.getLogger(__name__)

# Make langdetect deterministic
DetectorFactory.seed = 0


def detect_language(text: str) -> str:
    """Detect language of text, return ISO 639-1 code.

    Returns:
        Two-letter code: 'en', 'ru', 'sr', 'hr', etc.
        Returns 'unknown' on failure or empty text.
    """
    if not text or len(text.strip()) < 10:
        return "unknown"

    try:
        return detect(text)
    except Exception as e:
        log.debug("langdetect.failed: %s", e)
        return "unknown"

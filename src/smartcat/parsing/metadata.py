"""Extract structured metadata from email body text.

Extracts: monetary amounts, date references, document references, deal IDs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class ExtractedEntity:
    entity_type: str  # 'monetary', 'date_ref', 'document_ref', 'deal_id'
    entity_value: str
    context: str  # surrounding text for display


# -- Monetary amounts --
_MONETARY_PATTERNS = [
    # $1,234.56 or $1234
    re.compile(r"\$\s?([\d,]+(?:\.\d{1,2})?)\s*(?:million|mil|mm|M|billion|bil|B|k|K|thousand)?", re.IGNORECASE),
    # 1.5 million / 2.3 billion
    re.compile(r"([\d,.]+)\s+(?:million|billion|trillion)", re.IGNORECASE),
    # MMBtu (energy unit common in Enron)
    re.compile(r"([\d,.]+)\s*(?:MMBtu|MWh|MW)", re.IGNORECASE),
    # USD/EUR amounts without $
    re.compile(r"(?:USD|EUR|GBP)\s*([\d,]+(?:\.\d{1,2})?)", re.IGNORECASE),
]

# -- Date references in body text --
_DATE_PATTERNS = [
    # MM/DD/YYYY or MM/DD/YY
    re.compile(r"\b(\d{1,2}/\d{1,2}/\d{2,4})\b"),
    # Month DD, YYYY
    re.compile(r"\b((?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},?\s+\d{4})\b", re.IGNORECASE),
    # DD Month YYYY
    re.compile(r"\b(\d{1,2}\s+(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4})\b", re.IGNORECASE),
    # Abbreviated months: Jan 15, 2001
    re.compile(r"\b((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},?\s+\d{4})\b", re.IGNORECASE),
]

# -- Document references --
_DOC_PATTERNS = [
    # << File: name.ext >>
    re.compile(r"<<\s*File:\s*(.+?)\s*>>"),
    # Explicit attachment mentions
    re.compile(r"(?:attached|attachment|enclosed)[:\s]+[\"']?(\S+\.(?:xls|xlsx|doc|docx|pdf|csv|txt|ppt|pptx|jpg|jpeg|png|gif|zip))[\"']?", re.IGNORECASE),
    # Standalone filenames with common extensions
    re.compile(r"\b([\w\-]+\.(?:xls|xlsx|doc|docx|pdf|csv|ppt|pptx))\b", re.IGNORECASE),
]

# -- Deal/contract IDs (Enron-specific patterns) --
_DEAL_PATTERNS = [
    re.compile(r"\b(Deal\s*#?\s*\d+)\b", re.IGNORECASE),
    re.compile(r"\b(Contract\s*#?\s*[\w\-]+)\b", re.IGNORECASE),
    re.compile(r"\b(ISDA\s+[\w\-]+)\b", re.IGNORECASE),
    re.compile(r"\b(Docket\s*(?:No\.?)?\s*[\w\-]+)\b", re.IGNORECASE),
]


def _get_context(text: str, match: re.Match, window: int = 80) -> str:
    """Get surrounding text for context."""
    start = max(0, match.start() - window)
    end = min(len(text), match.end() + window)
    ctx = text[start:end].strip()
    # Clean up whitespace
    ctx = re.sub(r"\s+", " ", ctx)
    return ctx


def extract_entities(body_text: str) -> list[ExtractedEntity]:
    """Extract all structured entities from email body text."""
    entities: list[ExtractedEntity] = []
    seen_values: set[tuple[str, str]] = set()  # dedup (type, value)

    # Monetary amounts
    for pattern in _MONETARY_PATTERNS:
        for match in pattern.finditer(body_text):
            value = match.group(0).strip()
            key = ("monetary", value)
            if key not in seen_values:
                seen_values.add(key)
                entities.append(ExtractedEntity(
                    entity_type="monetary",
                    entity_value=value,
                    context=_get_context(body_text, match),
                ))

    # Date references
    for pattern in _DATE_PATTERNS:
        for match in pattern.finditer(body_text):
            value = match.group(1).strip()
            key = ("date_ref", value)
            if key not in seen_values:
                seen_values.add(key)
                entities.append(ExtractedEntity(
                    entity_type="date_ref",
                    entity_value=value,
                    context=_get_context(body_text, match),
                ))

    # Document references
    for pattern in _DOC_PATTERNS:
        for match in pattern.finditer(body_text):
            value = match.group(1).strip() if match.lastindex else match.group(0).strip()
            key = ("document_ref", value)
            if key not in seen_values:
                seen_values.add(key)
                entities.append(ExtractedEntity(
                    entity_type="document_ref",
                    entity_value=value,
                    context=_get_context(body_text, match),
                ))

    # Deal/contract IDs
    for pattern in _DEAL_PATTERNS:
        for match in pattern.finditer(body_text):
            value = match.group(1).strip()
            key = ("deal_id", value)
            if key not in seen_values:
                seen_values.add(key)
                entities.append(ExtractedEntity(
                    entity_type="deal_id",
                    entity_value=value,
                    context=_get_context(body_text, match),
                ))

    return entities

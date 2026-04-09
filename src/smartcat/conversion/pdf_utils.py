"""PDF utilities: text-layer detection and extraction via PyMuPDF.

Used to skip OCR for PDFs that already have extractable text.
"""

from __future__ import annotations

import logging

import pymupdf

log = logging.getLogger(__name__)


def pdf_needs_ocr(data: bytes, min_text_chars: int = 50) -> bool:
    """Check if a PDF needs OCR (no usable text layer).

    Heuristics:
    1. Extract text from all pages
    2. If total text < min_text_chars → scanned, needs OCR
    3. If text exists but <30% alphanumeric → garbage text layer → needs OCR

    Args:
        data: Raw PDF bytes.
        min_text_chars: Minimum chars to consider a text layer present.

    Returns:
        True if OCR is needed, False if text can be extracted directly.
    """
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception as e:
        log.warning("pdf_utils.open_failed: %s", e)
        return True  # Can't open → try OCR

    try:
        total_text = []
        for page in doc:
            total_text.append(page.get_text().strip())

        combined = "\n".join(total_text).strip()

        if len(combined) < min_text_chars:
            return True  # No meaningful text → scanned

        # Garbage detection: if mostly non-alphanumeric, text layer is junk
        alnum = sum(1 for c in combined if c.isalnum())
        if alnum / len(combined) < 0.3:
            return True  # Garbage text layer

        return False  # Good text layer, skip OCR
    finally:
        doc.close()


def extract_pdf_text(data: bytes) -> tuple[str, int]:
    """Extract text from a PDF with a text layer (no OCR).

    Args:
        data: Raw PDF bytes.

    Returns:
        (extracted_text, page_count).
    """
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception as e:
        log.warning("pdf_utils.extract_failed: %s", e)
        return "", 0

    try:
        pages = []
        for page in doc:
            pages.append(page.get_text())
        return "\n\n".join(pages).strip(), len(pages)
    finally:
        doc.close()


def pdf_to_images(data: bytes, dpi: int = 150) -> list:
    """Convert PDF pages to PIL Images for OCR.

    Args:
        data: Raw PDF bytes.
        dpi: Resolution for rendering (higher = better OCR, more VRAM).

    Returns:
        List of PIL.Image objects, one per page.
    """
    from PIL import Image
    import io

    doc = pymupdf.open(stream=data, filetype="pdf")
    images = []

    zoom = dpi / 72.0
    matrix = pymupdf.Matrix(zoom, zoom)

    for page in doc:
        pix = page.get_pixmap(matrix=matrix)
        img_data = pix.tobytes("png")
        img = Image.open(io.BytesIO(img_data))
        images.append(img)

    doc.close()
    return images

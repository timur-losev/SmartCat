"""Tests for PDF text-layer detection and extraction."""

from __future__ import annotations

from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"

skip_no_fixtures = pytest.mark.skipif(
    not (FIXTURES_DIR / "scan_english.pdf").exists()
    or not (FIXTURES_DIR / "digital_english.pdf").exists(),
    reason="PDF fixtures not generated. Run: python tests/generate_test_pdfs.py",
)


@skip_no_fixtures
class TestPdfNeedsOcr:
    """Tests for pdf_needs_ocr() heuristic."""

    def test_scanned_pdf_needs_ocr(self):
        from smartcat.conversion.pdf_utils import pdf_needs_ocr

        data = (FIXTURES_DIR / "scan_english.pdf").read_bytes()
        assert pdf_needs_ocr(data) is True, \
            "Scanned PDF (image-only) should need OCR"

    def test_digital_pdf_no_ocr(self):
        from smartcat.conversion.pdf_utils import pdf_needs_ocr

        data = (FIXTURES_DIR / "digital_english.pdf").read_bytes()
        assert pdf_needs_ocr(data) is False, \
            "Digital PDF (with text layer) should NOT need OCR"

    def test_scanned_russian_needs_ocr(self):
        from smartcat.conversion.pdf_utils import pdf_needs_ocr

        data = (FIXTURES_DIR / "scan_russian.pdf").read_bytes()
        assert pdf_needs_ocr(data) is True

    def test_digital_russian_no_ocr(self):
        from smartcat.conversion.pdf_utils import pdf_needs_ocr

        data = (FIXTURES_DIR / "digital_russian.pdf").read_bytes()
        assert pdf_needs_ocr(data) is False

    def test_empty_bytes(self):
        from smartcat.conversion.pdf_utils import pdf_needs_ocr

        # Invalid PDF should return True (try OCR as fallback)
        assert pdf_needs_ocr(b"not a pdf") is True


@skip_no_fixtures
class TestExtractPdfText:
    """Tests for extract_pdf_text()."""

    def test_extract_digital_english(self):
        from smartcat.conversion.pdf_utils import extract_pdf_text

        data = (FIXTURES_DIR / "digital_english.pdf").read_bytes()
        text, pages = extract_pdf_text(data)

        assert pages >= 1
        assert len(text) > 50
        assert "INVOICE" in text or "invoice" in text.lower()

    def test_extract_digital_russian(self):
        from smartcat.conversion.pdf_utils import extract_pdf_text

        data = (FIXTURES_DIR / "digital_russian.pdf").read_bytes()
        text, pages = extract_pdf_text(data)

        assert pages >= 1
        assert len(text) > 50
        # Should contain Cyrillic
        has_cyrillic = any("\u0400" <= c <= "\u04ff" for c in text)
        assert has_cyrillic, f"Expected Cyrillic in digital PDF text: {text[:200]}"

    def test_scanned_pdf_returns_empty(self):
        from smartcat.conversion.pdf_utils import extract_pdf_text

        data = (FIXTURES_DIR / "scan_english.pdf").read_bytes()
        text, pages = extract_pdf_text(data)

        assert pages >= 1
        # Scanned PDF should have little/no extractable text
        assert len(text) < 50

    def test_invalid_pdf(self):
        from smartcat.conversion.pdf_utils import extract_pdf_text

        text, pages = extract_pdf_text(b"not a pdf")
        assert text == ""
        assert pages == 0


@skip_no_fixtures
class TestPdfToImages:
    """Tests for pdf_to_images()."""

    def test_converts_to_pil_images(self):
        from smartcat.conversion.pdf_utils import pdf_to_images

        data = (FIXTURES_DIR / "scan_english.pdf").read_bytes()
        images = pdf_to_images(data, dpi=72)  # low dpi for speed

        assert len(images) >= 1

        from PIL import Image
        assert isinstance(images[0], Image.Image)
        assert images[0].width > 0
        assert images[0].height > 0

    def test_dpi_affects_resolution(self):
        from smartcat.conversion.pdf_utils import pdf_to_images

        data = (FIXTURES_DIR / "scan_english.pdf").read_bytes()
        low = pdf_to_images(data, dpi=72)
        high = pdf_to_images(data, dpi=150)

        # Higher DPI should produce larger images
        assert high[0].width > low[0].width

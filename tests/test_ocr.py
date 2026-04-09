"""Tests for OCR-enabled document conversion.

Uses synthetic scanned PDF fixtures from tests/fixtures/.
Regenerate with: python tests/generate_test_pdfs.py
"""

from __future__ import annotations

from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _try_import(module: str) -> bool:
    try:
        __import__(module)
        return True
    except ImportError:
        return False


skip_no_fixtures = pytest.mark.skipif(
    not (FIXTURES_DIR / "scan_english.pdf").exists(),
    reason="PDF fixtures not generated. Run: python tests/generate_test_pdfs.py",
)

skip_no_docling = pytest.mark.skipif(
    not _try_import("docling"),
    reason="docling not installed",
)

skip_no_easyocr = pytest.mark.skipif(
    not _try_import("easyocr"),
    reason="easyocr not installed",
)

skip_no_surya = pytest.mark.skipif(
    not _try_import("surya"),
    reason="surya-ocr not installed",
)


@skip_no_fixtures
@skip_no_docling
@skip_no_easyocr
class TestOcrEnglish:
    """Test OCR on English scanned PDF."""

    def test_extracts_text(self):
        from smartcat.conversion.docling_converter import DoclingConverter

        converter = DoclingConverter(ocr_enabled=True, ocr_langs=["en"])
        pdf_bytes = (FIXTURES_DIR / "scan_english.pdf").read_bytes()

        text, pages = converter.convert_attachment(
            pdf_bytes, filename="scan_english.pdf", content_type="application/pdf"
        )

        assert len(text) > 0, "OCR should extract some text from English scan"
        text_lower = text.lower()
        assert any(w in text_lower for w in ["invoice", "trading", "shipping", "payment"]), \
            f"Expected English business terms in OCR output, got: {text[:200]}"

    def test_page_count(self):
        from smartcat.conversion.docling_converter import DoclingConverter

        converter = DoclingConverter(ocr_enabled=True, ocr_langs=["en"])
        pdf_bytes = (FIXTURES_DIR / "scan_english.pdf").read_bytes()

        _, pages = converter.convert_attachment(
            pdf_bytes, filename="scan_english.pdf", content_type="application/pdf"
        )
        assert pages >= 1


@skip_no_fixtures
@skip_no_docling
@skip_no_easyocr
class TestOcrRussian:
    """Test OCR on Russian (Cyrillic) scanned PDF."""

    def test_extracts_cyrillic(self):
        from smartcat.conversion.docling_converter import DoclingConverter

        converter = DoclingConverter(ocr_enabled=True, ocr_langs=["en", "ru"])
        pdf_bytes = (FIXTURES_DIR / "scan_russian.pdf").read_bytes()

        text, pages = converter.convert_attachment(
            pdf_bytes, filename="scan_russian.pdf", content_type="application/pdf"
        )

        assert len(text) > 0, "OCR should extract text from Russian scan"
        has_cyrillic = any("\u0400" <= c <= "\u04ff" for c in text)
        assert has_cyrillic, f"Expected Cyrillic text in OCR output, got: {text[:200]}"


@skip_no_fixtures
@skip_no_docling
@skip_no_easyocr
class TestOcrMixed:
    """Test OCR on mixed English+Russian scanned PDF."""

    def test_extracts_both_languages(self):
        from smartcat.conversion.docling_converter import DoclingConverter

        converter = DoclingConverter(ocr_enabled=True, ocr_langs=["en", "ru"])
        pdf_bytes = (FIXTURES_DIR / "scan_mixed.pdf").read_bytes()

        text, pages = converter.convert_attachment(
            pdf_bytes, filename="scan_mixed.pdf", content_type="application/pdf"
        )

        assert len(text) > 0, "OCR should extract text from mixed scan"


@skip_no_fixtures
@skip_no_surya
class TestSuryaOcrEnglish:
    """Test Surya OCR on English scanned PDF."""

    @pytest.mark.slow
    def test_surya_extracts_text(self):
        from smartcat.conversion.surya_ocr import SuryaOCR

        ocr = SuryaOCR(langs=["en"])
        pdf_bytes = (FIXTURES_DIR / "scan_english.pdf").read_bytes()

        text, pages = ocr.ocr_pdf(pdf_bytes)

        assert pages >= 1
        assert len(text) > 0, "Surya should extract text from English scan"
        text_lower = text.lower()
        assert any(w in text_lower for w in ["invoice", "trading", "shipping", "payment"]), \
            f"Expected English business terms, got: {text[:200]}"

        ocr.unload()


@skip_no_fixtures
@skip_no_surya
class TestSuryaOcrRussian:
    """Test Surya OCR on Russian (Cyrillic) scanned PDF."""

    @pytest.mark.slow
    def test_surya_extracts_cyrillic(self):
        from smartcat.conversion.surya_ocr import SuryaOCR

        ocr = SuryaOCR(langs=["en", "ru"])
        pdf_bytes = (FIXTURES_DIR / "scan_russian.pdf").read_bytes()

        text, pages = ocr.ocr_pdf(pdf_bytes)

        assert pages >= 1
        assert len(text) > 0, "Surya should extract text from Russian scan"
        has_cyrillic = any("\u0400" <= c <= "\u04ff" for c in text)
        assert has_cyrillic, f"Expected Cyrillic in Surya output, got: {text[:200]}"

        ocr.unload()


@skip_no_fixtures
@skip_no_surya
class TestSuryaViaConverter:
    """Test Surya OCR routed through DoclingConverter."""

    @pytest.mark.slow
    def test_converter_routes_to_surya(self):
        from smartcat.conversion.docling_converter import DoclingConverter

        converter = DoclingConverter(
            ocr_enabled=True, ocr_langs=["en"], ocr_engine="surya"
        )
        pdf_bytes = (FIXTURES_DIR / "scan_english.pdf").read_bytes()

        text, pages = converter.convert_attachment(
            pdf_bytes, filename="scan.pdf", content_type="application/pdf"
        )

        assert len(text) > 0, "Converter with surya engine should extract text"
        assert pages >= 1

    @pytest.mark.slow
    def test_digital_pdf_skips_ocr(self):
        """Digital PDFs should be extracted via PyMuPDF, not Surya."""
        from smartcat.conversion.docling_converter import DoclingConverter

        converter = DoclingConverter(
            ocr_enabled=True, ocr_langs=["en"], ocr_engine="surya"
        )
        pdf_bytes = (FIXTURES_DIR / "digital_english.pdf").read_bytes()

        text, pages = converter.convert_attachment(
            pdf_bytes, filename="digital.pdf", content_type="application/pdf"
        )

        assert len(text) > 50, "Digital PDF should have extractable text"
        assert "INVOICE" in text or "invoice" in text.lower()
        assert pages >= 1


@skip_no_fixtures
@skip_no_docling
class TestOcrDisabled:
    """Test that OCR can be disabled (default behavior preserved)."""

    def test_default_no_ocr(self):
        from smartcat.conversion.docling_converter import DoclingConverter

        converter = DoclingConverter()  # default: ocr_enabled=False
        pdf_bytes = (FIXTURES_DIR / "scan_english.pdf").read_bytes()

        text, pages = converter.convert_attachment(
            pdf_bytes, filename="scan_english.pdf", content_type="application/pdf"
        )
        # Scanned PDF without OCR should return empty or minimal text
        assert len(text) < 50, \
            "Without OCR, scanned PDF should produce little/no text"

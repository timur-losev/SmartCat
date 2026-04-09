"""Docling-based document conversion for HTML bodies and binary attachments."""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path

log = logging.getLogger(__name__)

# Formats Docling can handle
_SUPPORTED_EXTENSIONS = {
    ".pdf", ".docx", ".doc", ".pptx", ".xlsx",
    ".html", ".htm",
    ".png", ".jpg", ".jpeg", ".tiff", ".bmp",
}

# Map MIME types to file extensions for tempfile naming
_MIME_TO_EXT = {
    "application/pdf": ".pdf",
    "application/msword": ".doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/vnd.ms-excel": ".xls",
    "application/vnd.ms-powerpoint": ".ppt",
    "text/html": ".html",
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/tiff": ".tiff",
}


def _ext_from_filename_or_mime(filename: str, content_type: str) -> str:
    """Determine file extension from filename or MIME type."""
    if filename:
        ext = Path(filename).suffix.lower()
        if ext:
            return ext
    return _MIME_TO_EXT.get(content_type, "")


class DoclingConverter:
    """Wrapper around Docling DocumentConverter for email processing."""

    def __init__(self, ocr_enabled: bool = False,
                 ocr_langs: list[str] | None = None,
                 ocr_engine: str = "easyocr"):
        """Initialize converter.

        Args:
            ocr_enabled: Enable OCR for scanned PDFs/images.
            ocr_langs: Language codes for OCR (e.g. ["en", "ru"]).
                       Only used when ocr_enabled=True.
            ocr_engine: OCR backend — "surya" (faster, better quality)
                        or "easyocr" (fallback). Only used when ocr_enabled=True.
        """
        self._converter = None
        self._ocr_enabled = ocr_enabled
        self._ocr_langs = ocr_langs or ["en"]
        self._ocr_engine = ocr_engine
        self._surya = None

    def _ensure_converter(self):
        if self._converter is None:
            from docling.document_converter import DocumentConverter

            if self._ocr_enabled:
                try:
                    from docling.datamodel.pipeline_options import (
                        PdfPipelineOptions,
                        EasyOcrOptions,
                    )
                    ocr_options = EasyOcrOptions(lang=self._ocr_langs)
                    pipeline_options = PdfPipelineOptions(
                        do_ocr=True,
                        ocr_options=ocr_options,
                    )
                    self._converter = DocumentConverter(
                        pipeline_options={"pdf": pipeline_options}
                    )
                    log.info("docling.ocr_enabled: langs=%s", self._ocr_langs)
                except ImportError:
                    log.warning("docling.ocr_import_failed: falling back to no OCR")
                    self._converter = DocumentConverter()
                except Exception as e:
                    log.warning("docling.ocr_config_failed: %s, falling back", e)
                    self._converter = DocumentConverter()
            else:
                self._converter = DocumentConverter()

    def convert_html(self, html: str) -> str:
        """Convert HTML string to clean markdown.

        Args:
            html: Raw HTML content from email body.

        Returns:
            Clean markdown text, or original HTML on failure.
        """
        if not html or not html.strip():
            return ""

        self._ensure_converter()

        try:
            with tempfile.NamedTemporaryFile(
                suffix=".html", mode="w", encoding="utf-8", delete=False
            ) as f:
                f.write(html)
                tmp_path = Path(f.name)

            result = self._converter.convert(str(tmp_path))
            markdown = result.document.export_to_markdown()
            return markdown.strip() if markdown else ""
        except Exception as e:
            log.warning("docling.html_convert_failed: %s", e)
            return html  # fallback to original
        finally:
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception:
                pass

    def convert_attachment(self, data: bytes, filename: str = "",
                           content_type: str = "") -> tuple[str, int]:
        """Convert binary attachment to extracted text.

        Args:
            data: Raw binary payload.
            filename: Original filename (used for extension detection).
            content_type: MIME type (fallback for extension detection).

        Returns:
            (extracted_text, page_count). Empty string if unsupported/failed.
        """
        if not data:
            return "", 0

        ext = _ext_from_filename_or_mime(filename, content_type)
        if ext and ext not in _SUPPORTED_EXTENSIONS:
            log.debug("docling.unsupported_format: ext=%s file=%s", ext, filename)
            return "", 0

        if not ext:
            log.debug("docling.unknown_format: mime=%s file=%s", content_type, filename)
            return "", 0

        # PDF fast-path: check text layer before expensive OCR
        if ext == ".pdf" and self._ocr_enabled:
            try:
                from smartcat.conversion.pdf_utils import pdf_needs_ocr, extract_pdf_text

                if not pdf_needs_ocr(data):
                    # Has text layer — extract directly, skip OCR
                    text, page_count = extract_pdf_text(data)
                    if text:
                        log.info("pdf.has_text_layer: skipping OCR file=%s pages=%d",
                                 filename, page_count)
                        return text, page_count

                # Needs OCR — use Surya if configured
                if self._ocr_engine == "surya":
                    try:
                        return self._ocr_with_surya(data, filename)
                    except Exception as e:
                        log.warning("surya.failed, falling back to docling: %s", e)
                        # Fall through to Docling

            except ImportError:
                log.debug("pymupdf not installed, skipping text-layer check")

        # Default path: use Docling converter
        self._ensure_converter()

        try:
            with tempfile.NamedTemporaryFile(
                suffix=ext, delete=False
            ) as f:
                f.write(data)
                tmp_path = Path(f.name)

            result = self._converter.convert(str(tmp_path))
            markdown = result.document.export_to_markdown()
            text = markdown.strip() if markdown else ""

            # Try to get page count for PDFs
            page_count = 0
            try:
                page_count = result.document.num_pages()
            except (AttributeError, Exception):
                pass

            return text, page_count
        except Exception as e:
            log.warning("docling.attachment_convert_failed: file=%s err=%s", filename, e)
            return "", 0
        finally:
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception:
                pass

    def _ocr_with_surya(self, data: bytes, filename: str) -> tuple[str, int]:
        """OCR a scanned PDF using standalone Surya."""
        if self._surya is None:
            from smartcat.conversion.surya_ocr import SuryaOCR
            self._surya = SuryaOCR(langs=self._ocr_langs)

        log.info("surya.ocr_start: file=%s", filename)
        text, page_count = self._surya.ocr_pdf(data)
        log.info("surya.ocr_done: file=%s pages=%d chars=%d",
                 filename, page_count, len(text))
        return text, page_count

    def is_supported(self, filename: str = "", content_type: str = "") -> bool:
        """Check if a file format is supported for conversion."""
        ext = _ext_from_filename_or_mime(filename, content_type)
        return ext in _SUPPORTED_EXTENSIONS

"""Standalone Surya OCR wrapper for scanned PDF processing.

Uses Surya directly (not via Docling plugin) for full control over
batch sizes, VRAM, and language configuration.

⚠️ License: surya-ocr is GPLv3+. Keep as optional dependency.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PIL import Image

from smartcat.conversion.pdf_utils import pdf_to_images

log = logging.getLogger(__name__)


class SuryaOCR:
    """Standalone Surya OCR engine for scanned PDFs."""

    def __init__(self, langs: list[str] | None = None):
        """Initialize Surya OCR.

        Args:
            langs: Language codes (e.g. ["en", "ru"]). Surya supports 90+ languages.
                   Models are downloaded on first use (~200MB total).
        """
        self._langs = langs or ["en", "ru"]
        self._recognition = None
        self._detection = None

    def _ensure_models(self) -> None:
        """Lazy-load Surya models on first use."""
        if self._recognition is not None:
            return

        log.info("surya.loading_models")
        from surya.detection import DetectionPredictor
        from surya.recognition import RecognitionPredictor

        try:
            # Surya >= 0.17: RecognitionPredictor requires a FoundationPredictor
            from surya.foundation import FoundationPredictor
            foundation = FoundationPredictor()
            self._detection = DetectionPredictor()
            self._recognition = RecognitionPredictor(foundation)
        except ImportError:
            # Older Surya versions: no FoundationPredictor
            self._detection = DetectionPredictor()
            self._recognition = RecognitionPredictor()

        log.info("surya.models_loaded")

    def ocr_images(self, images: list[Image.Image]) -> list[str]:
        """Run OCR on a list of PIL Images.

        Args:
            images: List of PIL Image objects (one per page).

        Returns:
            List of extracted text strings, one per image.
        """
        if not images:
            return []

        self._ensure_models()

        try:
            predictions = self._recognition(
                images,
                det_predictor=self._detection,
            )

            texts = []
            for pred in predictions:
                # Each prediction has text_lines with individual line texts
                if hasattr(pred, "text_lines"):
                    page_text = "\n".join(
                        line.text for line in pred.text_lines if line.text
                    )
                elif hasattr(pred, "text"):
                    page_text = pred.text
                else:
                    page_text = str(pred)
                texts.append(page_text)

            return texts

        except Exception as e:
            log.error("surya.ocr_failed: %s", e)
            return [""] * len(images)

    def ocr_pdf(self, data: bytes, dpi: int = 150) -> tuple[str, int]:
        """OCR a scanned PDF.

        Args:
            data: Raw PDF bytes.
            dpi: Resolution for page rendering.

        Returns:
            (extracted_text, page_count).
        """
        images = pdf_to_images(data, dpi=dpi)
        if not images:
            return "", 0

        texts = self.ocr_images(images)
        combined = "\n\n".join(t for t in texts if t).strip()
        return combined, len(images)

    def unload(self) -> None:
        """Free GPU memory by unloading models."""
        self._recognition = None
        self._detection = None
        log.info("surya.models_unloaded")

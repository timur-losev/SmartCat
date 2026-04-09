"""Generate synthetic scanned PDF fixtures for OCR testing.

Creates PDFs by rendering text onto images (simulating scanned documents),
then embedding those images in PDFs via reportlab.

Run once to create fixtures, then commit them:
    python tests/generate_test_pdfs.py

Requires: Pillow, reportlab
"""

from __future__ import annotations

import sys
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

FIXTURES_DIR = Path(__file__).parent / "fixtures"

# Text samples for different languages
ENGLISH_TEXT = """INVOICE #2024-0891
Date: March 15, 2024
From: Global Trading Corp.
To: Pacific Logistics Ltd.

Dear Partner,

Please find attached the updated shipping manifest
for container GATU-7823451. Total value: $45,230.00

Payment terms: Net 30 days.
Bank: First National, Account: 1234567890

Best regards,
John Smith, Operations Manager"""

RUSSIAN_TEXT = """СЧЕТ-ФАКТУРА №2024-0891
Дата: 15 марта 2024 г.
От: ООО "Глобал Трейдинг"
Кому: ООО "Пасифик Логистикс"

Уважаемый партнер,

Направляем обновленную грузовую накладную
на контейнер GATU-7823451. Общая стоимость: $45 230,00

Условия оплаты: 30 дней нетто.
Банк: Первый Национальный, Счет: 1234567890

С уважением,
Иван Петров, Менеджер по операциям"""

MIXED_TEXT = """MEETING NOTES / ПРОТОКОЛ СОВЕЩАНИЯ
Date/Дата: 2024-03-15

Participants / Участники:
- John Smith (Global Trading)
- Иван Петров (Глобал Трейдинг)

Discussion / Обсуждение:
1. Container shipment GATU-7823451 confirmed
   Подтверждена отправка контейнера GATU-7823451
2. Total value: $45,230.00 / Общая стоимость: $45 230,00
3. Next meeting: March 22 / Следующая встреча: 22 марта"""


def text_to_scanned_pdf(text: str, output_path: Path, dpi: int = 150) -> None:
    """Render text as image, then embed in PDF to simulate a scan."""
    # Create image from text
    width_px = int(A4[0] / 72 * dpi)
    height_px = int(A4[1] / 72 * dpi)

    img = Image.new("L", (width_px, height_px), color=245)  # light gray background
    draw = ImageDraw.Draw(img)

    # Use default font (monospace, available everywhere)
    try:
        font = ImageFont.truetype("arial.ttf", size=int(dpi * 0.12))
    except OSError:
        try:
            font = ImageFont.truetype("DejaVuSans.ttf", size=int(dpi * 0.12))
        except OSError:
            font = ImageFont.load_default()

    # Draw text with margins
    margin_x = int(dpi * 0.8)
    margin_y = int(dpi * 0.8)
    y = margin_y

    for line in text.split("\n"):
        draw.text((margin_x, y), line, fill=30, font=font)
        y += int(dpi * 0.18)

    # Add slight noise to simulate scan artifacts
    import random
    random.seed(42)
    for _ in range(200):
        x = random.randint(0, width_px - 1)
        y = random.randint(0, height_px - 1)
        img.putpixel((x, y), random.randint(180, 220))

    # Save image as JPEG to temp file (reportlab needs a file path)
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
        img.save(tmp, format="JPEG", quality=85)
        tmp_path = tmp.name

    c = canvas.Canvas(str(output_path), pagesize=A4)
    c.drawImage(tmp_path, 0, 0, width=A4[0], height=A4[1])
    c.save()

    Path(tmp_path).unlink(missing_ok=True)
    print(f"Created: {output_path}")


def text_to_digital_pdf(text: str, output_path: Path) -> None:
    """Create a PDF with a real text layer (not scanned).

    These PDFs have extractable text and should NOT need OCR.
    """
    c = canvas.Canvas(str(output_path), pagesize=A4)

    # Try to use a font that supports Cyrillic
    try:
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        pdfmetrics.registerFont(TTFont("Arial", "arial.ttf"))
        font_name = "Arial"
    except Exception:
        font_name = "Helvetica"

    c.setFont(font_name, 11)

    # Draw text line by line
    y = A4[1] - 72  # Start 1 inch from top
    for line in text.split("\n"):
        c.drawString(72, y, line)
        y -= 16
        if y < 72:
            c.showPage()
            c.setFont(font_name, 11)
            y = A4[1] - 72

    c.save()
    print(f"Created: {output_path}")


def main():
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)

    # Scanned PDFs (image-only, no text layer)
    text_to_scanned_pdf(ENGLISH_TEXT, FIXTURES_DIR / "scan_english.pdf")
    text_to_scanned_pdf(RUSSIAN_TEXT, FIXTURES_DIR / "scan_russian.pdf")
    text_to_scanned_pdf(MIXED_TEXT, FIXTURES_DIR / "scan_mixed.pdf")

    # Digital PDFs (with text layer, OCR not needed)
    text_to_digital_pdf(ENGLISH_TEXT, FIXTURES_DIR / "digital_english.pdf")
    text_to_digital_pdf(RUSSIAN_TEXT, FIXTURES_DIR / "digital_russian.pdf")

    print(f"\nAll fixtures created in {FIXTURES_DIR}")


if __name__ == "__main__":
    main()

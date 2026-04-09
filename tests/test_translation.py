"""Tests for language detection and translation modules."""

from __future__ import annotations

import pytest


class TestLanguageDetector:
    """Tests for smartcat.translation.detector."""

    def test_detect_english(self):
        from smartcat.translation.detector import detect_language

        result = detect_language(
            "Dear partner, please find the invoice attached. "
            "Total payment amount is forty-five thousand dollars."
        )
        assert result == "en"

    def test_detect_russian(self):
        from smartcat.translation.detector import detect_language

        result = detect_language(
            "Уважаемый партнер, направляем вам счет-фактуру. "
            "Общая сумма платежа составляет сорок пять тысяч долларов."
        )
        assert result == "ru"

    def test_detect_serbian(self):
        from smartcat.translation.detector import detect_language

        result = detect_language(
            "Poštovani partneru, u prilogu se nalazi faktura. "
            "Ukupan iznos plaćanja je četrdeset pet hiljada dolara."
        )
        # langdetect may return 'hr' or 'sr' for Serbian/Croatian
        assert result in ("sr", "hr", "bs")

    def test_detect_short_text(self):
        from smartcat.translation.detector import detect_language

        result = detect_language("Hi")
        assert result == "unknown"

    def test_detect_empty(self):
        from smartcat.translation.detector import detect_language

        result = detect_language("")
        assert result == "unknown"

    def test_detect_mixed_defaults_to_primary(self):
        from smartcat.translation.detector import detect_language

        # Mostly Russian with some English words
        result = detect_language(
            "Направляем обновленную грузовую накладную на контейнер. "
            "Условия оплаты: тридцать дней. Банк: Первый Национальный."
        )
        assert result == "ru"


class TestTranslator:
    """Tests for smartcat.translation.translator.

    Note: First run downloads language packs (~100MB each).
    Tests are marked slow accordingly.
    """

    @pytest.mark.slow
    def test_translate_russian(self):
        from smartcat.translation.translator import translate_to_english

        result = translate_to_english("Привет, как дела?", "ru")

        assert result is not None
        assert len(result) > 0
        # Should be English now
        from smartcat.translation.detector import detect_language
        assert detect_language(result) == "en"

    @pytest.mark.slow
    def test_translate_english_passthrough(self):
        from smartcat.translation.translator import translate_to_english

        text = "Hello, how are you doing today?"
        result = translate_to_english(text, "en")

        assert result == text  # English should pass through unchanged

    @pytest.mark.slow
    def test_translate_unknown_passthrough(self):
        from smartcat.translation.translator import translate_to_english

        text = "Some text"
        result = translate_to_english(text, "unknown")

        assert result == text

    @pytest.mark.slow
    def test_translate_longer_russian(self):
        from smartcat.translation.translator import translate_to_english

        text = (
            "Уважаемый партнер, направляем вам обновленную грузовую накладную "
            "на контейнер. Общая стоимость составляет сорок пять тысяч долларов. "
            "Условия оплаты: тридцать дней."
        )
        result = translate_to_english(text, "ru")

        assert result is not None
        assert len(result) > 20
        # Should contain some recognizable English words
        result_lower = result.lower()
        assert any(w in result_lower for w in ["partner", "container", "dollar", "payment", "thousand"])

    @pytest.mark.slow
    def test_is_supported(self):
        from smartcat.translation.translator import is_supported

        assert is_supported("en") is True
        assert is_supported("ru") is True
        # Unsupported exotic language
        # (don't test 'sr' here as it triggers a download)


class TestEndToEnd:
    """End-to-end: detect + translate pipeline."""

    @pytest.mark.slow
    def test_detect_and_translate_russian(self):
        from smartcat.translation.detector import detect_language
        from smartcat.translation.translator import translate_to_english

        text = "Направляем счет-фактуру на сумму сорок пять тысяч долларов."

        lang = detect_language(text)
        assert lang == "ru"

        translated = translate_to_english(text, lang)
        assert translated is not None
        assert len(translated) > 10

        # Verify output is English
        out_lang = detect_language(translated)
        assert out_lang == "en"

    @pytest.mark.slow
    def test_english_noop(self):
        from smartcat.translation.detector import detect_language
        from smartcat.translation.translator import translate_to_english

        text = "Please find the updated shipping manifest for the container."

        lang = detect_language(text)
        assert lang == "en"

        result = translate_to_english(text, lang)
        assert result == text  # no-op for English

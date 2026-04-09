"""Offline translation via argos-translate.

Supports Russian, Serbian/Croatian/Montenegrin → English.
Downloads language packs on first use.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)

# Lazy-loaded translation objects
_installed_languages: dict[str, object] = {}
_translations: dict[str, object] = {}


def _ensure_package(from_code: str, to_code: str = "en") -> bool:
    """Ensure argos-translate language pack is installed.

    Downloads if not already present. Returns True if available.
    """
    cache_key = f"{from_code}->{to_code}"
    if cache_key in _translations:
        return True

    try:
        import argostranslate.package
        import argostranslate.translate

        # Check if already installed
        installed = argostranslate.translate.get_installed_languages()
        from_lang = None
        to_lang = None
        for lang in installed:
            if lang.code == from_code:
                from_lang = lang
            if lang.code == to_code:
                to_lang = lang

        if from_lang and to_lang:
            translation = from_lang.get_translation(to_lang)
            if translation:
                _translations[cache_key] = translation
                return True

        # Need to download
        log.info("translator.downloading_pack: %s -> %s", from_code, to_code)
        argostranslate.package.update_package_index()
        available = argostranslate.package.get_available_packages()

        pkg = next(
            (p for p in available
             if p.from_code == from_code and p.to_code == to_code),
            None,
        )
        if pkg is None:
            log.warning("translator.no_package: %s -> %s", from_code, to_code)
            return False

        argostranslate.package.install_from_path(pkg.download())

        # Reload
        installed = argostranslate.translate.get_installed_languages()
        from_lang = next((l for l in installed if l.code == from_code), None)
        to_lang = next((l for l in installed if l.code == to_code), None)

        if from_lang and to_lang:
            translation = from_lang.get_translation(to_lang)
            if translation:
                _translations[cache_key] = translation
                return True

        return False

    except Exception as e:
        log.error("translator.install_failed: %s -> %s err=%s", from_code, to_code, e)
        return False


# Map detected language codes to argos-translate source codes.
# Serbian (sr), Croatian (hr), Montenegrin — all use Serbo-Croatian in argos.
_LANG_CODE_MAP = {
    "ru": "ru",
    "sr": "sr",
    "hr": "hr",
    "bs": "bs",  # Bosnian
    "mk": "mk",  # Macedonian
    # Montenegrin is detected as 'sr' or 'hr' by langdetect
}

# Languages that don't need translation
_SKIP_LANGS = {"en", "unknown"}


def translate_to_english(text: str, source_lang: str) -> str | None:
    """Translate text to English using argos-translate.

    Args:
        text: Text to translate.
        source_lang: ISO 639-1 code from detector.

    Returns:
        Translated text, or None if translation not available.
    """
    if source_lang in _SKIP_LANGS:
        return text

    from_code = _LANG_CODE_MAP.get(source_lang, source_lang)

    if not _ensure_package(from_code, "en"):
        log.warning("translator.unavailable: %s -> en", from_code)
        return None

    cache_key = f"{from_code}->en"
    translation = _translations.get(cache_key)
    if translation is None:
        return None

    try:
        return translation.translate(text)
    except Exception as e:
        log.warning("translator.translate_failed: lang=%s err=%s", from_code, e)
        return None


def is_supported(lang_code: str) -> bool:
    """Check if a language code can be translated to English."""
    if lang_code in _SKIP_LANGS:
        return True
    from_code = _LANG_CODE_MAP.get(lang_code, lang_code)
    return _ensure_package(from_code, "en")

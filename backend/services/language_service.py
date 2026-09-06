"""
Language Detection Service

Detects email language (Chinese/English) using Unicode character analysis.
Supports mixed-language detection and language-specific template routing.
No external dependencies — uses Unicode ranges for CJK detection.
"""

import re
import unicodedata
from typing import Dict, Any, Optional, Tuple
from utils.logger import get_logger


logger = get_logger('language_service')


class Language:
    """Supported language codes."""
    CHINESE = "zh"
    ENGLISH = "en"
    MIXED = "mixed"
    UNKNOWN = "unknown"


# CJK Unicode ranges
_CJK_RANGES = [
    (0x4E00, 0x9FFF),    # CJK Unified Ideographs
    (0x3400, 0x4DBF),    # CJK Unified Ideographs Extension A
    (0xF900, 0xFAFF),    # CJK Compatibility Ideographs
    (0x2E80, 0x2EFF),    # CJK Radicals Supplement
    (0x3000, 0x303F),    # CJK Symbols and Punctuation
    (0xFF00, 0xFFEF),    # Fullwidth Forms
]


def _is_cjk(char: str) -> bool:
    cp = ord(char)
    return any(lo <= cp <= hi for lo, hi in _CJK_RANGES)


def _is_latin(char: str) -> bool:
    try:
        name = unicodedata.name(char, '')
        return 'LATIN' in name
    except ValueError:
        return False


class LanguageService:
    """Detects language and provides language-aware routing."""

    def detect_language(self, text: str) -> Dict[str, Any]:
        """
        Detect the primary language of the text.

        Returns:
            Dict with language code, confidence, and character breakdown.
        """
        if not text or not text.strip():
            return {'language': Language.UNKNOWN, 'confidence': 0.0, 'details': {}}

        cleaned = re.sub(r'[\s\d\W]+', '', text)
        if not cleaned:
            return {'language': Language.UNKNOWN, 'confidence': 0.0, 'details': {}}

        total = len(cleaned)
        cjk_count = sum(1 for c in cleaned if _is_cjk(c))
        latin_count = sum(1 for c in cleaned if _is_latin(c))

        cjk_ratio = cjk_count / total
        latin_ratio = latin_count / total

        if cjk_ratio > 0.5:
            language = Language.CHINESE
            confidence = min(0.95, 0.5 + cjk_ratio * 0.5)
        elif latin_ratio > 0.5:
            language = Language.ENGLISH
            confidence = min(0.95, 0.5 + latin_ratio * 0.5)
        elif cjk_ratio > 0.2 and latin_ratio > 0.2:
            language = Language.MIXED
            confidence = 0.7
        elif cjk_ratio > latin_ratio:
            language = Language.CHINESE
            confidence = 0.6
        elif latin_ratio > cjk_ratio:
            language = Language.ENGLISH
            confidence = 0.6
        else:
            language = Language.UNKNOWN
            confidence = 0.3

        result = {
            'language': language,
            'confidence': round(confidence, 2),
            'details': {
                'cjk_ratio': round(cjk_ratio, 3),
                'latin_ratio': round(latin_ratio, 3),
                'total_chars': total,
                'cjk_chars': cjk_count,
                'latin_chars': latin_count,
            }
        }

        logger.info("Language detected", {
            'language': language,
            'confidence': result['confidence']
        })

        return result

    def get_primary_language(self, text: str) -> str:
        """Return just the language code."""
        result = self.detect_language(text)
        lang = result['language']
        if lang == Language.MIXED:
            return Language.CHINESE if result['details']['cjk_ratio'] >= result['details']['latin_ratio'] else Language.ENGLISH
        return lang

    def get_reply_language(self, subject: str, body: str) -> str:
        """
        Determine which language to use for the reply.
        Replies should match the customer's language.
        """
        full_text = f"{subject or ''}\n{body or ''}"
        return self.get_primary_language(full_text)

    def get_greeting(self, language: str, customer_name: str) -> str:
        """Get language-appropriate greeting."""
        if language == Language.CHINESE:
            return f"尊敬的 {customer_name}，"
        return f"Dear {customer_name},"

    def get_closing(self, language: str) -> str:
        """Get language-appropriate closing."""
        if language == Language.CHINESE:
            return "此致，\nMIS2001 Dev Ltd.\n+86 123 456 7890"
        return "Best regards,\nMIS2001 Dev Ltd.\n+86 123 456 7890"

    def get_template_key(self, category: str, language: str) -> str:
        """Get the template key for a category and language combination."""
        return f"{category}_{language}"

    def is_chinese(self, text: str) -> bool:
        """Quick check if text is primarily Chinese."""
        return self.get_primary_language(text) == Language.CHINESE

    def is_english(self, text: str) -> bool:
        """Quick check if text is primarily English."""
        return self.get_primary_language(text) == Language.ENGLISH


# Singleton
_language_service = None


def get_language_service() -> LanguageService:
    global _language_service
    if _language_service is None:
        _language_service = LanguageService()
    return _language_service

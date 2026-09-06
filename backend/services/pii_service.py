"""
PII (Personally Identifiable Information) Service

Detects and redacts PII from text to protect customer privacy.
Supports multiple PII types: emails, phone numbers, credit cards, SSNs, addresses.
"""

import re
from typing import Dict, List, Tuple, Optional
from enum import Enum
from utils.logger import get_logger


logger = get_logger('pii_service')


class PIIType(Enum):
    """Types of PII that can be detected."""
    EMAIL = "email"
    PHONE = "phone"
    CREDIT_CARD = "credit_card"
    SSN = "ssn"
    ADDRESS = "address"
    PASSPORT = "passport"
    ID_NUMBER = "id_number"


class RedactionLevel(Enum):
    """Levels of PII redaction."""
    NONE = "none"           # No redaction
    PARTIAL = "partial"     # Mask middle characters
    FULL = "full"          # Replace with [REDACTED]


class PIIService:
    """Service for detecting and redacting PII."""

    # Regex patterns for PII detection
    PATTERNS = {
        PIIType.EMAIL: r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b',
        PIIType.PHONE: r'\b(?:\+?86)?[-.\s]?1[3-9]\d{9}\b|\b(?:\+?1)?[-.\s]?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b',
        PIIType.CREDIT_CARD: r'\b(?:\d{4}[-\s]?){3}\d{4}\b',
        PIIType.SSN: r'\b\d{3}-\d{2}-\d{4}\b',
        PIIType.PASSPORT: r'\b[A-Z]{1,2}\d{6,9}\b',
        PIIType.ID_NUMBER: r'\b\d{15}|\d{18}\b',  # Chinese ID numbers
    }

    def __init__(self):
        """Initialize PIIService."""
        self.logger = logger

    def detect_pii(
        self,
        text: str,
        pii_types: Optional[List[PIIType]] = None
    ) -> Dict[PIIType, List[str]]:
        """
        Detect PII in text.

        Args:
            text: Text to scan for PII
            pii_types: Specific PII types to detect (None = all types)

        Returns:
            Dictionary mapping PII types to list of detected instances
        """
        if pii_types is None:
            pii_types = list(PIIType)

        detected = {}

        for pii_type in pii_types:
            if pii_type in self.PATTERNS:
                pattern = self.PATTERNS[pii_type]
                matches = re.findall(pattern, text)
                if matches:
                    detected[pii_type] = matches
                    self.logger.info(f"Detected {pii_type.value}", {
                        'count': len(matches),
                        'type': pii_type.value
                    })

        return detected

    def redact_pii(
        self,
        text: str,
        redaction_level: RedactionLevel = RedactionLevel.FULL,
        pii_types: Optional[List[PIIType]] = None
    ) -> Tuple[str, Dict[PIIType, int]]:
        """
        Redact PII from text.

        Args:
            text: Text to redact
            redaction_level: Level of redaction to apply
            pii_types: Specific PII types to redact (None = all types)

        Returns:
            Tuple of (redacted_text, redaction_counts)
        """
        if redaction_level == RedactionLevel.NONE:
            return text, {}

        if pii_types is None:
            pii_types = list(PIIType)

        redacted_text = text
        redaction_counts = {}

        for pii_type in pii_types:
            if pii_type not in self.PATTERNS:
                continue

            pattern = self.PATTERNS[pii_type]
            matches = re.findall(pattern, redacted_text)

            if matches:
                redaction_counts[pii_type] = len(matches)

                if redaction_level == RedactionLevel.FULL:
                    redacted_text = re.sub(
                        pattern,
                        f'[{pii_type.value.upper()}_REDACTED]',
                        redacted_text
                    )
                elif redaction_level == RedactionLevel.PARTIAL:
                    redacted_text = self._partial_redact(
                        redacted_text,
                        pattern,
                        pii_type
                    )

        if redaction_counts:
            self.logger.info("PII redacted", {
                'redaction_level': redaction_level.value,
                'counts': {k.value: v for k, v in redaction_counts.items()}
            })

        return redacted_text, redaction_counts

    def _partial_redact(
        self,
        text: str,
        pattern: str,
        pii_type: PIIType
    ) -> str:
        """
        Partially redact PII (mask middle characters).

        Args:
            text: Text to redact
            pattern: Regex pattern
            pii_type: Type of PII

        Returns:
            Partially redacted text
        """
        def mask_match(match):
            value = match.group(0)

            if pii_type == PIIType.EMAIL:
                # Mask email: user@domain.com -> u***@domain.com
                parts = value.split('@')
                if len(parts) == 2:
                    username = parts[0]
                    if len(username) > 2:
                        masked_username = username[0] + '*' * (len(username) - 2) + username[-1]
                    else:
                        masked_username = username[0] + '*'
                    return f"{masked_username}@{parts[1]}"

            elif pii_type == PIIType.PHONE:
                # Mask phone: 13812345678 -> 138****5678
                digits = re.sub(r'\D', '', value)
                if len(digits) >= 7:
                    return digits[:3] + '****' + digits[-4:]

            elif pii_type == PIIType.CREDIT_CARD:
                # Mask card: 1234-5678-9012-3456 -> 1234-****-****-3456
                digits = re.sub(r'\D', '', value)
                if len(digits) >= 12:
                    return digits[:4] + '-****-****-' + digits[-4:]

            elif pii_type == PIIType.SSN:
                # Mask SSN: 123-45-6789 -> ***-**-6789
                return '***-**-' + value[-4:]

            elif pii_type == PIIType.ID_NUMBER:
                # Mask ID: 123456789012345678 -> 123456********5678
                if len(value) >= 10:
                    return value[:6] + '*' * (len(value) - 10) + value[-4:]

            # Default: mask middle 50%
            if len(value) > 4:
                visible = len(value) // 4
                return value[:visible] + '*' * (len(value) - 2 * visible) + value[-visible:]
            else:
                return '*' * len(value)

        return re.sub(pattern, mask_match, text)

    def contains_pii(
        self,
        text: str,
        pii_types: Optional[List[PIIType]] = None
    ) -> bool:
        """
        Check if text contains any PII.

        Args:
            text: Text to check
            pii_types: Specific PII types to check (None = all types)

        Returns:
            True if PII detected, False otherwise
        """
        detected = self.detect_pii(text, pii_types)
        return len(detected) > 0

    def get_pii_summary(
        self,
        text: str,
        pii_types: Optional[List[PIIType]] = None
    ) -> Dict[str, any]:
        """
        Get summary of PII in text.

        Args:
            text: Text to analyze
            pii_types: Specific PII types to check (None = all types)

        Returns:
            Summary dictionary with counts and types
        """
        detected = self.detect_pii(text, pii_types)

        return {
            'contains_pii': len(detected) > 0,
            'pii_types_found': [pii_type.value for pii_type in detected.keys()],
            'total_instances': sum(len(matches) for matches in detected.values()),
            'counts_by_type': {
                pii_type.value: len(matches)
                for pii_type, matches in detected.items()
            }
        }

    def sanitize_for_logging(
        self,
        text: str,
        max_length: int = 200
    ) -> str:
        """
        Sanitize text for safe logging (redact PII and truncate).

        Args:
            text: Text to sanitize
            max_length: Maximum length of output

        Returns:
            Sanitized text safe for logging
        """
        # Redact PII
        redacted_text, _ = self.redact_pii(text, RedactionLevel.FULL)

        # Truncate
        if len(redacted_text) > max_length:
            redacted_text = redacted_text[:max_length] + '...'

        return redacted_text

    def validate_email_privacy(
        self,
        subject: str,
        body: str
    ) -> Dict[str, any]:
        """
        Validate email for privacy compliance.

        Args:
            subject: Email subject
            body: Email body

        Returns:
            Validation result with PII detection and recommendations
        """
        full_text = f"{subject}\n{body}"

        # Detect PII
        detected = self.detect_pii(full_text)

        # Determine risk level
        risk_level = 'low'
        if PIIType.CREDIT_CARD in detected or PIIType.SSN in detected:
            risk_level = 'high'
        elif PIIType.PHONE in detected or PIIType.ID_NUMBER in detected:
            risk_level = 'medium'
        elif PIIType.EMAIL in detected:
            risk_level = 'low'

        # Generate recommendations
        recommendations = []
        if PIIType.CREDIT_CARD in detected:
            recommendations.append("Credit card detected - ensure secure handling")
        if PIIType.SSN in detected:
            recommendations.append("SSN detected - high sensitivity data")
        if len(detected) > 3:
            recommendations.append("Multiple PII types detected - review carefully")

        return {
            'contains_pii': len(detected) > 0,
            'risk_level': risk_level,
            'pii_types': [pii_type.value for pii_type in detected.keys()],
            'total_instances': sum(len(matches) for matches in detected.values()),
            'recommendations': recommendations,
            'should_flag_for_review': risk_level in ['high', 'medium']
        }


# Global instance
_pii_service = None


def get_pii_service() -> PIIService:
    """
    Get global PIIService instance (singleton pattern).

    Returns:
        PIIService instance
    """
    global _pii_service
    if _pii_service is None:
        _pii_service = PIIService()
    return _pii_service

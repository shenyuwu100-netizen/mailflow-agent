"""
Configuration loader service.
Loads and validates YAML configuration files.
"""

import os
import yaml
import json
from typing import Dict, List, Any, Optional
from pathlib import Path


class ConfigLoader:
    """Loads and validates configuration from YAML files."""

    def __init__(self, config_dir: Optional[str] = None):
        """
        Initialize ConfigLoader.

        Args:
            config_dir: Path to configuration directory. Defaults to backend/config/
        """
        if config_dir is None:
            # Default to backend/config directory
            backend_dir = Path(__file__).parent.parent
            config_dir = backend_dir / "config"

        self.config_dir = Path(config_dir)
        self._categories_cache = None
        self._thresholds_cache = None
        self._schema_cache = None

    def load_categories(self) -> Dict[str, Any]:
        """
        Load categories configuration from categories.yaml.

        Returns:
            Dictionary containing categories, business_hints, and non_business_hints
        """
        if self._categories_cache is not None:
            return self._categories_cache

        categories_file = self.config_dir / "categories.yaml"

        if not categories_file.exists():
            raise FileNotFoundError(f"Categories config not found: {categories_file}")

        with open(categories_file, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)

        # Validate structure
        if 'categories' not in config:
            raise ValueError("Missing 'categories' key in categories.yaml")
        if 'business_hints' not in config:
            raise ValueError("Missing 'business_hints' key in categories.yaml")
        if 'non_business_hints' not in config:
            raise ValueError("Missing 'non_business_hints' key in categories.yaml")

        # Validate each category
        for category in config['categories']:
            required_fields = ['id', 'label_en', 'label_zh', 'description', 'keywords']
            for field in required_fields:
                if field not in category:
                    raise ValueError(f"Category missing required field '{field}': {category}")

        self._categories_cache = config
        return config

    def load_thresholds(self) -> Dict[str, Any]:
        """
        Load thresholds configuration from thresholds.yaml.

        Returns:
            Dictionary containing global, routing_rules, default, retry, and rate_limiting
        """
        if self._thresholds_cache is not None:
            return self._thresholds_cache

        thresholds_file = self.config_dir / "thresholds.yaml"

        if not thresholds_file.exists():
            raise FileNotFoundError(f"Thresholds config not found: {thresholds_file}")

        with open(thresholds_file, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)

        # Validate structure
        required_keys = ['global', 'routing_rules', 'default']
        for key in required_keys:
            if key not in config:
                raise ValueError(f"Missing '{key}' key in thresholds.yaml")

        # Validate global thresholds
        global_config = config['global']
        required_global = ['confidence_threshold', 'auto_send_minimum_confidence', 'business_gate_threshold']
        for field in required_global:
            if field not in global_config:
                raise ValueError(f"Missing '{field}' in global thresholds")
            value = global_config[field]
            if not isinstance(value, (int, float)) or value < 0.0 or value > 1.0:
                raise ValueError(f"Invalid threshold value for '{field}': {value}")

        self._thresholds_cache = config
        return config

    def get_category_list(self) -> List[str]:
        """
        Get list of category IDs.

        Returns:
            List of category ID strings
        """
        config = self.load_categories()
        return [cat['id'] for cat in config['categories']]

    def get_category_labels(self, language: str = 'zh') -> Dict[str, str]:
        """
        Get category labels for specified language.

        Args:
            language: 'en' or 'zh'

        Returns:
            Dictionary mapping category ID to label
        """
        config = self.load_categories()
        label_key = f'label_{language}'

        labels = {}
        for category in config['categories']:
            if label_key in category:
                labels[category['id']] = category[label_key]
            else:
                labels[category['id']] = category['id']

        return labels

    def get_category_keywords(self, category_id: str) -> List[str]:
        """
        Get keywords for a specific category.

        Args:
            category_id: Category identifier

        Returns:
            List of keywords
        """
        config = self.load_categories()

        for category in config['categories']:
            if category['id'] == category_id:
                return category.get('keywords', [])

        return []

    def get_business_hints(self) -> List[str]:
        """
        Get business-related keywords for rule-based filtering.

        Returns:
            List of business keywords
        """
        config = self.load_categories()
        return config.get('business_hints', [])

    def get_non_business_hints(self) -> List[str]:
        """
        Get non-business keywords for rule-based filtering.

        Returns:
            List of non-business keywords
        """
        config = self.load_categories()
        return config.get('non_business_hints', [])

    def get_global_threshold(self, key: str) -> float:
        """
        Get global threshold value.

        Args:
            key: Threshold key (e.g., 'confidence_threshold')

        Returns:
            Threshold value
        """
        config = self.load_thresholds()
        return config['global'].get(key, 0.75)

    def get_routing_rule(self, category_id: str) -> Dict[str, Any]:
        """
        Get routing rule for a specific category.
        Falls back to default rule if category-specific rule not found.

        Args:
            category_id: Category identifier

        Returns:
            Routing rule dictionary
        """
        config = self.load_thresholds()

        # Try category-specific rule first
        if category_id in config['routing_rules']:
            return config['routing_rules'][category_id]

        # Fall back to default
        return config['default']

    def get_retry_config(self) -> Dict[str, Any]:
        """
        Get retry configuration.

        Returns:
            Retry configuration dictionary
        """
        config = self.load_thresholds()
        return config.get('retry', {
            'max_attempts': 3,
            'delay_seconds': 1.0,
            'exponential_backoff': False
        })

    def get_rate_limiting_config(self) -> Dict[str, Any]:
        """
        Get rate limiting configuration.

        Returns:
            Rate limiting configuration dictionary
        """
        config = self.load_thresholds()
        return config.get('rate_limiting', {
            'enabled': False,
            'max_auto_send_per_hour': 100,
            'max_auto_send_per_day': 500
        })

    def reload(self):
        """Clear cache and reload configurations."""
        self._categories_cache = None
        self._thresholds_cache = None
        self._schema_cache = None

    def validate_config(self) -> bool:
        """
        Validate all configuration files.

        Returns:
            True if all configs are valid

        Raises:
            ValueError: If validation fails
        """
        # Load and validate categories
        categories = self.load_categories()

        # Load and validate thresholds
        thresholds = self.load_thresholds()

        # Cross-validate: ensure all routing rules reference valid categories
        category_ids = set(self.get_category_list())
        for rule_id in thresholds['routing_rules'].keys():
            if rule_id not in category_ids:
                raise ValueError(f"Routing rule '{rule_id}' references unknown category")

        return True


# Global instance
_config_loader = None


def get_config_loader() -> ConfigLoader:
    """
    Get global ConfigLoader instance (singleton pattern).

    Returns:
        ConfigLoader instance
    """
    global _config_loader
    if _config_loader is None:
        _config_loader = ConfigLoader()
    return _config_loader

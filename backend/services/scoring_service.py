"""
Rubric-based scoring service.
Provides structured, explainable confidence scores for classification and auto-send decisions.
"""

import json
import yaml
import pickle
from typing import Dict, Any, List, Optional
from pathlib import Path
from openai import OpenAI
from config import Config


class ScoringService:
    """Calculates rubric-based scores for email classification and auto-send readiness."""

    def __init__(self, rubrics_file: Optional[str] = None, calibration_model_path: Optional[str] = None):
        """
        Initialize ScoringService.

        Args:
            rubrics_file: Path to rubrics.yaml file. Defaults to backend/config/rubrics.yaml
            calibration_model_path: Path to calibration model. Defaults to backend/models/calibration_model.pkl
        """
        self.client = OpenAI(api_key=Config.OPENAI_API_KEY, base_url=Config.OPENAI_BASE_URL)
        self.model = Config.OPENAI_MODEL

        backend_dir = Path(__file__).parent.parent

        if rubrics_file is None:
            rubrics_file = backend_dir / "config" / "rubrics.yaml"

        if calibration_model_path is None:
            calibration_model_path = backend_dir / "models" / "calibration_model.pkl"

        self.rubrics_file = Path(rubrics_file)
        self.calibration_model_path = Path(calibration_model_path)
        self._rubrics_cache = None
        self._calibration_model = None
        self._load_calibration_model()

    def _load_rubrics(self) -> Dict[str, Any]:
        """Load rubrics configuration from YAML file."""
        if self._rubrics_cache is not None:
            return self._rubrics_cache

        if not self.rubrics_file.exists():
            raise FileNotFoundError(f"Rubrics config not found: {self.rubrics_file}")

        with open(self.rubrics_file, 'r', encoding='utf-8') as f:
            rubrics = yaml.safe_load(f)

        self._rubrics_cache = rubrics
        return rubrics

    def _load_calibration_model(self):
        """Load calibration model if available."""
        if self.calibration_model_path.exists():
            try:
                with open(self.calibration_model_path, 'rb') as f:
                    self._calibration_model = pickle.load(f)
                print(f"Loaded calibration model from {self.calibration_model_path}")
            except Exception as e:
                print(f"Warning: Failed to load calibration model: {e}")
                self._calibration_model = None
        else:
            self._calibration_model = None

    def calibrate_confidence(self, raw_confidence: float) -> float:
        """
        Calibrate raw confidence score using trained calibration model.

        Args:
            raw_confidence: Raw confidence score (0.0-1.0)

        Returns:
            Calibrated confidence score (0.0-1.0)
        """
        if self._calibration_model is None:
            # No calibration model available, return raw confidence
            return raw_confidence

        try:
            import numpy as np
            # Reshape for sklearn predict
            confidence_array = np.array([[raw_confidence]])
            calibrated = self._calibration_model.predict(confidence_array)[0]
            # Ensure output is in valid range
            return max(0.0, min(1.0, float(calibrated)))
        except Exception as e:
            print(f"Warning: Calibration failed: {e}")
            return raw_confidence

    def _calculate_weighted_score(self, scores: Dict[str, int], dimensions: List[Dict]) -> float:
        """
        Calculate weighted score from dimension scores.

        Args:
            scores: Dictionary mapping dimension name to score (0-3)
            dimensions: List of dimension definitions with weights

        Returns:
            Weighted score (0.0-3.0)
        """
        total_weight = sum(dim['weight'] for dim in dimensions)
        weighted_sum = 0.0

        for dim in dimensions:
            dim_name = dim['name']
            dim_weight = dim['weight']
            dim_score = scores.get(dim_name, 0)

            weighted_sum += dim_score * dim_weight

        return weighted_sum / total_weight if total_weight > 0 else 0.0

    def _score_to_confidence(self, weighted_score: float, apply_calibration: bool = True) -> float:
        """
        Convert weighted score (0.0-3.0) to confidence (0.0-1.0).

        Args:
            weighted_score: Weighted score from rubric
            apply_calibration: Whether to apply calibration model

        Returns:
            Confidence value (0.0-1.0)
        """
        raw_confidence = weighted_score / 3.0
        raw_confidence = max(0.0, min(1.0, raw_confidence))

        if apply_calibration:
            return self.calibrate_confidence(raw_confidence)
        else:
            return raw_confidence

    def score_classification(
        self,
        subject: str,
        body: str,
        category: str,
        use_llm: bool = True,
        apply_calibration: bool = True
    ) -> Dict[str, Any]:
        """
        Score email classification quality using classification rubric.

        Args:
            subject: Email subject
            body: Email body
            category: Proposed category
            use_llm: Whether to use LLM for scoring (default True)
            apply_calibration: Whether to apply confidence calibration (default True)

        Returns:
            Dictionary with scores, weighted_score, confidence, and reasoning
        """
        rubrics = self._load_rubrics()
        classification_rubric = rubrics['classification_rubric']
        dimensions = classification_rubric['dimensions']

        if use_llm:
            # Use LLM to score against rubric
            result = self._llm_score_classification(subject, body, category, classification_rubric)
        else:
            # Rule-based scoring (fallback)
            result = self._rule_based_score_classification(subject, body, category, dimensions)

        # Apply calibration to confidence
        if apply_calibration and 'confidence' in result:
            raw_confidence = result['confidence']
            calibrated_confidence = self.calibrate_confidence(raw_confidence)
            result['raw_confidence'] = raw_confidence
            result['confidence'] = round(calibrated_confidence, 2)
            result['calibrated'] = True
        else:
            result['calibrated'] = False

        return result

    def _llm_score_classification(
        self,
        subject: str,
        body: str,
        category: str,
        rubric: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Use LLM to score classification against rubric."""
        rubrics_config = self._load_rubrics()
        prompts = rubrics_config.get('prompts', {})
        classification_prompt = prompts.get('classification_scoring', {})

        system_prompt = classification_prompt.get('system', '')
        user_template = classification_prompt.get('user_template', '')

        # Format rubric as YAML for prompt
        rubric_yaml = yaml.dump(rubric, allow_unicode=True, default_flow_style=False)

        # Format user prompt
        user_content = user_template.format(
            subject=subject,
            body=body[:3000],
            category=category,
            rubric_yaml=rubric_yaml
        )

        # Call LLM
        response = self.client.chat.completions.create(
            model=self.model,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content}
            ],
            temperature=0.1,
            max_tokens=500
        )

        result = json.loads(response.choices[0].message.content)

        # Validate and normalize scores
        scores = result.get('scores', {})
        for dim_name, dim_data in scores.items():
            if isinstance(dim_data, dict):
                score = dim_data.get('score', 0)
            else:
                score = dim_data
            scores[dim_name] = max(0, min(3, int(score)))

        # Recalculate weighted score to ensure consistency
        dimensions = rubric['dimensions']
        weighted_score = self._calculate_weighted_score(scores, dimensions)
        confidence = self._score_to_confidence(weighted_score, apply_calibration=False)

        return {
            'scores': result.get('scores', {}),
            'weighted_score': round(weighted_score, 2),
            'confidence': round(confidence, 2),
            'rubric_version': rubric.get('version', '1.0')
        }

    def _rule_based_score_classification(
        self,
        subject: str,
        body: str,
        category: str,
        dimensions: List[Dict]
    ) -> Dict[str, Any]:
        """Rule-based scoring (fallback when LLM unavailable)."""
        from services.config_loader import get_config_loader

        config_loader = get_config_loader()
        keywords = config_loader.get_category_keywords(category)

        text = f"{subject}\n{body}".lower()

        # Simple rule-based scoring
        scores = {}

        # Keyword match
        keyword_count = sum(1 for kw in keywords if kw.lower() in text)
        if keyword_count >= 3:
            scores['keyword_match'] = 3
        elif keyword_count >= 2:
            scores['keyword_match'] = 2
        elif keyword_count >= 1:
            scores['keyword_match'] = 1
        else:
            scores['keyword_match'] = 0

        # Intent clarity (based on length and structure)
        word_count = len(body.split())
        if word_count >= 50 and '?' in text:
            scores['intent_clarity'] = 3
        elif word_count >= 20:
            scores['intent_clarity'] = 2
        elif word_count >= 5:
            scores['intent_clarity'] = 1
        else:
            scores['intent_clarity'] = 0

        # Context completeness (based on length)
        if word_count >= 100:
            scores['context_completeness'] = 3
        elif word_count >= 50:
            scores['context_completeness'] = 2
        elif word_count >= 10:
            scores['context_completeness'] = 1
        else:
            scores['context_completeness'] = 0

        # Exclusion confidence (default to moderate)
        scores['exclusion_confidence'] = 2

        weighted_score = self._calculate_weighted_score(scores, dimensions)
        confidence = self._score_to_confidence(weighted_score, apply_calibration=False)

        return {
            'scores': {
                dim: {'score': scores[dim], 'reasoning': 'Rule-based scoring'}
                for dim in scores
            },
            'weighted_score': round(weighted_score, 2),
            'confidence': round(confidence, 2),
            'rubric_version': '1.0',
            'method': 'rule_based'
        }

    def score_auto_send_readiness(
        self,
        subject: str,
        body: str,
        reply_text: str,
        category: str,
        use_llm: bool = True,
        apply_calibration: bool = True
    ) -> Dict[str, Any]:
        """
        Score auto-send readiness using auto-send rubric.

        Args:
            subject: Original email subject
            body: Original email body
            reply_text: Generated reply text
            category: Email category
            use_llm: Whether to use LLM for scoring (default True)
            apply_calibration: Whether to apply confidence calibration (default True)

        Returns:
            Dictionary with scores, weighted_score, confidence, auto_send_recommended
        """
        rubrics = self._load_rubrics()
        auto_send_rubric = rubrics['auto_send_rubric']
        dimensions = auto_send_rubric['dimensions']
        thresholds = auto_send_rubric['thresholds']

        if use_llm:
            # Use LLM to score against rubric
            result = self._llm_score_auto_send(subject, body, reply_text, category, auto_send_rubric)
        else:
            # Rule-based scoring (fallback)
            result = self._rule_based_score_auto_send(reply_text, dimensions)

        # Apply calibration to confidence
        if apply_calibration and 'confidence' in result:
            raw_confidence = result['confidence']
            calibrated_confidence = self.calibrate_confidence(raw_confidence)
            result['raw_confidence'] = raw_confidence
            result['confidence'] = round(calibrated_confidence, 2)
            result['calibrated'] = True
        else:
            result['calibrated'] = False

        # Apply auto-send decision logic
        weighted_score = result['weighted_score']
        scores = result.get('scores', {})

        # Check thresholds
        auto_send_minimum = thresholds.get('auto_send_minimum', 2.5)
        require_all_above = thresholds.get('require_all_above', 1)

        # Check if any dimension is below minimum
        any_below_minimum = False
        for dim_name, dim_data in scores.items():
            if isinstance(dim_data, dict):
                score = dim_data.get('score', 0)
            else:
                score = dim_data
            if score <= require_all_above:
                any_below_minimum = True
                break

        # Auto-send recommended if weighted score >= minimum AND no dimension below threshold
        auto_send_recommended = (
            weighted_score >= auto_send_minimum and
            not any_below_minimum
        )

        result['auto_send_recommended'] = auto_send_recommended
        result['thresholds_applied'] = {
            'auto_send_minimum': auto_send_minimum,
            'require_all_above': require_all_above
        }

        return result

    def _llm_score_auto_send(
        self,
        subject: str,
        body: str,
        reply_text: str,
        category: str,
        rubric: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Use LLM to score auto-send readiness against rubric."""
        rubrics_config = self._load_rubrics()
        prompts = rubrics_config.get('prompts', {})
        auto_send_prompt = prompts.get('auto_send_scoring', {})

        system_prompt = auto_send_prompt.get('system', '')
        user_template = auto_send_prompt.get('user_template', '')

        # Format rubric as YAML for prompt
        rubric_yaml = yaml.dump(rubric, allow_unicode=True, default_flow_style=False)

        # Format user prompt
        user_content = user_template.format(
            subject=subject,
            body=body[:3000],
            reply_text=reply_text,
            category=category,
            rubric_yaml=rubric_yaml
        )

        # Call LLM
        response = self.client.chat.completions.create(
            model=self.model,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content}
            ],
            temperature=0.1,
            max_tokens=600
        )

        result = json.loads(response.choices[0].message.content)

        # Validate and normalize scores
        scores = result.get('scores', {})
        for dim_name, dim_data in scores.items():
            if isinstance(dim_data, dict):
                score = dim_data.get('score', 0)
            else:
                score = dim_data
            scores[dim_name] = max(0, min(3, int(score)))

        # Recalculate weighted score
        dimensions = rubric['dimensions']
        weighted_score = self._calculate_weighted_score(scores, dimensions)
        confidence = self._score_to_confidence(weighted_score, apply_calibration=False)

        return {
            'scores': result.get('scores', {}),
            'weighted_score': round(weighted_score, 2),
            'confidence': round(confidence, 2),
            'rubric_version': rubric.get('version', '1.0')
        }

    def _rule_based_score_auto_send(
        self,
        reply_text: str,
        dimensions: List[Dict]
    ) -> Dict[str, Any]:
        """Rule-based auto-send scoring (fallback)."""
        scores = {}

        # Simple heuristics
        reply_length = len(reply_text)

        # Information completeness (based on reply length)
        if reply_length >= 200:
            scores['information_completeness'] = 3
        elif reply_length >= 100:
            scores['information_completeness'] = 2
        elif reply_length >= 50:
            scores['information_completeness'] = 1
        else:
            scores['information_completeness'] = 0

        # Risk level (default to low-medium)
        scores['risk_level'] = 2

        # Template applicability (default to good fit)
        scores['template_applicability'] = 2

        # Policy alignment (default to compliant)
        scores['policy_alignment'] = 3

        weighted_score = self._calculate_weighted_score(scores, dimensions)
        confidence = self._score_to_confidence(weighted_score, apply_calibration=False)

        return {
            'scores': {
                dim: {'score': scores[dim], 'reasoning': 'Rule-based scoring'}
                for dim in scores
            },
            'weighted_score': round(weighted_score, 2),
            'confidence': round(confidence, 2),
            'rubric_version': '1.0',
            'method': 'rule_based'
        }

    def reload(self):
        """Clear cache and reload rubrics and calibration model."""
        self._rubrics_cache = None
        self._load_calibration_model()


# Global instance
_scoring_service = None


def get_scoring_service() -> ScoringService:
    """
    Get global ScoringService instance (singleton pattern).

    Returns:
        ScoringService instance
    """
    global _scoring_service
    if _scoring_service is None:
        _scoring_service = ScoringService()
    return _scoring_service

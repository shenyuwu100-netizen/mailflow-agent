"""
Validation Service

Validates reply quality before auto-send by checking:
- Factual accuracy (hallucination detection)
- Tone appropriateness
- Completeness
- Policy compliance

Provides detailed validation reports with visual quality indicators.
"""

import json
import yaml
import re
from typing import Dict, Any, List, Optional, Tuple
from pathlib import Path
from openai import OpenAI
from config import Config


class ValidationService:
    """Multi-stage validation service for reply quality assurance."""

    def __init__(
        self,
        rubrics_file: Optional[str] = None,
        policies_file: Optional[str] = None
    ):
        """
        Initialize ValidationService.

        Args:
            rubrics_file: Path to rubrics.yaml
            policies_file: Path to policies.yaml
        """
        self.client = OpenAI(api_key=Config.OPENAI_API_KEY, base_url=Config.OPENAI_BASE_URL)
        self.model = Config.OPENAI_MODEL

        backend_dir = Path(__file__).parent.parent

        if rubrics_file is None:
            rubrics_file = backend_dir / "config" / "rubrics.yaml"
        if policies_file is None:
            policies_file = backend_dir / "config" / "policies.yaml"

        self.rubrics_file = Path(rubrics_file)
        self.policies_file = Path(policies_file)

        self._rubrics_cache = None
        self._policies_cache = None

    def _load_rubrics(self) -> Dict[str, Any]:
        """Load rubrics configuration."""
        if self._rubrics_cache is not None:
            return self._rubrics_cache

        if not self.rubrics_file.exists():
            raise FileNotFoundError(f"Rubrics config not found: {self.rubrics_file}")

        with open(self.rubrics_file, 'r', encoding='utf-8') as f:
            self._rubrics_cache = yaml.safe_load(f)

        return self._rubrics_cache

    def _load_policies(self) -> Dict[str, Any]:
        """Load policies configuration."""
        if self._policies_cache is not None:
            return self._policies_cache

        if not self.policies_file.exists():
            raise FileNotFoundError(f"Policies config not found: {self.policies_file}")

        with open(self.policies_file, 'r', encoding='utf-8') as f:
            self._policies_cache = yaml.safe_load(f)

        return self._policies_cache

    def validate_reply_quality(
        self,
        reply_text: str,
        email_context: Dict[str, Any],
        category: str,
        company_info: Optional[Dict] = None,
        use_llm: bool = True
    ) -> Dict[str, Any]:
        """
        Validate reply quality using multi-stage validation pipeline.

        Args:
            reply_text: Generated reply text
            email_context: Original email context (subject, body)
            category: Email category
            company_info: Company information for fact-checking
            use_llm: Whether to use LLM for validation

        Returns:
            Validation report with scores, issues, and pass/fail status
        """
        # Stage 1: Policy compliance check (fast, rule-based)
        policy_result = self.check_policy_compliance(reply_text, category)

        # Stage 2: Hallucination detection (fact-checking)
        hallucination_result = self.detect_hallucinations(
            reply_text,
            company_info or {}
        )

        # Stage 3: LLM-based quality validation (comprehensive)
        if use_llm:
            quality_result = self._llm_validate_quality(
                reply_text,
                email_context,
                category,
                company_info or {}
            )
        else:
            quality_result = self._rule_based_validate_quality(
                reply_text,
                email_context
            )

        # Combine results
        validation_report = self._combine_validation_results(
            policy_result,
            hallucination_result,
            quality_result
        )

        # Generate visual report
        validation_report['visual_report'] = self._generate_visual_report(
            validation_report
        )

        return validation_report

    def check_policy_compliance(
        self,
        reply_text: str,
        category: str
    ) -> Dict[str, Any]:
        """
        Check reply against company policies (rule-based).

        Args:
            reply_text: Reply text to check
            category: Email category

        Returns:
            Policy compliance report
        """
        policies = self._load_policies()
        forbidden_patterns = policies.get('forbidden_patterns', {})

        violations = []
        warnings = []

        # Check forbidden patterns
        for pattern_group, config in forbidden_patterns.items():
            severity = config.get('severity', 'warning')
            patterns = config.get('patterns', [])

            for pattern_def in patterns:
                pattern = pattern_def.get('pattern', '')
                is_regex = pattern_def.get('regex', False)
                case_sensitive = pattern_def.get('case_sensitive', False)
                reason = pattern_def.get('reason', 'Policy violation')

                # Check if pattern matches
                if is_regex:
                    flags = 0 if case_sensitive else re.IGNORECASE
                    matches = re.findall(pattern, reply_text, flags)
                else:
                    if case_sensitive:
                        matches = [pattern] if pattern in reply_text else []
                    else:
                        matches = [pattern] if pattern.lower() in reply_text.lower() else []

                if matches:
                    issue = {
                        'type': pattern_group,
                        'severity': severity,
                        'pattern': pattern,
                        'matches': matches[:3],  # Limit to first 3 matches
                        'reason': reason
                    }

                    if severity == 'blocking':
                        violations.append(issue)
                    else:
                        warnings.append(issue)

        # Check category-specific policies
        category_policies = policies.get('category_policies', {})
        if category in category_policies:
            cat_policy = category_policies[category]
            # Add category-specific checks here if needed

        passed = len(violations) == 0
        score = 3 if passed and len(warnings) == 0 else (2 if passed else 0)

        return {
            'passed': passed,
            'score': score,
            'violations': violations,
            'warnings': warnings,
            'total_issues': len(violations) + len(warnings)
        }

    def detect_hallucinations(
        self,
        reply_text: str,
        company_info: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Detect hallucinations by fact-checking against company information.

        Args:
            reply_text: Reply text to check
            company_info: Company information database

        Returns:
            Hallucination detection report
        """
        hallucinations = []
        warnings = []

        # Extract potential factual claims
        claims = self._extract_factual_claims(reply_text)

        # Check each claim against company info
        for claim in claims:
            verification = self._verify_claim(claim, company_info)

            if verification['status'] == 'false':
                hallucinations.append({
                    'claim': claim,
                    'reason': verification['reason'],
                    'severity': 'blocking'
                })
            elif verification['status'] == 'unverified':
                warnings.append({
                    'claim': claim,
                    'reason': verification['reason'],
                    'severity': 'warning'
                })

        passed = len(hallucinations) == 0
        score = 3 if passed and len(warnings) == 0 else (2 if passed else 0)

        return {
            'passed': passed,
            'score': score,
            'hallucinations': hallucinations,
            'warnings': warnings,
            'total_claims_checked': len(claims)
        }

    def _extract_factual_claims(self, text: str) -> List[str]:
        """Extract potential factual claims from text."""
        claims = []

        # Price claims
        price_patterns = [
            r'\$\d+\.?\d*',
            r'\d+\s*(CNY|RMB|yuan)',
            r'costs?\s+\d+',
            r'price\s+is\s+\d+'
        ]
        for pattern in price_patterns:
            matches = re.findall(pattern, text, re.IGNORECASE)
            claims.extend([f"Price claim: {m}" for m in matches])

        # Product claims
        product_patterns = [
            r'(product|item|model)\s+\w+\s+(has|includes|features)',
            r'available\s+in\s+\w+',
            r'comes\s+with\s+\w+'
        ]
        for pattern in product_patterns:
            matches = re.findall(pattern, text, re.IGNORECASE)
            claims.extend([f"Product claim: {m}" for m in matches])

        # Policy claims
        policy_patterns = [
            r'(refund|return)\s+policy\s+(is|states|allows)',
            r'we\s+(guarantee|promise|ensure)',
            r'within\s+\d+\s+days'
        ]
        for pattern in policy_patterns:
            matches = re.findall(pattern, text, re.IGNORECASE)
            claims.extend([f"Policy claim: {m}" for m in matches])

        return claims

    def _verify_claim(
        self,
        claim: str,
        company_info: Dict[str, Any]
    ) -> Dict[str, str]:
        """
        Verify a claim against company information.

        Args:
            claim: Claim to verify
            company_info: Company information

        Returns:
            Verification result (status: 'true', 'false', 'unverified')
        """
        # Simple verification logic
        # In production, this would be more sophisticated

        if 'Price claim' in claim:
            # Check if price is in company info
            # For now, mark as unverified (needs price list check)
            return {
                'status': 'unverified',
                'reason': 'Price not verified against price list'
            }

        if 'Product claim' in claim:
            # Check if product exists in catalog
            products = company_info.get('products', [])
            # Simplified check
            return {
                'status': 'unverified',
                'reason': 'Product details not verified against catalog'
            }

        if 'Policy claim' in claim:
            # Check against policies
            return {
                'status': 'unverified',
                'reason': 'Policy statement not verified'
            }

        return {
            'status': 'true',
            'reason': 'No verification needed'
        }

    def _llm_validate_quality(
        self,
        reply_text: str,
        email_context: Dict[str, Any],
        category: str,
        company_info: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Use LLM to validate reply quality against rubric."""
        rubrics = self._load_rubrics()
        quality_rubric = rubrics.get('reply_quality_rubric', {})
        prompts = rubrics.get('prompts', {})
        validation_prompt = prompts.get('reply_quality_validation', {})

        system_prompt = validation_prompt.get('system', '')
        user_template = validation_prompt.get('user_template', '')

        # Format rubric and company info
        rubric_yaml = yaml.dump(quality_rubric, allow_unicode=True, default_flow_style=False)
        company_info_str = json.dumps(company_info, indent=2, ensure_ascii=False)[:1000]

        # Format user prompt
        user_content = user_template.format(
            subject=email_context.get('subject', ''),
            body=email_context.get('body', '')[:2000],
            reply_text=reply_text,
            category=category,
            company_info=company_info_str,
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
            max_tokens=800
        )

        result = json.loads(response.choices[0].message.content)

        # Calculate weighted score
        scores = result.get('scores', {})
        dimensions = quality_rubric.get('dimensions', [])
        weighted_score = self._calculate_weighted_score(scores, dimensions)

        result['weighted_score'] = round(weighted_score, 2)
        result['quality_score'] = round(weighted_score / 3.0, 2)

        return result

    def _rule_based_validate_quality(
        self,
        reply_text: str,
        email_context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Rule-based quality validation (fallback)."""
        scores = {}

        # Simple heuristics
        reply_length = len(reply_text)
        body_length = len(email_context.get('body', ''))

        # Completeness (based on length ratio)
        if reply_length >= body_length * 0.5:
            scores['completeness'] = 3
        elif reply_length >= body_length * 0.3:
            scores['completeness'] = 2
        else:
            scores['completeness'] = 1

        # Tone (check for negative words)
        negative_words = ['stupid', 'dumb', 'not our problem', 'too bad']
        has_negative = any(word in reply_text.lower() for word in negative_words)
        scores['tone_appropriateness'] = 0 if has_negative else 3

        # Factual accuracy (default to uncertain)
        scores['factual_accuracy'] = 2

        # Policy compliance (default to compliant)
        scores['policy_compliance'] = 3

        weighted_score = sum(scores.values()) / len(scores)

        return {
            'scores': scores,
            'weighted_score': round(weighted_score, 2),
            'quality_score': round(weighted_score / 3.0, 2),
            'passed': weighted_score >= 2.0,
            'method': 'rule_based'
        }

    def _calculate_weighted_score(
        self,
        scores: Dict[str, Any],
        dimensions: List[Dict]
    ) -> float:
        """Calculate weighted score from dimension scores."""
        total_weight = sum(dim['weight'] for dim in dimensions)
        weighted_sum = 0.0

        for dim in dimensions:
            dim_name = dim['name']
            dim_weight = dim['weight']

            score_data = scores.get(dim_name, {})
            if isinstance(score_data, dict):
                dim_score = score_data.get('score', 0)
            else:
                dim_score = score_data

            weighted_sum += dim_score * dim_weight

        return weighted_sum / total_weight if total_weight > 0 else 0.0

    def _combine_validation_results(
        self,
        policy_result: Dict,
        hallucination_result: Dict,
        quality_result: Dict
    ) -> Dict[str, Any]:
        """Combine results from all validation stages."""
        # Determine overall pass/fail
        passed = (
            policy_result['passed'] and
            hallucination_result['passed'] and
            quality_result.get('passed', True)
        )

        # Collect all blocking issues
        blocking_issues = []
        blocking_issues.extend(policy_result.get('violations', []))
        blocking_issues.extend(hallucination_result.get('hallucinations', []))

        # Collect all warnings
        warnings = []
        warnings.extend(policy_result.get('warnings', []))
        warnings.extend(hallucination_result.get('warnings', []))

        # Add quality issues
        quality_scores = quality_result.get('scores', {})
        for dim_name, dim_data in quality_scores.items():
            if isinstance(dim_data, dict):
                score = dim_data.get('score', 0)
                issues = dim_data.get('issues', [])
                if score <= 1:
                    blocking_issues.extend(issues)
                elif score == 2:
                    warnings.extend(issues)

        return {
            'passed': passed,
            'overall_quality_score': quality_result.get('quality_score', 0),
            'policy_compliance': policy_result,
            'hallucination_detection': hallucination_result,
            'quality_validation': quality_result,
            'blocking_issues': blocking_issues,
            'warnings': warnings,
            'total_issues': len(blocking_issues) + len(warnings),
            'recommendation': 'AUTO_SEND' if passed else 'MANUAL_REVIEW'
        }

    def _generate_visual_report(self, validation_report: Dict) -> str:
        """
        Generate visual validation report with quality indicators.

        Args:
            validation_report: Validation report data

        Returns:
            Formatted visual report string
        """
        passed = validation_report['passed']
        quality_score = validation_report['overall_quality_score']
        blocking_issues = validation_report['blocking_issues']
        warnings = validation_report['warnings']

        # Status indicator
        status_icon = "✅" if passed else "❌"
        status_text = "PASSED" if passed else "FAILED"

        # Quality bar
        quality_bar = self._generate_quality_bar(quality_score)

        # Build report
        lines = []
        lines.append("=" * 60)
        lines.append(f"  REPLY QUALITY VALIDATION REPORT")
        lines.append("=" * 60)
        lines.append("")
        lines.append(f"Status: {status_icon} {status_text}")
        lines.append(f"Overall Quality Score: {quality_score:.2f} / 1.00")
        lines.append(f"Quality: {quality_bar}")
        lines.append("")

        # Dimension scores
        lines.append("Dimension Scores:")
        lines.append("-" * 60)

        quality_validation = validation_report.get('quality_validation', {})
        scores = quality_validation.get('scores', {})

        for dim_name, dim_data in scores.items():
            if isinstance(dim_data, dict):
                score = dim_data.get('score', 0)
                reasoning = dim_data.get('reasoning', '')
            else:
                score = dim_data
                reasoning = ''

            score_bar = self._generate_score_bar(score, 3)
            lines.append(f"  {dim_name.replace('_', ' ').title()}: {score_bar} ({score}/3)")
            if reasoning:
                lines.append(f"    → {reasoning}")

        lines.append("")

        # Policy compliance
        policy_result = validation_report.get('policy_compliance', {})
        policy_score = policy_result.get('score', 0)
        policy_icon = "✅" if policy_result.get('passed', False) else "❌"
        lines.append(f"Policy Compliance: {policy_icon} ({policy_score}/3)")

        # Hallucination detection
        hallucination_result = validation_report.get('hallucination_detection', {})
        hallucination_score = hallucination_result.get('score', 0)
        hallucination_icon = "✅" if hallucination_result.get('passed', False) else "❌"
        lines.append(f"Hallucination Check: {hallucination_icon} ({hallucination_score}/3)")
        lines.append("")

        # Issues
        if blocking_issues:
            lines.append("🚫 BLOCKING ISSUES:")
            lines.append("-" * 60)
            for i, issue in enumerate(blocking_issues[:5], 1):
                issue_type = issue.get('type', 'Unknown')
                reason = issue.get('reason', 'No reason provided')
                lines.append(f"  {i}. [{issue_type}] {reason}")
            if len(blocking_issues) > 5:
                lines.append(f"  ... and {len(blocking_issues) - 5} more issues")
            lines.append("")

        if warnings:
            lines.append("⚠️  WARNINGS:")
            lines.append("-" * 60)
            for i, warning in enumerate(warnings[:5], 1):
                warning_type = warning.get('type', 'Unknown')
                reason = warning.get('reason', 'No reason provided')
                lines.append(f"  {i}. [{warning_type}] {reason}")
            if len(warnings) > 5:
                lines.append(f"  ... and {len(warnings) - 5} more warnings")
            lines.append("")

        # Recommendation
        recommendation = validation_report['recommendation']
        rec_icon = "🚀" if recommendation == 'AUTO_SEND' else "👤"
        lines.append(f"Recommendation: {rec_icon} {recommendation}")
        lines.append("=" * 60)

        return "\n".join(lines)

    def _generate_quality_bar(self, score: float, max_score: float = 1.0) -> str:
        """Generate visual quality bar."""
        percentage = score / max_score
        filled = int(percentage * 20)
        empty = 20 - filled

        if percentage >= 0.9:
            color = "🟩"
        elif percentage >= 0.75:
            color = "🟨"
        elif percentage >= 0.5:
            color = "🟧"
        else:
            color = "🟥"

        bar = color * filled + "⬜" * empty
        return f"{bar} {percentage:.0%}"

    def _generate_score_bar(self, score: int, max_score: int = 3) -> str:
        """Generate visual score bar."""
        filled = score
        empty = max_score - score

        if score == max_score:
            symbol = "🟢"
        elif score >= max_score * 0.66:
            symbol = "🟡"
        elif score >= max_score * 0.33:
            symbol = "🟠"
        else:
            symbol = "🔴"

        return symbol * filled + "⚪" * empty

    def reload(self):
        """Clear cache and reload configurations."""
        self._rubrics_cache = None
        self._policies_cache = None


# Global instance
_validation_service = None


def get_validation_service() -> ValidationService:
    """Get global ValidationService instance (singleton pattern)."""
    global _validation_service
    if _validation_service is None:
        _validation_service = ValidationService()
    return _validation_service

"""
Validation Report Generator

Generates detailed validation reports with visual quality indicators,
issue highlighting, and quality score breakdowns for demonstration purposes.
"""

import json
from typing import Dict, Any, List
from pathlib import Path
from datetime import datetime


class ValidationReportGenerator:
    """Generates comprehensive validation reports with visualizations."""

    def __init__(self):
        """Initialize ValidationReportGenerator."""
        pass

    def generate_html_report(
        self,
        validation_result: Dict[str, Any],
        reply_text: str,
        email_context: Dict[str, Any],
        output_path: str = None
    ) -> str:
        """
        Generate HTML validation report with visual indicators.

        Args:
            validation_result: Validation result from ValidationService
            reply_text: The reply text that was validated
            email_context: Original email context
            output_path: Optional path to save HTML file

        Returns:
            HTML report string
        """
        passed = validation_result.get('passed', False)
        quality_score = validation_result.get('overall_quality_score', 0)
        blocking_issues = validation_result.get('blocking_issues', [])
        warnings = validation_result.get('warnings', [])
        recommendation = validation_result.get('recommendation', 'MANUAL_REVIEW')

        # Build HTML
        html = self._build_html_template(
            passed=passed,
            quality_score=quality_score,
            validation_result=validation_result,
            reply_text=reply_text,
            email_context=email_context,
            blocking_issues=blocking_issues,
            warnings=warnings,
            recommendation=recommendation
        )

        # Save to file if path provided
        if output_path:
            output_path = Path(output_path)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(html)

        return html

    def _build_html_template(
        self,
        passed: bool,
        quality_score: float,
        validation_result: Dict,
        reply_text: str,
        email_context: Dict,
        blocking_issues: List,
        warnings: List,
        recommendation: str
    ) -> str:
        """Build HTML template for validation report."""

        status_color = "#28a745" if passed else "#dc3545"
        status_text = "PASSED ✓" if passed else "FAILED ✗"

        quality_color = self._get_quality_color(quality_score)
        quality_percentage = int(quality_score * 100)

        html = f"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Reply Quality Validation Report</title>
    <style>
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}

        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            padding: 20px;
            min-height: 100vh;
        }}

        .container {{
            max-width: 1200px;
            margin: 0 auto;
            background: white;
            border-radius: 12px;
            box-shadow: 0 10px 40px rgba(0,0,0,0.2);
            overflow: hidden;
        }}

        .header {{
            background: {status_color};
            color: white;
            padding: 30px;
            text-align: center;
        }}

        .header h1 {{
            font-size: 28px;
            margin-bottom: 10px;
        }}

        .header .status {{
            font-size: 20px;
            font-weight: bold;
        }}

        .content {{
            padding: 30px;
        }}

        .section {{
            margin-bottom: 30px;
        }}

        .section-title {{
            font-size: 20px;
            font-weight: bold;
            color: #333;
            margin-bottom: 15px;
            padding-bottom: 10px;
            border-bottom: 2px solid #e0e0e0;
        }}

        .quality-score {{
            text-align: center;
            padding: 20px;
            background: #f8f9fa;
            border-radius: 8px;
            margin-bottom: 20px;
        }}

        .quality-score .score {{
            font-size: 48px;
            font-weight: bold;
            color: {quality_color};
        }}

        .quality-score .label {{
            font-size: 16px;
            color: #666;
            margin-top: 5px;
        }}

        .progress-bar {{
            width: 100%;
            height: 30px;
            background: #e0e0e0;
            border-radius: 15px;
            overflow: hidden;
            margin-top: 10px;
        }}

        .progress-fill {{
            height: 100%;
            background: {quality_color};
            width: {quality_percentage}%;
            transition: width 0.3s ease;
            display: flex;
            align-items: center;
            justify-content: center;
            color: white;
            font-weight: bold;
        }}

        .dimension-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
            gap: 20px;
            margin-top: 20px;
        }}

        .dimension-card {{
            background: #f8f9fa;
            border-radius: 8px;
            padding: 20px;
            border-left: 4px solid #667eea;
        }}

        .dimension-card .name {{
            font-size: 16px;
            font-weight: bold;
            color: #333;
            margin-bottom: 10px;
        }}

        .dimension-card .score {{
            font-size: 24px;
            font-weight: bold;
            margin-bottom: 5px;
        }}

        .dimension-card .reasoning {{
            font-size: 14px;
            color: #666;
            line-height: 1.5;
        }}

        .score-excellent {{ color: #28a745; }}
        .score-good {{ color: #ffc107; }}
        .score-fair {{ color: #fd7e14; }}
        .score-poor {{ color: #dc3545; }}

        .issues-list {{
            list-style: none;
        }}

        .issue-item {{
            background: #fff3cd;
            border-left: 4px solid #ffc107;
            padding: 15px;
            margin-bottom: 10px;
            border-radius: 4px;
        }}

        .issue-item.blocking {{
            background: #f8d7da;
            border-left-color: #dc3545;
        }}

        .issue-item .type {{
            font-weight: bold;
            color: #333;
            margin-bottom: 5px;
        }}

        .issue-item .reason {{
            color: #666;
            font-size: 14px;
        }}

        .recommendation {{
            background: {status_color};
            color: white;
            padding: 20px;
            border-radius: 8px;
            text-align: center;
            font-size: 18px;
            font-weight: bold;
        }}

        .email-preview {{
            background: #f8f9fa;
            border: 1px solid #dee2e6;
            border-radius: 8px;
            padding: 20px;
            margin-top: 15px;
        }}

        .email-preview .label {{
            font-weight: bold;
            color: #666;
            margin-bottom: 5px;
        }}

        .email-preview .text {{
            color: #333;
            line-height: 1.6;
            white-space: pre-wrap;
        }}

        .stats-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 15px;
            margin-top: 20px;
        }}

        .stat-card {{
            background: #f8f9fa;
            padding: 15px;
            border-radius: 8px;
            text-align: center;
        }}

        .stat-card .value {{
            font-size: 32px;
            font-weight: bold;
            color: #667eea;
        }}

        .stat-card .label {{
            font-size: 14px;
            color: #666;
            margin-top: 5px;
        }}

        .timestamp {{
            text-align: center;
            color: #999;
            font-size: 12px;
            margin-top: 20px;
            padding-top: 20px;
            border-top: 1px solid #e0e0e0;
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>Reply Quality Validation Report</h1>
            <div class="status">{status_text}</div>
        </div>

        <div class="content">
            <!-- Overall Quality Score -->
            <div class="section">
                <div class="quality-score">
                    <div class="score">{quality_score:.2f}</div>
                    <div class="label">Overall Quality Score (0.00 - 1.00)</div>
                    <div class="progress-bar">
                        <div class="progress-fill">{quality_percentage}%</div>
                    </div>
                </div>
            </div>

            <!-- Statistics -->
            <div class="section">
                <div class="section-title">Validation Statistics</div>
                <div class="stats-grid">
                    <div class="stat-card">
                        <div class="value">{len(blocking_issues)}</div>
                        <div class="label">Blocking Issues</div>
                    </div>
                    <div class="stat-card">
                        <div class="value">{len(warnings)}</div>
                        <div class="label">Warnings</div>
                    </div>
                    <div class="stat-card">
                        <div class="value">{validation_result.get('policy_compliance', {}).get('score', 0)}/3</div>
                        <div class="label">Policy Compliance</div>
                    </div>
                    <div class="stat-card">
                        <div class="value">{validation_result.get('hallucination_detection', {}).get('score', 0)}/3</div>
                        <div class="label">Factual Accuracy</div>
                    </div>
                </div>
            </div>

            <!-- Dimension Scores -->
            <div class="section">
                <div class="section-title">Quality Dimensions</div>
                <div class="dimension-grid">
                    {self._build_dimension_cards(validation_result)}
                </div>
            </div>

            <!-- Blocking Issues -->
            {self._build_issues_section(blocking_issues, "Blocking Issues", "blocking")}

            <!-- Warnings -->
            {self._build_issues_section(warnings, "Warnings", "warning")}

            <!-- Email Context -->
            <div class="section">
                <div class="section-title">Email Context</div>
                <div class="email-preview">
                    <div class="label">Subject:</div>
                    <div class="text">{email_context.get('subject', 'N/A')}</div>
                </div>
                <div class="email-preview">
                    <div class="label">Body:</div>
                    <div class="text">{email_context.get('body', 'N/A')[:500]}...</div>
                </div>
            </div>

            <!-- Generated Reply -->
            <div class="section">
                <div class="section-title">Generated Reply</div>
                <div class="email-preview">
                    <div class="text">{reply_text}</div>
                </div>
            </div>

            <!-- Recommendation -->
            <div class="section">
                <div class="section-title">Recommendation</div>
                <div class="recommendation">
                    {self._get_recommendation_icon(recommendation)} {recommendation}
                </div>
            </div>

            <div class="timestamp">
                Generated on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
            </div>
        </div>
    </div>
</body>
</html>
"""
        return html

    def _build_dimension_cards(self, validation_result: Dict) -> str:
        """Build HTML for dimension score cards."""
        quality_validation = validation_result.get('quality_validation', {})
        scores = quality_validation.get('scores', {})

        cards_html = []

        for dim_name, dim_data in scores.items():
            if isinstance(dim_data, dict):
                score = dim_data.get('score', 0)
                reasoning = dim_data.get('reasoning', 'No reasoning provided')
            else:
                score = dim_data
                reasoning = 'No reasoning provided'

            score_class = self._get_score_class(score)
            display_name = dim_name.replace('_', ' ').title()

            card_html = f"""
                <div class="dimension-card">
                    <div class="name">{display_name}</div>
                    <div class="score {score_class}">{score} / 3</div>
                    <div class="reasoning">{reasoning}</div>
                </div>
            """
            cards_html.append(card_html)

        return ''.join(cards_html)

    def _build_issues_section(
        self,
        issues: List[Dict],
        title: str,
        issue_type: str
    ) -> str:
        """Build HTML for issues section."""
        if not issues:
            return ""

        items_html = []
        for issue in issues:
            issue_title = issue.get('type', 'Unknown')
            reason = issue.get('reason', 'No reason provided')

            item_html = f"""
                <li class="issue-item {issue_type}">
                    <div class="type">{issue_title}</div>
                    <div class="reason">{reason}</div>
                </li>
            """
            items_html.append(item_html)

        section_html = f"""
            <div class="section">
                <div class="section-title">{title} ({len(issues)})</div>
                <ul class="issues-list">
                    {''.join(items_html)}
                </ul>
            </div>
        """
        return section_html

    def _get_quality_color(self, score: float) -> str:
        """Get color based on quality score."""
        if score >= 0.9:
            return "#28a745"  # Green
        elif score >= 0.75:
            return "#ffc107"  # Yellow
        elif score >= 0.5:
            return "#fd7e14"  # Orange
        else:
            return "#dc3545"  # Red

    def _get_score_class(self, score: int) -> str:
        """Get CSS class based on score."""
        if score == 3:
            return "score-excellent"
        elif score == 2:
            return "score-good"
        elif score == 1:
            return "score-fair"
        else:
            return "score-poor"

    def _get_recommendation_icon(self, recommendation: str) -> str:
        """Get icon for recommendation."""
        if recommendation == "AUTO_SEND":
            return "🚀"
        else:
            return "👤"

    def generate_json_report(
        self,
        validation_result: Dict[str, Any],
        output_path: str = None
    ) -> str:
        """
        Generate JSON validation report.

        Args:
            validation_result: Validation result
            output_path: Optional path to save JSON file

        Returns:
            JSON report string
        """
        report = {
            'timestamp': datetime.now().isoformat(),
            'validation_result': validation_result
        }

        json_str = json.dumps(report, indent=2, ensure_ascii=False)

        if output_path:
            output_path = Path(output_path)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(json_str)

        return json_str


# Global instance
_report_generator = None


def get_report_generator() -> ValidationReportGenerator:
    """Get global ValidationReportGenerator instance."""
    global _report_generator
    if _report_generator is None:
        _report_generator = ValidationReportGenerator()
    return _report_generator

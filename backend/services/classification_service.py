"""
Email classification service.
Uses OpenAI GPT-4o-mini to classify email intent into business categories.
Integrates rubric-based scoring for explainable confidence.
Includes retry logic and circuit breaker for resilience.
"""

import json
from openai import OpenAI, APIError, APITimeoutError, RateLimitError
from config import Config
from services.config_loader import get_config_loader
from services.scoring_service import get_scoring_service
from utils.retry_handler import with_retry, get_circuit_breaker
from utils.logger import get_logger

logger = get_logger('classification_service')

# Load configuration
_config_loader = get_config_loader()
CATEGORIES = _config_loader.get_category_list()
CATEGORY_LABELS_ZH = _config_loader.get_category_labels('zh')
BUSINESS_HINTS = _config_loader.get_business_hints()
NON_BUSINESS_HINTS = _config_loader.get_non_business_hints()

BUSINESS_GATE_PROMPTS = {
    'zh': """你是物流/贸易客服邮箱的守门员。
判断收到的邮件是否是与物流/贸易业务相关的真实业务服务请求。

示例：

示例1 - 业务相关（无订单号的退款请求）：
主题：申请退款
正文：你好，我想退款，产品不符合预期。
结果：{"is_business_related": true, "confidence": 0.95, "reasoning": "明确的退款请求，即使没有订单号也是业务相关"}

示例2 - 业务相关（无订单号的追踪查询）：
主题：订单在哪里
正文：我的货物到哪里了？
结果：{"is_business_related": true, "confidence": 0.90, "reasoning": "追踪查询，尽管缺少订单详情仍是业务相关"}

示例3 - 业务相关（一般价格咨询）：
主题：价格咨询
正文：你们的运费怎么算？
结果：{"is_business_related": true, "confidence": 0.95, "reasoning": "物流服务价格咨询"}

示例4 - 非业务（营销邮件）：
主题：【促销】限时优惠！
正文：亲爱的用户，我们的产品正在促销，点击链接了解更多...
结果：{"is_business_related": false, "confidence": 0.98, "reasoning": "营销邮件，不是服务请求"}

示例5 - 非业务（系统通知）：
主题：Your cloud storage is almost full
正文：Hi, your OneDrive storage is 95% full. Upgrade now to get more space.
结果：{"is_business_related": false, "confidence": 0.99, "reasoning": "云存储通知，与物流/贸易无关"}

示例6 - 非业务（个人消息）：
主题：周末聚餐
正文：嗨，这周末一起吃饭吗？
结果：{"is_business_related": false, "confidence": 0.99, "reasoning": "个人消息，非业务相关"}

仅返回有效的JSON：
{
  "is_business_related": <true|false>,
  "confidence": <0.0到1.0之间的浮点数>,
  "reasoning": "<简短的一句话解释>"
}""",

    'en': """You are a gatekeeper for a logistics/trade customer-service mailbox.
Determine whether the incoming email is a real business-service request relevant to logistics/trade operations.

Examples:

Example 1 - Business-related (refund request without order number):
Subject: Request refund
Body: Hi, I want a refund, the product doesn't meet expectations.
Result: {"is_business_related": true, "confidence": 0.95, "reasoning": "Clear refund request, business-related even without order number"}

Example 2 - Business-related (tracking inquiry without order number):
Subject: Where is my order
Body: Where is my shipment?
Result: {"is_business_related": true, "confidence": 0.90, "reasoning": "Tracking inquiry, business-related despite missing order details"}

Example 3 - Business-related (general pricing inquiry):
Subject: Pricing inquiry
Body: How do you calculate shipping costs?
Result: {"is_business_related": true, "confidence": 0.95, "reasoning": "Pricing inquiry for logistics services"}

Example 4 - Non-business (newsletter):
Subject: [Promotion] Limited time offer!
Body: Dear user, our products are on sale, click the link to learn more...
Result: {"is_business_related": false, "confidence": 0.98, "reasoning": "Marketing newsletter, not a service request"}

Example 5 - Non-business (system notification):
Subject: Your cloud storage is almost full
Body: Hi, your OneDrive storage is 95% full. Upgrade now to get more space.
Result: {"is_business_related": false, "confidence": 0.99, "reasoning": "Cloud storage notification, unrelated to logistics/trade"}

Example 6 - Non-business (personal message):
Subject: Weekend dinner
Body: Hey, want to grab dinner this weekend?
Result: {"is_business_related": false, "confidence": 0.99, "reasoning": "Personal message, not business-related"}

Respond ONLY with valid JSON:
{
  "is_business_related": <true|false>,
  "confidence": <float between 0.0 and 1.0>,
  "reasoning": "<brief one-sentence explanation>"
}"""
}

CATEGORY_PROMPTS = {
    'zh': """你是物流和贸易公司客服系统的专业邮件分类器。
将收到的邮件准确分类到以下类别之一：
- pricing_inquiry: 价格、报价、费率问题
- order_cancellation: 取消或退款请求
- order_tracking: 订单状态/物流追踪
- shipping_time: 运输时长/预计到达时间
- shipping_exception: 延误、损坏、异常运输事件
- billing_invoice: 账单、发票、付款问题
- non_business: 营销邮件、账户/系统通知、内部公告，或与公司物流/贸易业务无关的消息

重要规则：
1) 如果邮件不是针对本公司的客服业务请求，选择non_business。
2) 不要将通用的技术/账户/存储/安全消息映射到物流类别。
3) 只有在证据明确时才使用高置信度（>0.85）。

仅返回有效的JSON：
{
  "category": "<上述类别名称之一>",
  "confidence": <0.0到1.0之间的浮点数>,
  "reasoning": "<简短的一句话解释>"
}""",

    'en': """You are an expert email classifier for a logistics and trade company's customer service system.
Classify the incoming email into exactly one category:
- pricing_inquiry: pricing, quotation, rate questions
- order_cancellation: cancellation or refund requests
- order_tracking: order status / logistics tracking
- shipping_time: shipping duration / ETA
- shipping_exception: delay, damage, abnormal shipping events
- billing_invoice: billing, invoice, payment issues
- non_business: newsletters, marketing, account/system notifications, internal announcements, or messages unrelated to the company's logistics/trade business

Important rules:
1) If the email is not a customer service business request to this company, choose non_business.
2) Do NOT map generic technology/account/storage/security messages to logistics categories.
3) Only use high confidence (>0.85) when evidence is explicit.

Respond ONLY with valid JSON:
{
  "category": "<one of the category names above>",
  "confidence": <float between 0.0 and 1.0>,
  "reasoning": "<brief one-sentence explanation>"
}"""
}



class ClassificationService:
    """Classifies customer service emails using OpenAI GPT."""

    def __init__(self, language: str = 'zh'):
        self.client = OpenAI(api_key=Config.OPENAI_API_KEY, base_url=Config.OPENAI_BASE_URL)
        self.model = Config.OPENAI_MODEL
        self.language = language  # Store language for prompt selection
        self.config_loader = get_config_loader()
        self.scoring_service = get_scoring_service()
        self.business_gate_threshold = self.config_loader.get_global_threshold('business_gate_threshold')

        # Circuit breaker for OpenAI API
        self.circuit_breaker = get_circuit_breaker(
            'openai_classification',
            failure_threshold=5,
            recovery_timeout=60
        )

    @staticmethod
    def _contains_any(text: str, keywords: list[str]) -> bool:
        lowered = text.lower()
        return any(keyword in lowered for keyword in keywords)

    def _rule_based_non_business(self, subject: str, body: str) -> bool:
        text = f"{subject}\n{body[:3000]}"
        has_business = self._contains_any(text, BUSINESS_HINTS)
        has_non_business = self._contains_any(text, NON_BUSINESS_HINTS)
        return has_non_business and not has_business

    def _gate_business_relevance(self, subject: str, body: str) -> dict:
        """Gate business relevance with retry logic."""
        user_content = f"Subject: {subject}\n\nBody:\n{body[:3000]}"

        @with_retry(
            max_retries=3,
            base_delay=1.0,
            retryable_exceptions=(APIError, APITimeoutError, RateLimitError),
            circuit_breaker=self.circuit_breaker
        )
        def _call_api():
            prompt = BUSINESS_GATE_PROMPTS.get(self.language, BUSINESS_GATE_PROMPTS['en'])
            return self.client.chat.completions.create(
                model=self.model,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": user_content},
                ],
                temperature=0.0,
                max_tokens=120,
            )

        try:
            response = _call_api()
            raw = response.choices[0].message.content
            result = json.loads(raw)
            is_business_related = bool(result.get("is_business_related", False))
            confidence = float(result.get("confidence", 0.5))
            confidence = max(0.0, min(1.0, confidence))
            reasoning = result.get("reasoning", "")

            logger.info("Business gate check completed", {
                'is_business': is_business_related,
                'confidence': confidence
            })

            return {
                "is_business_related": is_business_related,
                "business_confidence": confidence,
                "business_reasoning": reasoning,
            }
        except Exception as e:
            logger.error("Business gate check failed", {
                'error': str(e),
                'subject': subject[:50]
            }, exc_info=True)
            # Fallback: assume business-related to avoid false negatives
            return {
                "is_business_related": True,
                "business_confidence": 0.5,
                "business_reasoning": f"API error, defaulting to business: {str(e)}",
            }

    def classify_email(self, subject: str, body: str) -> dict:
        """
        Classify an email and return {category, confidence, reasoning}.
        Falls back to a safe default on parse errors.
        Includes retry logic and fallback strategies.
        """
        logger.info("Starting email classification", {
            'subject': subject[:50]
        })

        # Rule-based non-business check (fast, no API call)
        if self._rule_based_non_business(subject, body):
            category = "non_business"
            logger.info("Classified as non-business (rule-based)", {
                'category': category
            })
            return {
                "category": category,
                "confidence": 0.98,
                "reasoning": "Rule-based filter: detected non-business notification/marketing content.",
                "category_label": CATEGORY_LABELS_ZH.get(category, category),
                "is_business_related": False,
                "business_confidence": 0.98,
            }

        # Business gate check (with retry)
        gate = self._gate_business_relevance(subject, body)
        if not gate["is_business_related"] and gate["business_confidence"] >= self.business_gate_threshold:
            category = "non_business"
            logger.info("Classified as non-business (gate)", {
                'category': category,
                'confidence': gate["business_confidence"]
            })
            return {
                "category": category,
                "confidence": max(0.85, gate["business_confidence"]),
                "reasoning": gate["business_reasoning"] or "Business gate: email judged as non-business.",
                "category_label": CATEGORY_LABELS_ZH.get(category, category),
                "is_business_related": False,
                "business_confidence": gate["business_confidence"],
            }

        user_content = f"Subject: {subject}\n\nBody:\n{body[:3000]}"

        @with_retry(
            max_retries=3,
            base_delay=1.0,
            retryable_exceptions=(APIError, APITimeoutError, RateLimitError),
            circuit_breaker=self.circuit_breaker
        )
        def _call_classification_api():
            prompt = CATEGORY_PROMPTS.get(self.language, CATEGORY_PROMPTS['en'])
            return self.client.chat.completions.create(
                model=self.model,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": user_content},
                ],
                temperature=0.1,
                max_tokens=200,
            )

        try:
            response = _call_classification_api()
            raw = response.choices[0].message.content
            result = json.loads(raw)

            category = result.get("category", "non_business")
            if category not in CATEGORIES:
                category = "non_business"

            confidence = float(result.get("confidence", 0.5))
            confidence = max(0.0, min(1.0, confidence))

            if category != "non_business" and confidence >= 0.9 and self._rule_based_non_business(subject, body):
                category = "non_business"
                confidence = 0.85

            # Phase 2: Apply rubric-based scoring for explainable confidence
            rubric_scores = None
            try:
                rubric_result = self.scoring_service.score_classification(
                    subject=subject,
                    body=body,
                    category=category,
                    use_llm=True
                )
                rubric_scores = rubric_result
                # Use rubric confidence if available
                if 'confidence' in rubric_result:
                    confidence = rubric_result['confidence']
            except Exception as e:
                # Fallback to original confidence if rubric scoring fails
                logger.warning("Rubric scoring failed, using original confidence", {
                    'error': str(e)
                })

            logger.info("Email classified successfully", {
                'category': category,
                'confidence': confidence
            })

            return {
                "category": category,
                "confidence": confidence,
                "reasoning": result.get("reasoning", ""),
                "category_label": CATEGORY_LABELS_ZH.get(category, category),
                "is_business_related": category != "non_business",
                "business_confidence": gate["business_confidence"],
                "rubric_scores": rubric_scores,  # Phase 2: Include rubric scores
            }

        except Exception as e:
            # Fallback: Use rule-based classification
            logger.error("Classification API failed, using fallback", {
                'error': str(e),
                'subject': subject[:50]
            }, exc_info=True)

            return self._fallback_classification(subject, body, gate)



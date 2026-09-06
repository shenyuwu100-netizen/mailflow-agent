"""
Reply generation and email processing service.
Generates GPT reply drafts and routes emails based on confidence threshold.
Integrates rubric-based scoring for auto-send decisions.
"""

from datetime import datetime, timezone
from openai import OpenAI
from config import Config
from mailflow.policy import decide, local_proposal
from models.database import get_db_connection
from services.graph_service import EmailSendError
from services.config_loader import get_config_loader
from services.scoring_service import get_scoring_service
from services.validation_service import get_validation_service
from services.pii_service import get_pii_service
from models.audit_log import log_audit_event, AuditAction
from utils.logger import get_logger

logger = get_logger('reply_service')

REPLY_SYSTEM_PROMPTS = {
    'zh': """你是一家物流和贸易公司的专业客服代表。
根据邮件内容和识别的类别，撰写清晰、礼貌、有帮助的回复邮件。
保持回复简洁（3-5句话）、专业且富有同理心。
不要使用占位符如[追踪号] — 承认询问并通用地解释后续步骤。
用中文回复。""",

    'en': """You are a professional customer service representative for a logistics and trade company.
Write a clear, polite, and helpful reply email to the customer based on the email content and its identified category.
Keep the reply concise (3-5 sentences), professional, and empathetic.
Do NOT use placeholders like [tracking number] — acknowledge the inquiry and explain next steps generically.
Write in English."""
}


class ReplyService:
    """Generates reply drafts and orchestrates the full email processing pipeline."""

    def __init__(self, language: str = 'zh'):
        self.client = OpenAI(api_key=Config.OPENAI_API_KEY, base_url=Config.OPENAI_BASE_URL)
        self.model = Config.OPENAI_MODEL
        self.language = language  # Store language for prompt and template selection
        self.config_loader = get_config_loader()
        self.scoring_service = get_scoring_service()
        self.validation_service = get_validation_service()
        self.pii_service = get_pii_service()
        self.threshold = self.config_loader.get_global_threshold('confidence_threshold')
        self.auto_send_minimum = self.config_loader.get_global_threshold('auto_send_minimum_confidence')

    @staticmethod
    def _extract_sender_local_part(sender: str) -> str:
        local_part = (sender or "").split("@", 1)[0].strip()
        return local_part or "客户"

    @staticmethod
    def _extract_customer_name(sender: str) -> str:
        local_part = ReplyService._extract_sender_local_part(sender)
        normalized = local_part.replace(".", " ").replace("_", " ").replace("-", " ").strip()
        return normalized or "客户"

    @staticmethod
    def _resolve_base_date(received_at: str) -> datetime:
        if not received_at:
            return datetime.now(timezone.utc)
        try:
            parsed = datetime.fromisoformat(received_at.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed
        except ValueError:
            return datetime.now(timezone.utc)

    @staticmethod
    def _select_product(subject: str, body: str, products: list[dict]) -> dict | None:
        if not products:
            return None

        haystack = f"{subject}\n{body}".lower()
        for product in products:
            product_name = str(product.get("product_name", "")).strip().lower()
            if product_name and product_name in haystack:
                return product

        return products[0]

    def _generate_pricing_template_reply(
        self,
        sender: str,
        subject: str,
        body: str,
        received_at: str,
    ) -> str:
        from services.logistics_service import get_logistics_service, RouteNotFoundError
        import re

        customer_name = self._extract_customer_name(sender)
        text = f"{subject}\n{body}".lower()

        # Extract route information
        origin = self._extract_city_name(text, is_origin=True)
        destination = self._extract_city_name(text, is_origin=False)

        # Determine shipping method
        shipping_method = None
        if any(keyword in text for keyword in ['海运', 'sea freight', '海运价格', '整柜']):
            shipping_method = 'sea_freight'
        elif any(keyword in text for keyword in ['空运', 'air freight', '空运价格', '航空']):
            shipping_method = 'air_freight'

        # Extract container type for sea freight
        container_type = None
        if shipping_method == 'sea_freight':
            if '20' in text and ('尺' in text or 'ft' in text or '柜' in text):
                container_type = '20ft'
            elif '40' in text and ('尺' in text or 'ft' in text or '柜' in text):
                container_type = '40ft'

        # Extract weight for air freight
        weight_kg = None
        if shipping_method == 'air_freight':
            weight_match = re.search(r'(\d+)\s*(?:公斤|kg|千克)', text)
            if weight_match:
                weight_kg = float(weight_match.group(1))

        # Query logistics database if we have enough information
        if origin and destination and shipping_method:
            try:
                logistics_service = get_logistics_service()
                route = logistics_service.query_route_pricing(
                    origin=origin,
                    destination=destination,
                    shipping_method=shipping_method,
                    container_type=container_type,
                    weight_kg=weight_kg
                )

                route_info = logistics_service.format_route_pricing(route, language="zh")

                return (
                    f"尊敬的 {customer_name}，\n\n"
                    "感谢您的询价。根据您提供的信息，以下是我们的运输报价：\n\n"
                    f"{route_info}\n\n"
                    "备注：\n"
                    "- 以上报价为基础运费，不含目的港费用、关税等\n"
                    "- 实际价格可能因货物性质、旺季因素等有所调整\n"
                    "- 报价有效期：7天\n\n"
                    "如需更详细的报价或有其他问题，请随时与我们联系。\n\n"
                    "此致，\n"
                    "MIS2001 Dev Ltd.\n"
                    "+86 123 456 7890"
                )

            except RouteNotFoundError:
                # Route not in database - request more details
                return (
                    f"尊敬的 {customer_name}，\n\n"
                    "感谢您的询价。关于您咨询的运输路线，我们需要进一步核实具体报价。\n\n"
                    f"您咨询的路线：{origin} -> {destination}\n\n"
                    "为了给您提供准确的报价，请提供以下信息：\n"
                    "- 货物类型和名称\n"
                    "- 货物重量/体积\n"
                    "- 是否需要特殊处理（如冷藏、危险品等）\n"
                    "- 期望的运输时效\n\n"
                    "我们的客服团队将在收到详细信息后，尽快为您提供准确报价。\n\n"
                    "此致，\n"
                    "MIS2001 Dev Ltd.\n"
                    "+86 123 456 7890"
                )

        # Insufficient information - request details
        return (
            f"尊敬的 {customer_name}，\n\n"
            "感谢您对我们物流服务的关注。为了给您提供准确的报价，请提供以下信息：\n\n"
            "- 起运地（城市）\n"
            "- 目的地（城市）\n"
            "- 运输方式（海运/空运）\n"
            "- 货物重量/体积或柜型（如20尺柜、40尺柜）\n"
            "- 货物类型\n\n"
            "收到详细信息后，我们将尽快为您提供专业的运输方案和报价。\n\n"
            "此致，\n"
            "MIS2001 Dev Ltd.\n"
            "+86 123 456 7890"
        )

    @staticmethod
    def _extract_city_name(text: str, is_origin: bool) -> str | None:
        """Extract city name from pricing inquiry text."""
        # Common city names in Chinese and English
        cities = [
            '深圳', '上海', '广州', '北京', '香港', '天津', '宁波', '青岛',
            '纽约', '洛杉矶', '芝加哥', '休斯顿', '旧金山',
            '伦敦', '巴黎', '汉堡', '鹿特丹', '安特卫普',
            '东京', '新加坡', '迪拜', '悉尼', '墨尔本'
        ]

        # Look for "从X到Y" or "X到Y" pattern
        import re
        pattern = r'(?:从|自)?\s*([^\s到至]+?)\s*(?:到|至|->|→)\s*([^\s，。！？]+)'
        match = re.search(pattern, text)

        if match:
            origin_candidate = match.group(1).strip()
            dest_candidate = match.group(2).strip()

            # Check if candidates are valid cities
            for city in cities:
                if is_origin and city in origin_candidate:
                    return city
                elif not is_origin and city in dest_candidate:
                    return city

        # Fallback: look for any city mention
        for city in cities:
            if city in text:
                return city

        return None

    @staticmethod
    def _generate_order_number() -> str:
        from random import randint

        return f"ORD{randint(100000, 999999)}"

    def _generate_billing_invoice_template_reply(self, sender: str, body: str) -> str:
        from services.order_service import get_order_service, OrderNotFoundError

        customer_name = self._extract_sender_local_part(sender)
        order_number = self._extract_order_number_from_text(body)

        # Try to find order for billing/invoice information
        if order_number:
            try:
                order_service = get_order_service()
                order = order_service.validate_order_ownership(order_number, sender)

                # Format billing information from order
                order_info = order_service.format_order_info(order, language="zh")

                return (
                    f"尊敬的 {customer_name}，\n\n"
                    "感谢您的来信。以下是您订单的账单及发票信息：\n\n"
                    f"{order_info}\n\n"
                    "发票说明：\n"
                    "- 电子发票将在订单完成后3个工作日内发送至您的邮箱\n"
                    "- 如需纸质发票，请提供邮寄地址\n"
                    "- 如需修改发票抬头，请在开票前联系我们\n\n"
                    "如有任何疑问，请随时联系我们的财务部门。\n\n"
                    "此致，\n"
                    "MIS2001 Dev Ltd. 财务部\n"
                    "+86 123 456 7890"
                )
            except OrderNotFoundError:
                return (
                    f"尊敬的 {customer_name}，\n\n"
                    f"感谢您的来信。关于您查询的订单 {order_number}，我们在系统中未能找到相关记录。\n\n"
                    "为了查询您的账单和发票信息，请提供：\n"
                    "- 正确的订单号\n"
                    "- 下单时使用的邮箱\n"
                    "- 订单日期（大致时间）\n\n"
                    "财务部联系方式：+86 123 456 7890\n\n"
                    "此致，\n"
                    "MIS2001 Dev Ltd. 财务部"
                )

        # No order number found - request it
        return (
            f"尊敬的 {customer_name}，\n\n"
            "感谢您的来信。为了查询您的账单和发票信息，请提供以下信息：\n\n"
            "- 订单号（格式如：ORD123456）\n"
            "- 或提供下单时使用的邮箱和订单日期\n\n"
            "收到信息后，我们将尽快为您提供详细的账单和发票信息。\n\n"
            "此致，\n"
            "MIS2001 Dev Ltd. 财务部\n"
            "+86 123 456 7890"
        )

    def _generate_non_business_template_reply(self, sender: str) -> str:
        customer_name = self._extract_sender_local_part(sender)
        return (
            f"尊敬的 {customer_name}，\n\n"
            "感谢您的来信。根据您的邮件内容，当前此邮件并不涉及需要进一步回复的业务内容。如果您有任何其他问题或需求，欢迎随时与我们联系。\n\n"
            "感谢您的关注！\n\n"
            "此致，\n"
            "MIS2001 Dev Ltd.\n"
            "+86 123 456 7890"
        )

    def _generate_order_cancellation_template_reply(self, sender: str, body: str) -> str:
        from services.order_service import get_order_service, OrderNotFoundError

        customer_name = self._extract_sender_local_part(sender)
        order_number = self._extract_order_number_from_text(body)

        # Try to validate order
        if order_number:
            try:
                order_service = get_order_service()
                order = order_service.validate_order_ownership(order_number, sender)

                # Format order info (do NOT update status here - that should happen after email is sent)
                order_info = order_service.format_order_info(order, language="zh")

                return (
                    f"尊敬的 {customer_name}，\n\n"
                    f"感谢您的来信。我们已经收到您的取消订单请求，订单信息如下：\n\n"
                    f"{order_info}\n\n"
                    "我们将尽快处理您的退款申请。退款预计将在七个工作日内完成，"
                    "您将收到相应的退款通知。如果有任何问题，您可以随时联系客户服务团队。\n\n"
                    "我们为此次不便向您表示歉意，并感谢您的理解。\n\n"
                    "此致，\n"
                    "MIS2001 Dev Ltd.\n"
                    "+86 123 456 7890"
                )
            except OrderNotFoundError:
                # Order not found or doesn't belong to customer
                return (
                    f"尊敬的 {customer_name}，\n\n"
                    f"感谢您的来信。关于您提到的订单 {order_number}，我们在系统中未能找到相关记录，"
                    "或该订单不属于您的账户。\n\n"
                    "请您核实订单号是否正确，或联系我们的客服团队获取进一步帮助。\n\n"
                    "此致，\n"
                    "MIS2001 Dev Ltd.\n"
                    "+86 123 456 7890"
                )

        # No order number found in email
        return (
            f"尊敬的 {customer_name}，\n\n"
            "感谢您的来信。为了更好地处理您的取消订单请求，请您提供以下信息：\n\n"
            "- 订单号（格式如：ORD123456）\n"
            "- 订单日期\n\n"
            "收到您的订单信息后，我们将立即为您处理。\n\n"
            "此致，\n"
            "MIS2001 Dev Ltd.\n"
            "+86 123 456 7890"
        )

    def _generate_order_tracking_template_reply(self, sender: str, body: str) -> str:
        from services.order_service import get_order_service, OrderNotFoundError

        customer_name = self._extract_sender_local_part(sender)
        order_number = self._extract_order_number_from_text(body)

        # Try to validate order
        if order_number:
            try:
                order_service = get_order_service()
                order = order_service.validate_order_ownership(order_number, sender)

                order_info = order_service.format_order_info(order, language="zh")

                # Add ETA if in transit
                eta_info = ""
                if order['shipping_status'] == 'in_transit':
                    eta_info = "\n- 预计到达日期：2026/5/15"

                return (
                    f"尊敬的 {customer_name}，\n\n"
                    "感谢您的来信。以下是您订单的当前物流状态：\n\n"
                    f"{order_info}{eta_info}\n\n"
                    "如需更多帮助，您可以随时通过我们的客服热线或邮件联系我们。\n\n"
                    "感谢您的耐心等待，祝您有愉快的一天！\n\n"
                    "此致，\n"
                    "MIS2001 Dev Ltd.\n"
                    "+86 123 456 7890"
                )
            except OrderNotFoundError:
                return (
                    f"尊敬的 {customer_name}，\n\n"
                    f"感谢您的来信。关于您查询的订单 {order_number}，我们在系统中未能找到相关记录。\n\n"
                    "请您核实订单号是否正确，或提供以下信息以便我们查询：\n"
                    "- 下单时使用的邮箱\n"
                    "- 订单日期\n"
                    "- 产品名称\n\n"
                    "客服热线：+86 123 456 7890\n\n"
                    "此致，\n"
                    "MIS2001 Dev Ltd."
                )

        # No order number found
        return (
            f"尊敬的 {customer_name}，\n\n"
            "感谢您的来信。为了查询您的订单物流状态，请您提供订单号（格式如：ORD123456）。\n\n"
            "您也可以登录我们的网站查询订单状态，或联系客服团队获取帮助。\n\n"
            "此致，\n"
            "MIS2001 Dev Ltd.\n"
            "+86 123 456 7890"
        )

    def _generate_shipping_time_template_reply(self, sender: str, body: str) -> str:
        from services.order_service import get_order_service, OrderNotFoundError

        customer_name = self._extract_sender_local_part(sender)
        order_number = self._extract_order_number_from_text(body)

        # If order number provided, query database for real order info
        if order_number:
            try:
                order_service = get_order_service()
                order = order_service.validate_order_ownership(order_number, sender)
                order_info = order_service.format_order_info(order, language="zh")

                # Add ETA if in transit
                eta_info = ""
                if order['shipping_status'] == 'in_transit':
                    eta_info = "\n- 预计到达日期：2026/5/15"

                return (
                    f"尊敬的 {customer_name}，\n\n"
                    "感谢您的来信。以下是您订单的运输时效信息：\n\n"
                    f"{order_info}{eta_info}\n\n"
                    "如需更多帮助，您可以随时通过我们的客服热线或邮件联系我们。\n\n"
                    "感谢您的耐心等待，祝您有愉快的一天！\n\n"
                    "此致，\n"
                    "MIS2001 Dev Ltd.\n"
                    "+86 123 456 7890"
                )
            except OrderNotFoundError:
                return (
                    f"尊敬的 {customer_name}，\n\n"
                    f"感谢您的来信。关于您查询的订单 {order_number}，我们在系统中未能找到相关记录。\n\n"
                    "请您核实订单号是否正确，或提供以下信息以便我们查询：\n"
                    "- 下单时使用的邮箱\n"
                    "- 订单日期\n"
                    "- 产品名称\n\n"
                    "此致，\n"
                    "MIS2001 Dev Ltd.\n"
                    "+86 123 456 7890"
                )

        # No order number - provide general shipping time guidance
        return (
            f"尊敬的 {customer_name}，\n\n"
            "感谢您的来信。关于运输时效的咨询，以下是我们公司的一般运输时间参考：\n\n"
            "- 国内快递：1-3个工作日\n"
            "- 国际快递（亚洲）：3-5个工作日\n"
            "- 国际快递（欧美）：5-7个工作日\n"
            "- 海运（亚洲）：7-14天\n"
            "- 海运（欧美）：20-35天\n\n"
            "具体时效会根据起运地、目的地、货物类型、海关清关等因素有所不同。\n\n"
            "如果您有具体的订单需要查询，请提供订单号，我们将为您提供准确的物流信息。\n"
            "如需获取具体路线的报价和时效，欢迎提供详细的起运地和目的地信息。\n\n"
            "此致，\n"
            "MIS2001 Dev Ltd.\n"
            "+86 123 456 7890"
        )

    @staticmethod
    def _summarize_exception_from_body(body: str) -> str:
        import re

        text = re.sub(r"<[^>]+>", " ", body or "")
        text = re.sub(r"\s+", " ", text).strip()
        if not text:
            return "运输环节出现异常，包裹未按计划完成交付。"

        text = text[:80]
        if len(text) == 80:
            text = text.rstrip("，。；;,. ") + "……"
        return text

    @staticmethod
    def _extract_order_number_from_text(body: str) -> str | None:
        import re

        text = body or ""
        patterns = [
            r"\bORD\d{6,}\b",
            r"订单号\s*[:：]?\s*([A-Za-z0-9\-]{4,})",
            r"order\s*(?:id|number)\s*[:：]?\s*([A-Za-z0-9\-]{4,})",
        ]
        for pattern in patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match:
                return match.group(1) if match.lastindex else match.group(0)
        return None

    def _generate_shipping_exception_template_reply(self, sender: str, body: str) -> str:
        from services.order_service import get_order_service, OrderNotFoundError

        customer_name = self._extract_sender_local_part(sender)
        order_number = self._extract_order_number_from_text(body)
        exception_desc = self._summarize_exception_from_body(body)

        # Try to validate order
        if order_number:
            try:
                order_service = get_order_service()
                order = order_service.validate_order_ownership(order_number, sender)

                # Update shipping status to exception
                # DEMO_MODE: Skip database updates to preserve test data
                from config import Config

                if not Config.DEMO_MODE:
                    order_service.update_order_status(order_number, shipping_status="exception")

                order_info = order_service.format_order_info(order, language="zh")
                solution = "我们将安排专人与您联系，协调上门回收货物并重新派送"

                return (
                    f"尊敬的 {customer_name}，\n\n"
                    f"感谢您的反馈。关于您的订单，我们注意到出现了运输异常情况：\n\n"
                    f"{order_info}\n\n"
                    f"- 异常情况：{exception_desc}\n"
                    f"- 解决方案：{solution}\n\n"
                    "我们深感抱歉给您带来的不便，并会尽快处理此问题。"
                    "客服团队将在24小时内与您联系。\n\n"
                    "再次为此给您带来的困扰表示歉意，感谢您的理解。\n\n"
                    "此致，\n"
                    "MIS2001 Dev Ltd.\n"
                    "+86 123 456 7890"
                )
            except OrderNotFoundError:
                return (
                    f"尊敬的 {customer_name}，\n\n"
                    f"感谢您的反馈。关于您提到的订单 {order_number}，我们在系统中未能找到相关记录。\n\n"
                    "为了更好地处理您的运输异常问题，请提供：\n"
                    "- 正确的订单号\n"
                    "- 物流单号（如有）\n"
                    "- 异常情况描述\n\n"
                    "我们将优先处理您的问题。客服热线：+86 123 456 7890\n\n"
                    "此致，\n"
                    "MIS2001 Dev Ltd."
                )

        # No order number found
        return (
            f"尊敬的 {customer_name}，\n\n"
            "感谢您的反馈。我们非常重视您遇到的运输问题。\n\n"
            "为了快速处理，请您提供：\n"
            "- 订单号（格式如：ORD123456）\n"
            "- 物流单号（如有）\n"
            "- 具体异常情况\n\n"
            "收到信息后，我们将立即为您处理。\n\n"
            "此致，\n"
            "MIS2001 Dev Ltd.\n"
            "+86 123 456 7890"
        )

    # English template methods
    def _generate_pricing_template_reply_en(self, sender: str, subject: str, body: str, received_at: str) -> str:
        """English version of pricing inquiry reply."""
        customer_name = self._extract_sender_local_part(sender)
        return (
            f"Dear {customer_name},\n\n"
            "Thank you for your inquiry. To provide you with an accurate quote, please provide the following information:\n\n"
            "- Origin city\n"
            "- Destination city\n"
            "- Shipping method (sea freight/air freight)\n"
            "- Cargo weight/volume or container type (e.g., 20ft, 40ft)\n"
            "- Cargo type\n\n"
            "Once we receive this information, we will provide you with a professional shipping solution and quote.\n\n"
            "Best regards,\n"
            "MIS2001 Dev Ltd.\n"
            "+86 123 456 7890"
        )

    def _generate_order_cancellation_template_reply_en(self, sender: str, body: str) -> str:
        """English version of order cancellation reply."""
        customer_name = self._extract_sender_local_part(sender)
        order_number = self._extract_order_number_from_text(body)

        if not order_number:
            return (
                f"Dear {customer_name},\n\n"
                "Thank you for contacting us. To process your cancellation request, please provide the following information:\n\n"
                "- Order number (format: ORD123456)\n"
                "- Order date\n\n"
                "Once we receive your order information, we will process it immediately.\n\n"
                "Best regards,\n"
                "MIS2001 Dev Ltd.\n"
                "+86 123 456 7890"
            )

        return (
            f"Dear {customer_name},\n\n"
            f"Thank you for contacting us. We have received your cancellation request for order {order_number}. "
            "We will process your refund request as soon as possible. The refund is expected to be completed within seven business days, "
            "and you will receive a refund notification.\n\n"
            "We apologize for any inconvenience and thank you for your understanding.\n\n"
            "Best regards,\n"
            "MIS2001 Dev Ltd.\n"
            "+86 123 456 7890"
        )

    def _generate_order_tracking_template_reply_en(self, sender: str, body: str) -> str:
        """English version of order tracking reply."""
        customer_name = self._extract_sender_local_part(sender)
        order_number = self._extract_order_number_from_text(body)

        if not order_number:
            return (
                f"Dear {customer_name},\n\n"
                "Thank you for contacting us. To check your order status, please provide your order number (format: ORD123456).\n\n"
                "You can also log in to our website to check your order status or contact our customer service team for assistance.\n\n"
                "Best regards,\n"
                "MIS2001 Dev Ltd.\n"
                "+86 123 456 7890"
            )

        return (
            f"Dear {customer_name},\n\n"
            f"Thank you for your inquiry about order {order_number}. We are currently checking the status of your shipment. "
            "Our customer service team will contact you within 24 hours with detailed tracking information.\n\n"
            "If you need further assistance, please feel free to contact us.\n\n"
            "Best regards,\n"
            "MIS2001 Dev Ltd.\n"
            "+86 123 456 7890"
        )

    def _generate_shipping_time_template_reply_en(self, sender: str, body: str) -> str:
        """English version of shipping time reply."""
        customer_name = self._extract_sender_local_part(sender)
        return (
            f"Dear {customer_name},\n\n"
            "Thank you for your inquiry about shipping times. Here are our general shipping time estimates:\n\n"
            "- Domestic express: 1-3 business days\n"
            "- International express (Asia): 3-5 business days\n"
            "- International express (Europe/Americas): 5-7 business days\n"
            "- Sea freight (Asia): 7-14 days\n"
            "- Sea freight (Europe/Americas): 20-35 days\n\n"
            "Actual delivery times may vary based on origin, destination, cargo type, and customs clearance.\n\n"
            "If you have a specific order to track, please provide your order number for accurate information.\n\n"
            "Best regards,\n"
            "MIS2001 Dev Ltd.\n"
            "+86 123 456 7890"
        )

    def _generate_shipping_exception_template_reply_en(self, sender: str, body: str) -> str:
        """English version of shipping exception reply."""
        customer_name = self._extract_sender_local_part(sender)
        order_number = self._extract_order_number_from_text(body)

        if not order_number:
            return (
                f"Dear {customer_name},\n\n"
                "Thank you for reporting the shipping issue. We take your concerns very seriously.\n\n"
                "To expedite resolution, please provide:\n"
                "- Order number (format: ORD123456)\n"
                "- Tracking number (if available)\n"
                "- Description of the issue\n\n"
                "Once we receive this information, we will address it immediately.\n\n"
                "Best regards,\n"
                "MIS2001 Dev Ltd.\n"
                "+86 123 456 7890"
            )

        return (
            f"Dear {customer_name},\n\n"
            f"Thank you for reporting the issue with order {order_number}. We have noted the shipping exception and will arrange for our team to contact you "
            "to coordinate a solution. Our customer service team will reach out within 24 hours.\n\n"
            "We sincerely apologize for any inconvenience and appreciate your understanding.\n\n"
            "Best regards,\n"
            "MIS2001 Dev Ltd.\n"
            "+86 123 456 7890"
        )

    def _generate_billing_invoice_template_reply_en(self, sender: str, body: str) -> str:
        """English version of billing/invoice reply."""
        customer_name = self._extract_sender_local_part(sender)
        order_number = self._extract_order_number_from_text(body)

        if not order_number:
            return (
                f"Dear {customer_name},\n\n"
                "Thank you for your inquiry. To retrieve your billing and invoice information, please provide:\n\n"
                "- Order number (format: ORD123456)\n"
                "- Or the email address used for the order and order date\n\n"
                "Once we receive this information, we will provide detailed billing and invoice information.\n\n"
                "Best regards,\n"
                "MIS2001 Dev Ltd. Finance Department\n"
                "+86 123 456 7890"
            )

        return (
            f"Dear {customer_name},\n\n"
            f"Thank you for your inquiry about order {order_number}. We are retrieving your billing and invoice information. "
            "Our finance team will send the detailed information to your email within 2 business days.\n\n"
            "If you have any questions, please feel free to contact our finance department.\n\n"
            "Best regards,\n"
            "MIS2001 Dev Ltd. Finance Department\n"
            "+86 123 456 7890"
        )

    def _generate_non_business_template_reply_en(self, sender: str) -> str:
        """English version of non-business reply."""
        customer_name = self._extract_sender_local_part(sender)
        return (
            f"Dear {customer_name},\n\n"
            "Thank you for your email. Based on the content, this message does not require a business response. "
            "If you have any other questions or needs, please feel free to contact us.\n\n"
            "Thank you for your attention!\n\n"
            "Best regards,\n"
            "MIS2001 Dev Ltd.\n"
            "+86 123 456 7890"
        )

    def generate_reply(
        self,
        sender: str,
        received_at: str,
        subject: str,
        body: str,
        category: str,
        reasoning: str,
    ) -> str:
        """Generate reply draft based on category policy and language."""
        # Route to language-specific template methods
        if self.language == 'en':
            if category in ("pricing_inquiry", "price_inquiry"):
                return self._generate_pricing_template_reply_en(sender, subject, body, received_at)
            if category == "order_cancellation":
                return self._generate_order_cancellation_template_reply_en(sender, body)
            if category == "order_tracking":
                return self._generate_order_tracking_template_reply_en(sender, body)
            if category == "shipping_time":
                return self._generate_shipping_time_template_reply_en(sender, body)
            if category == "shipping_exception":
                return self._generate_shipping_exception_template_reply_en(sender, body)
            if category == "billing_invoice":
                return self._generate_billing_invoice_template_reply_en(sender, body)
            if category == "non_business":
                return self._generate_non_business_template_reply_en(sender)
        else:  # Default to Chinese
            if category in ("pricing_inquiry", "price_inquiry"):
                return self._generate_pricing_template_reply(sender, subject, body, received_at)
            if category == "order_cancellation":
                return self._generate_order_cancellation_template_reply(sender, body)
            if category == "order_tracking":
                return self._generate_order_tracking_template_reply(sender, body)
            if category == "shipping_time":
                return self._generate_shipping_time_template_reply(sender, body)
            if category == "shipping_exception":
                return self._generate_shipping_exception_template_reply(sender, body)
            if category == "billing_invoice":
                return self._generate_billing_invoice_template_reply(sender, body)
            if category == "non_business":
                return self._generate_non_business_template_reply(sender)

        user_content = (
            f"Customer Email Subject: {subject}\n\n"
            f"Customer Email Body:\n{body[:3000]}\n\n"
            f"Identified Category: {category}\n"
            f"Classification Reasoning: {reasoning}"
        )

        prompt = REPLY_SYSTEM_PROMPTS.get(self.language, REPLY_SYSTEM_PROMPTS['en'])
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": user_content},
            ],
            temperature=0.4,
            max_tokens=500,
        )

        return response.choices[0].message.content.strip()

    def process_email(
        self,
        message_id: str,
        subject: str,
        sender: str,
        received_at: str,
        body: str,
        classification: dict,
        graph_service=None,
        operator: str = "system",
    ) -> dict:
        """
        Full pipeline: persist email + reply, decide auto-send or pending_review.
        Returns the saved email record dict.
        """
        # Detect language early
        from services.language_service import get_language_service
        lang_service = get_language_service()
        full_text = f"{subject or ''}\n{body or ''}"
        lang_result = lang_service.detect_language(full_text)
        detected_language = lang_result.get('language', 'unknown')
        language_confidence = lang_result.get('confidence', 0.0)

        # Map to supported languages (zh/en only)
        if detected_language == 'zh':
            language = 'zh'
        elif detected_language == 'en':
            language = 'en'
        elif detected_language == 'mixed':
            # Use primary language
            primary = lang_result.get('details', {}).get('primary_language', 'zh')
            language = 'zh' if primary == 'zh' else 'en'
        else:
            language = 'zh'  # Default to Chinese

        # Update self.language for reply generation
        self.language = language

        category = classification["category"]
        confidence = classification["confidence"]
        reasoning = classification.get("reasoning", "")
        is_business_related = classification.get("is_business_related", category != "non_business")

        # Phase 5: PII detection on incoming email
        pii_detected = False
        pii_check_failed = False
        pii_types_found = []
        try:
            pii_result = self.pii_service.detect_pii(full_text)
            if pii_result:
                pii_detected = True
                pii_types_found = [pt.value for pt in pii_result.keys()]
                logger.info("PII detected in email", {
                    'message_id': message_id,
                    'pii_types': pii_types_found
                })
        except Exception as e:
            pii_check_failed = True
            logger.error("PII detection failed", {'error': str(e)})

        # Phase 5: Consent check
        consent_status = classification.get("consent_status", "unknown")

        sent_at = None
        retry_count = 0
        last_error = None
        validation_result = None

        if category == "non_business" or not is_business_related:
            reply_text = self.generate_reply(sender, received_at, subject, body, "non_business", reasoning)
            status = "ignored_no_reply"
            auto_send_rubric_scores = None
        else:
            reply_text = self.generate_reply(sender, received_at, subject, body, category, reasoning)

            # Phase 2: Use rubric-based scoring for auto-send decision
            auto_send_rubric_scores = None
            auto_send_eligible = confidence >= self.threshold and confidence >= self.auto_send_minimum

            # Phase 3: Validate reply quality before auto-send
            try:
                # Score auto-send readiness
                rubric_result = self.scoring_service.score_auto_send_readiness(
                    subject=subject,
                    body=body,
                    reply_text=reply_text,
                    category=category,
                    use_llm=True
                )
                auto_send_rubric_scores = rubric_result

                # Use rubric recommendation if available
                if 'auto_send_recommended' in rubric_result:
                    auto_send_eligible = auto_send_eligible and rubric_result['auto_send_recommended'] is True

                # Validate reply quality (hallucination, policy, tone, completeness)
                if auto_send_eligible:
                    validation_result = self.validation_service.validate_reply_quality(
                        reply_text=reply_text,
                        email_context={'subject': subject, 'body': body},
                        category=category,
                        company_info={},  # TODO: Load from company_info_service
                        use_llm=True
                    )

                    # Block auto-send if validation fails
                    if not validation_result.get('passed', False):
                        auto_send_eligible = False
                        print(f"Validation failed: {validation_result.get('blocking_issues', [])}")

            except Exception as e:
                # Any scoring or validation failure routes to a human.
                auto_send_eligible = False
                logger.error("Auto-send checks failed", {"error_type": type(e).__name__})

            envelope = {"subject": subject or "", "body": body or "", "sender": sender}
            proposal = local_proposal(envelope)
            proposal.update(category=category, confidence=confidence, draft=reply_text)
            release = decide(envelope, proposal, enabled=Config.AUTO_SEND_ENABLED)
            if not isinstance(auto_send_rubric_scores, dict):
                auto_send_rubric_scores = {}
            auto_send_rubric_scores["release_policy"] = release.to_dict()
            auto_send_eligible = (auto_send_eligible and release.route == "auto"
                                  and not pii_check_failed and not pii_detected)
            status = "pending_review"
            if auto_send_eligible:
                reply_text = release.reply
                if graph_service is not None:
                    try:
                        # Do not retry an uncertain external side effect.
                        send_result = graph_service.send_reply(message_id, reply_text, max_attempts=1)
                        retry_count = send_result.get("attempts", 1)
                        sent_at = datetime.now(timezone.utc).isoformat()
                        status = "auto_sent"
                    except Exception as exc:
                        status = "send_unknown"
                        retry_count = 1
                        last_error = type(exc).__name__
                    if status == "auto_sent":
                        try:
                            graph_service.mark_as_read(message_id)
                        except Exception as exc:
                            # Sending succeeded. A read-flag failure must not cause a resend.
                            last_error = "mark_as_read:" + type(exc).__name__



        with get_db_connection() as conn:
            # Extract rubric scores from classification
            classification_rubric_scores = classification.get("rubric_scores")
            import json

            # Upsert email record with rubric scores
            conn.execute(
                """
                INSERT INTO emails (message_id, subject, sender, received_at, body,
                                    category, confidence, reasoning, status, retry_count, last_error,
                                    classification_rubric_scores, auto_send_rubric_scores, rubric_version,
                                    consent_status, pii_detected, pii_types, language, language_confidence)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(message_id) DO UPDATE SET
                    category=excluded.category,
                    confidence=excluded.confidence,
                    reasoning=excluded.reasoning,
                    status=excluded.status,
                    retry_count=excluded.retry_count,
                    last_error=excluded.last_error,
                    classification_rubric_scores=excluded.classification_rubric_scores,
                    auto_send_rubric_scores=excluded.auto_send_rubric_scores,
                    rubric_version=excluded.rubric_version,
                    consent_status=excluded.consent_status,
                    pii_detected=excluded.pii_detected,
                    pii_types=excluded.pii_types,
                    language=excluded.language,
                    language_confidence=excluded.language_confidence
                """,
                (message_id, subject, sender, received_at, body,
                 category, confidence, reasoning, status, retry_count, last_error,
                 json.dumps(classification_rubric_scores) if classification_rubric_scores else None,
                 json.dumps(auto_send_rubric_scores) if auto_send_rubric_scores else None,
                 'v1.0',
                 consent_status,
                 1 if pii_detected else 0,
                 json.dumps(pii_types_found) if pii_types_found else None,
                 language,
                 language_confidence),

            )
            email_row = conn.execute(
                "SELECT id FROM emails WHERE message_id = ?", (message_id,)
            ).fetchone()
            email_id = email_row["id"]

            # Save reply with validation results
            conn.execute(
                """INSERT INTO replies (email_id, reply_text, sent_at, reply_validation_scores,
                                       validation_passed, validation_issues)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (email_id, reply_text, sent_at,
                 json.dumps(validation_result) if validation_result else None,
                 1 if (validation_result and validation_result.get('passed', True)) else 0,
                 json.dumps(validation_result.get('blocking_issues', [])) if validation_result else None),
            )

            # Audit log
            conn.execute(
                "INSERT INTO audit_log (email_id, action, operator) VALUES (?, ?, ?)",
                (email_id, status, operator),
            )
            conn.commit()

        # Phase 5: Compliance audit logging
        log_audit_event(
            action=AuditAction.EMAIL_RECEIVED,
            email_id=email_id,
            operator=operator,
            details={'category': category, 'confidence': confidence, 'pii_detected': pii_detected}
        )
        if pii_detected:
            log_audit_event(
                action=AuditAction.PII_DETECTED,
                email_id=email_id,
                operator=operator,
                details={'pii_types': pii_types_found}
            )
        if status == "auto_sent":
            log_audit_event(
                action=AuditAction.AUTO_SENT,
                email_id=email_id,
                operator=operator,
                details={'confidence': confidence}
            )

        return {
            "id": email_id,
            "message_id": message_id,
            "subject": subject,
            "sender": sender,
            "received_at": received_at,
            "category": category,
            "confidence": confidence,
            "status": status,
            "retry_count": retry_count,
            "last_error": last_error,
            "reply_text": reply_text,

        }

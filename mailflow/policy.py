"""Deterministic release gate. Model scores are signals, never permissions."""
from dataclasses import asdict, dataclass
import hashlib
import json
import math
import re
import unicodedata

VERSION = "mailflow-1.0"
KNOWLEDGE = {
    "tracking-help": {
        "category": "order_tracking", "version": "2026-09-demo-1",
        "title": "如何查看物流进度 / Tracking instructions",
        "aliases": ["如何查看物流", "怎么查询物流", "如何查询物流", "how to track", "how do i track"],
        "zh": "您好，您可以在订单确认邮件中打开物流查询链接，查看承运商更新的进度。如找不到该邮件，请通过原订单渠道联系客服。本回复未查询您的具体订单状态。",
        "en": "Hello, you can open the tracking link in your order confirmation email to see carrier updates. If you cannot find that email, contact support through your original order channel. This reply has not looked up your specific order status.",
    },
    "delivery-help": {
        "category": "shipping_time", "version": "2026-09-demo-1",
        "title": "在哪里查看预计送达时间 / Finding a delivery estimate",
        "aliases": ["哪里查看预计送达", "哪里看预计送达", "where can i find the delivery estimate", "where to find the delivery estimate"],
        "zh": "您好，预计送达时间请以订单页面和承运商最新通知为准。预计时间不代表到货保证；如页面没有显示，请通过原订单渠道联系客服。本回复未查询或承诺具体日期。",
        "en": "Hello, please check your order page and the carrier's latest notice for the delivery estimate. An estimate is not a delivery guarantee. If none is shown, contact support through your original order channel. This reply has not looked up or promised a specific date.",
    },
}
RISK_TERMS = ["退款", "退货", "取消", "赔偿", "投诉", "起诉", "付款", "支付", "发票", "账单", "报价", "价格", "丢失", "损坏", "延误", "保证", "承诺", "验证码", "密码", "银行卡", "身份证",
              "refund", "cancel", "compensat", "complaint", "lawsuit", "payment", "invoice", "billing", "quotation", "price", "lost", "damaged", "delay", "guarantee", "password", "verification code", "credit card"]
INJECTION_TERMS = ["忽略之前", "忽略上述", "忽略规则", "系统提示", "跳过审核", "直接发送", "ignore previous", "ignore all", "system prompt", "bypass review", "override policy", "developer message"]


def normalize(text):
    return " ".join(unicodedata.normalize("NFKC", str(text)).casefold().split())


def knowledge_hash():
    return hashlib.sha256(json.dumps(KNOWLEDGE, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def retrieve(text):
    value = normalize(text)
    return [key for key, item in KNOWLEDGE.items() if any(term in value for term in item["aliases"])]


def language(text):
    return "zh" if re.search(r"[\u4e00-\u9fff]", text) else "en"


@dataclass(frozen=True)
class Decision:
    route: str
    reasons: list
    confidence: float | None
    source_id: str | None
    reply: str
    policy_version: str = VERSION
    knowledge_digest: str = ""

    def to_dict(self):
        return asdict(self)


def decide(email, proposal, threshold=0.90, enabled=True):
    if not isinstance(threshold, (int, float)) or not math.isfinite(threshold) or not 0 <= threshold <= 1:
        raise ValueError("Invalid policy threshold")
    text = normalize(email.get("subject", "") + "\n" + email.get("body", ""))
    reasons = []
    proposal = proposal if isinstance(proposal, dict) else {}
    score = proposal.get("confidence")
    valid_score = type(score) in (int, float) and math.isfinite(score) and 0 <= score <= 1
    if not enabled:
        reasons.append("automatic_processing_disabled")
    if not valid_score:
        reasons.append("invalid_confidence")
    elif score < threshold:
        reasons.append("below_threshold")
    if proposal.get("error"):
        reasons.append("model_or_validation_error")
    if any(term in text for term in RISK_TERMS):
        reasons.append("high_risk_request")
    if any(term in text for term in INJECTION_TERMS):
        reasons.append("instruction_in_email")
    if re.search(r"\b\d{12,19}\b", text):
        reasons.append("possible_sensitive_identifier")
    # Do not answer exact order-status requests with a generic FAQ.
    if any(term in text for term in ["订单号", "我的订单", "我的包裹", "order #", "my order", "my parcel", "tracking number"]):
        reasons.append("account_specific_request")
    if email.get("has_attachments"):
        reasons.append("attachment_needs_review")
    headers = {str(k).lower(): normalize(v) for k, v in email.get("headers", {}).items()}
    if (headers.get("auto-submitted", "no") != "no" or "list-id" in headers
            or headers.get("precedence") in {"bulk", "list", "junk"}
            or "x-auto-response-suppress" in headers):
        reasons.append("automated_message_or_mailing_list")
    sender = email.get("sender", "")
    if not re.fullmatch(r"[^\s<>@]+@[^\s<>@]+\.[^\s<>@]+", sender) or re.search(r"no.?reply|mailer-daemon|postmaster", sender, re.I):
        reasons.append("sender_not_replyable")
    matches = retrieve(text)
    source_id = proposal.get("source_id")
    source = KNOWLEDGE.get(source_id) if isinstance(source_id, str) else None
    if len(matches) != 1 or source_id not in matches:
        reasons.append("missing_or_ambiguous_evidence")
    if source is None or proposal.get("category") != source["category"]:
        reasons.append("intent_evidence_mismatch")
    reply = str(proposal.get("draft", ""))[:12000]
    if source:
        # A free-form LLM draft is never sent automatically. Use reviewed source verbatim.
        reply = source[language(text)]
    if not reply.strip():
        reasons.append("empty_reply")
    return Decision("review" if reasons else "auto", reasons or ["low_risk_verified_faq"],
                    float(score) if valid_score else None, source_id if source else None,
                    reply, knowledge_digest=knowledge_hash())


def local_proposal(email):
    """Deterministic demo selection; score is a rule match, not calibrated probability."""
    matches = retrieve(email.get("subject", "") + "\n" + email.get("body", ""))
    key = matches[0] if len(matches) == 1 else None
    return {"category": KNOWLEDGE[key]["category"] if key else "unknown",
            "confidence": 1.0 if key else 0.0, "source_id": key,
            "draft": "感谢您的来信，您的问题需要客服进一步核实。", "method": "deterministic_demo"}
